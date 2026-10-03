"""Comprehensive tests for Milestone 5 - AI Gateway."""

import io
import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.domain.models import ContextPacket, ContextItem, ContextPurpose, ContextSourceType
from backend.ai_gateway.models import (
    ClaimType,
    StructuredClaim,
    ExplanationResponse,
    ConsentToken,
    RawInteractionResponse,
    ValidatedGatewayResult,
)
from backend.ai_gateway.exceptions import (
    ConsentViolationError,
    CredentialMissingError,
    SecurityConfigurationError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAPIError,
    MalformedModelResponseError,
)
from backend.ai_gateway.consent import ConsentManager, compute_packet_hash
from backend.ai_gateway.credentials import CredentialStore
from backend.ai_gateway.gemini import (
    GeminiInteractionsAdapter,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_ENDPOINT,
)
from backend.ai_gateway.validator import EvidenceValidator
from backend.ai_gateway.gateway import AIGateway
from backend.project_model.db import Database


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_packet() -> ContextPacket:
    item1 = ContextItem(
        item_id="ci_1001",
        source_type=ContextSourceType.FILE,
        source_reference="src/auth.py",
        file_path="src/auth.py",
        line_start=10,
        line_end=25,
        evidence_refs=["ev_git_diff_1"],
        relevance_reason="Directly modified authentication logic",
        relevance_score=95.0,
        redacted=False,
        content="def verify_token(token: str) -> bool:\n    return token.startswith('bearer_')",
    )
    item2 = ContextItem(
        item_id="ci_1002",
        source_type=ContextSourceType.PROJECT_GRAPH,
        source_reference="src/app.py",
        file_path="src/app.py",
        line_start=1,
        line_end=15,
        evidence_refs=["ev_graph_edge_1"],
        relevance_reason="Imports auth module",
        relevance_score=75.0,
        redacted=False,
        content="from src.auth import verify_token\n\ndef main():\n    pass",
    )
    return ContextPacket(
        id="pkt_test_12345",
        project_id="proj_alpha",
        purpose=ContextPurpose.CHANGE_EXPLANATION,
        generated_at="2026-10-03T10:00:00Z",
        packet_version="1.0.0",
        items=[item1, item2],
        evidence_refs=["ev_git_diff_1", "ev_graph_edge_1"],
        redaction_summary={"redacted_count": 0, "secret_types_detected": []},
        token_estimate=120,
        truncation_status="NONE",
    )


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_gateway.db"
    return Database(db_file)


# ============================================================================
# 1. CONSENT TESTS
# ============================================================================

def test_packet_hash_deterministic(sample_packet: ContextPacket):
    hash1 = compute_packet_hash(sample_packet)
    hash2 = compute_packet_hash(sample_packet)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256


def test_packet_hash_changes_on_tampering(sample_packet: ContextPacket):
    original_hash = compute_packet_hash(sample_packet)
    tampered_packet = sample_packet.model_copy(deep=True)
    tampered_packet.items[0].content = "modified content"
    assert compute_packet_hash(tampered_packet) != original_hash


def test_consent_manager_create_preview(sample_packet: ContextPacket):
    preview = ConsentManager.create_preview(sample_packet, provider="gemini", model="gemini-3.8-flash")
    assert preview.packet_id == sample_packet.id
    assert preview.provider == "gemini"
    assert preview.model == "gemini-3.8-flash"
    assert preview.token_estimate == 120
    assert preview.files_included == ["src/app.py", "src/auth.py"]
    assert preview.item_count == 2
    assert preview.packet_hash == compute_packet_hash(sample_packet)


def test_consent_manager_grant_and_validate(sample_packet: ContextPacket):
    token = ConsentManager.grant_consent(
        sample_packet,
        provider="gemini",
        model="gemini-3.8-flash",
        duration_minutes=15,
    )
    assert token.packet_id == sample_packet.id
    assert token.user_acknowledged is True
    assert token.provider == "gemini"
    assert token.model == "gemini-3.8-flash"

    # Should validate cleanly
    ConsentManager.validate_consent(sample_packet, token, provider="gemini", model="gemini-3.8-flash")


def test_consent_manager_rejections(sample_packet: ContextPacket):
    token = ConsentManager.grant_consent(sample_packet, duration_minutes=15)

    # 1. User unacknowledged
    unack_token = token.model_copy(update={"user_acknowledged": False})
    with pytest.raises(ConsentViolationError, match="user has not explicitly acknowledged"):
        ConsentManager.validate_consent(sample_packet, unack_token)

    # 2. Packet ID mismatch
    bad_id_token = token.model_copy(update={"packet_id": "pkt_different"})
    with pytest.raises(ConsentViolationError, match="packet ID mismatch"):
        ConsentManager.validate_consent(sample_packet, bad_id_token)

    # 3. Hash mismatch (tampered packet)
    tampered_packet = sample_packet.model_copy(deep=True)
    tampered_packet.token_estimate = 9999
    with pytest.raises(ConsentViolationError, match="packet hash mismatch"):
        ConsentManager.validate_consent(tampered_packet, token)

    # 4. Provider mismatch
    with pytest.raises(ConsentViolationError, match="provider mismatch"):
        ConsentManager.validate_consent(sample_packet, token, provider="openai")

    # 5. Model mismatch
    with pytest.raises(ConsentViolationError, match="model mismatch"):
        ConsentManager.validate_consent(sample_packet, token, model="gemini-1.5-pro")

    # 6. Expired token
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    expired_token = token.model_copy(update={"expires_at": expired_time})
    with pytest.raises(ConsentViolationError, match="Consent token expired"):
        ConsentManager.validate_consent(sample_packet, expired_token)


# ============================================================================
# 2. CREDENTIAL STORE TESTS
# ============================================================================

def test_credential_store_explicit_key():
    store = CredentialStore()
    key = store.get_gemini_api_key(explicit_key="AIzaSyTestKey12345")
    assert key == "AIzaSyTestKey12345"


def test_credential_store_env_var(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env_secret_key_8888")
    store = CredentialStore()
    key = store.get_gemini_api_key()
    assert key == "env_secret_key_8888"


def test_credential_store_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BUILDCOACH_GEMINI_API_KEY", raising=False)
    store = CredentialStore()
    with pytest.raises(CredentialMissingError, match="GEMINI_API_KEY"):
        store.get_gemini_api_key()


def test_credential_store_rejects_key_in_config(tmp_path: Path):
    bad_config = tmp_path / "config.json"
    bad_config.write_text(json.dumps({"gemini_api_key": "insecure_on_disk_key"}), encoding="utf-8")

    store = CredentialStore(config_path=bad_config)
    with pytest.raises(SecurityConfigurationError, match="Sensitive API keys must NEVER be stored"):
        store.get_gemini_api_key(explicit_key="some_key")


def test_credential_store_mask_key():
    assert CredentialStore.mask_key(None) == "<none>"
    assert CredentialStore.mask_key("short") == "***"
    assert CredentialStore.mask_key("AIzaSy1234567890abcdef") == "AIza...cdef"


# ============================================================================
# 3. EVIDENCE VALIDATOR TESTS
# ============================================================================

def test_evidence_validator_valid_claims(sample_packet: ContextPacket):
    explanation = ExplanationResponse(
        summary="Updated authentication logic in src/auth.py",
        claims=[
            StructuredClaim(
                statement="Token verification now checks for bearer_ prefix",
                claim_type=ClaimType.OBSERVATION,
                evidence_refs=["ci_1001"],
                file_path="src/auth.py",
            ),
            StructuredClaim(
                statement="Future callers should wrap tokens with bearer_ prefix",
                claim_type=ClaimType.RECOMMENDATION,
                evidence_refs=["ci_1001", "ci_1002"],
            ),
        ],
        unresolved_questions=[],
    )

    validated, metrics = EvidenceValidator.validate_explanation(explanation, sample_packet)
    assert metrics["total_claims"] == 2
    assert metrics["grounded_claims"] == 2
    assert metrics["grounding_ratio"] == 1.0
    assert validated.claims[0].grounded is True
    assert validated.claims[0].claim_type == ClaimType.OBSERVATION
    assert validated.claims[1].grounded is True


def test_evidence_validator_invalid_refs_coerced_to_unknown(sample_packet: ContextPacket):
    explanation = ExplanationResponse(
        summary="Some claim with hallucinated item ID",
        claims=[
            StructuredClaim(
                statement="This claim cites an imaginary context item",
                claim_type=ClaimType.OBSERVATION,
                evidence_refs=["ci_9999_hallucinated"],
                file_path="src/auth.py",
            )
        ],
    )

    validated, metrics = EvidenceValidator.validate_explanation(explanation, sample_packet)
    assert metrics["grounded_claims"] == 0
    assert metrics["unknown_claims"] == 1
    # Must NOT silently change to INFERENCE! Must be UNKNOWN.
    assert validated.claims[0].claim_type == ClaimType.UNKNOWN
    assert validated.claims[0].grounded is False
    assert "Cited unknown evidence references" in validated.claims[0].validation_notes


def test_evidence_validator_uninspected_file_path(sample_packet: ContextPacket):
    explanation = ExplanationResponse(
        summary="Claim referencing hallucinated file",
        claims=[
            StructuredClaim(
                statement="Claims something about database file",
                claim_type=ClaimType.OBSERVATION,
                evidence_refs=["ci_1001"],
                file_path="src/non_existent_database.py",
            )
        ],
    )

    validated, metrics = EvidenceValidator.validate_explanation(explanation, sample_packet)
    assert validated.claims[0].grounded is False
    assert validated.claims[0].claim_type == ClaimType.UNKNOWN
    assert "Referenced uninspected file path" in validated.claims[0].validation_notes


def test_evidence_validator_no_evidence_refs(sample_packet: ContextPacket):
    explanation = ExplanationResponse(
        summary="Claim with no evidence refs",
        claims=[
            StructuredClaim(
                statement="Observation without citations",
                claim_type=ClaimType.OBSERVATION,
                evidence_refs=[],
            )
        ],
    )

    validated, metrics = EvidenceValidator.validate_explanation(explanation, sample_packet)
    assert validated.claims[0].grounded is False
    assert validated.claims[0].claim_type == ClaimType.UNKNOWN


# ============================================================================
# 4. GEMINI INTERACTIONS ADAPTER TESTS (100% OFFLINE MOCKED)
# ============================================================================

def test_gemini_adapter_payload_shape():
    adapter = GeminiInteractionsAdapter(model_name="gemini-3.8-flash")
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    payload = adapter._build_request_payload(
        system_instruction="System prompt",
        user_input="User input",
        response_format={"type": "text", "mime_type": "application/json", "schema": schema},
    )

    # Verify Gemini Interactions API exact shape
    assert payload["model"] == "gemini-3.8-flash"
    assert payload["system_instruction"] == "System prompt"
    assert isinstance(payload["system_instruction"], str)
    assert payload["input"] == "User input"
    assert isinstance(payload["input"], str)
    assert payload["response_format"]["type"] == "text"
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["response_format"]["schema"] == schema
    assert payload["generation_config"] == {"thinking_level": "low"}
    assert payload["store"] is False

    # Forbidden fields MUST NOT be present
    assert "contents" not in payload
    assert "previous_interaction_id" not in payload
    assert "temperature" not in payload.get("generation_config", {})


def test_gemini_adapter_successful_call():
    adapter = GeminiInteractionsAdapter()

    fake_response_data = {
        "output": {
            "summary": "Sample summary",
            "claims": [
                {
                    "statement": "Valid statement",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["ci_1001"],
                }
            ],
            "unresolved_questions": [],
        },
        "usage_metadata": {
            "prompt_token_count": 150,
            "candidates_token_count": 45,
        },
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(fake_response_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = adapter.complete_interaction(
            system_instruction="sys",
            user_input="inp",
            response_format={"type": "text", "mime_type": "application/json", "schema": {}},
            api_key="test_key_123",
        )

        assert result.status_code == 200
        assert result.tokens_prompt == 150
        assert result.tokens_candidate == 45
        assert result.raw_json == fake_response_data

        # Verify request sent to urlopen
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == DEFAULT_GEMINI_ENDPOINT
        assert called_req.headers["X-goog-api-key"] == "test_key_123"
        assert called_req.headers["Content-type"] == "application/json"


def test_gemini_adapter_timeout_not_retried():
    """Ambiguous network timeouts on POST MUST NOT be automatically retried."""
    adapter = GeminiInteractionsAdapter(max_retries=2)

    with patch("urllib.request.urlopen", side_effect=socket.timeout("Socket timed out")) as mock_urlopen:
        with pytest.raises(ProviderTimeoutError, match="timed out"):
            adapter.complete_interaction(
                system_instruction="sys",
                user_input="inp",
                response_format={},
                api_key="test_key",
                timeout=5.0,
            )
        # Should be attempted exactly ONCE (zero retries on timeout)
        assert mock_urlopen.call_count == 1


def test_gemini_adapter_retry_on_429():
    """Transient rate limit (HTTP 429) should be retried up to max_retries."""
    adapter = GeminiInteractionsAdapter(max_retries=2)

    # 2 failures then 1 success
    err_429 = urllib.error.HTTPError(
        url=DEFAULT_GEMINI_ENDPOINT,
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error": "rate limit"}'),
    )

    success_data = {
        "output": {"summary": "Retried successfully", "claims": []},
        "usage": {"prompt_token_count": 10, "candidates_token_count": 10},
    }
    mock_success = MagicMock()
    mock_success.status = 200
    mock_success.read.return_value = json.dumps(success_data).encode("utf-8")
    mock_success.__enter__.return_value = mock_success

    with patch("urllib.request.urlopen", side_effect=[err_429, mock_success]) as mock_urlopen:
        with patch("time.sleep", return_value=None):
            result = adapter.complete_interaction(
                system_instruction="sys",
                user_input="inp",
                response_format={},
                api_key="test_key",
            )
            assert result.status_code == 200
            assert mock_urlopen.call_count == 2


def test_gemini_adapter_http_401_no_retry():
    """Fatal auth error (HTTP 401) must NOT be retried."""
    adapter = GeminiInteractionsAdapter(max_retries=2)
    err_401 = urllib.error.HTTPError(
        url=DEFAULT_GEMINI_ENDPOINT,
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"error": "invalid api key"}'),
    )

    with patch("urllib.request.urlopen", side_effect=err_401) as mock_urlopen:
        with pytest.raises(ProviderAPIError, match="HTTP 401 error"):
            adapter.complete_interaction(
                system_instruction="sys",
                user_input="inp",
                response_format={},
                api_key="bad_key",
            )
        assert mock_urlopen.call_count == 1


# ============================================================================
# 5. AI GATEWAY ORCHESTRATOR TESTS
# ============================================================================

def test_ai_gateway_full_pipeline(sample_packet: ContextPacket, temp_db: Database):
    """End-to-end integration of AIGateway with mocked adapter."""
    mock_adapter = MagicMock()
    mock_adapter.get_provider_name.return_value = "gemini"
    mock_adapter.get_model_name.return_value = "gemini-3.8-flash"

    model_response = {
        "summary": "Updated token validation logic in auth module",
        "claims": [
            {
                "statement": "verify_token checks bearer_ prefix",
                "claim_type": "OBSERVATION",
                "evidence_refs": ["ci_1001"],
                "file_path": "src/auth.py",
                "confidence": "HIGH",
            },
            {
                "statement": "Unfounded hallucination about database migration",
                "claim_type": "OBSERVATION",
                "evidence_refs": ["ci_9999"],  # Invalid!
                "confidence": "HIGH",
            },
        ],
        "unresolved_questions": ["Is backwards compatibility needed for legacy tokens?"],
    }

    mock_adapter.complete_interaction.return_value = RawInteractionResponse(
        status_code=200,
        raw_json={"output": model_response},
        raw_text=json.dumps({"output": model_response}),
        latency_ms=124.5,
        tokens_prompt=210,
        tokens_candidate=65,
    )

    gateway = AIGateway(
        provider_adapter=mock_adapter,
        db=temp_db,
    )

    token = ConsentManager.grant_consent(sample_packet, provider="gemini", model="gemini-3.8-flash")

    result = gateway.generate_explanation(
        packet=sample_packet,
        consent_token=token,
        objective="Explain the auth changes",
        explicit_api_key="test_api_key",
    )

    assert isinstance(result, ValidatedGatewayResult)
    assert result.packet_id == sample_packet.id
    assert result.provider == "gemini"
    assert result.model == "gemini-3.8-flash"
    assert result.tokens_prompt == 210
    assert result.tokens_candidate == 65
    assert result.latency_ms == 124.5

    # Check claims & grounding
    assert len(result.claims) == 2
    # First claim is grounded
    assert result.claims[0].grounded is True
    assert result.claims[0].claim_type == ClaimType.OBSERVATION
    # Second claim has invalid ref -> grounded=False, coerced to UNKNOWN
    assert result.claims[1].grounded is False
    assert result.claims[1].claim_type == ClaimType.UNKNOWN

    # Check that prompt fencer created the prompt with anti-injection and context items
    prompt_call = mock_adapter.complete_interaction.call_args[1]
    system_instruction = prompt_call["system_instruction"]
    user_input = prompt_call["user_input"]

    assert "CRITICAL SECURITY AND ANTI-INJECTION RULES" in system_instruction
    assert "<untrusted_project_evidence>" in user_input
    assert '<context_item id="ci_1001"' in user_input
    assert '<context_item id="ci_1002"' in user_input
    assert "</untrusted_project_evidence>" in user_input

    # Check audit record in SQLite
    audit_row = temp_db.get_gateway_run_by_id(result.id)
    assert audit_row is not None
    assert audit_row["packet_id"] == sample_packet.id
    assert audit_row["provider"] == "gemini"
    assert audit_row["model"] == "gemini-3.8-flash"
    assert audit_row["tokens_prompt"] == 210
    assert audit_row["tokens_candidate"] == 65
    assert audit_row["claims_count"] == 2
    assert audit_row["grounded_count"] == 1
    assert audit_row["unknown_count"] == 1


def test_ai_gateway_consent_violation_stops_execution(sample_packet: ContextPacket):
    mock_adapter = MagicMock()
    mock_adapter.get_provider_name.return_value = "gemini"
    mock_adapter.get_model_name.return_value = "gemini-3.8-flash"

    gateway = AIGateway(provider_adapter=mock_adapter)
    unauthorized_token = ConsentToken(
        token_id="fake_tok",
        packet_id="different_packet",
        packet_hash="bad_hash",
        provider="gemini",
        model="gemini-3.8-flash",
        expires_at="2099-01-01T00:00:00Z",
        user_acknowledged=True,
    )

    with pytest.raises(ConsentViolationError):
        gateway.generate_explanation(
            packet=sample_packet,
            consent_token=unauthorized_token,
            explicit_api_key="some_key",
        )

    # Provider must NOT have been called
    mock_adapter.complete_interaction.assert_not_called()


def test_ai_gateway_missing_credential_stops_execution(sample_packet: ContextPacket, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BUILDCOACH_GEMINI_API_KEY", raising=False)

    mock_adapter = MagicMock()
    mock_adapter.get_provider_name.return_value = "gemini"
    mock_adapter.get_model_name.return_value = "gemini-3.8-flash"

    gateway = AIGateway(provider_adapter=mock_adapter)
    token = ConsentManager.grant_consent(sample_packet)

    with pytest.raises(CredentialMissingError):
        gateway.generate_explanation(
            packet=sample_packet,
            consent_token=token,
            explicit_api_key=None,
        )

    mock_adapter.complete_interaction.assert_not_called()


def test_ai_gateway_malformed_model_response(sample_packet: ContextPacket):
    mock_adapter = MagicMock()
    mock_adapter.get_provider_name.return_value = "gemini"
    mock_adapter.get_model_name.return_value = "gemini-3.8-flash"
    mock_adapter.complete_interaction.return_value = RawInteractionResponse(
        status_code=200,
        raw_json={"output": "Not a json object at all"},
        raw_text="Not a json object at all",
        latency_ms=10.0,
    )

    gateway = AIGateway(provider_adapter=mock_adapter)
    token = ConsentManager.grant_consent(sample_packet)

    with pytest.raises(MalformedModelResponseError):
        gateway.generate_explanation(
            packet=sample_packet,
            consent_token=token,
            explicit_api_key="valid_key",
        )
