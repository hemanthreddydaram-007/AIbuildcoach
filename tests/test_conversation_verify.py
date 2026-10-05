"""Comprehensive test suite for Milestone 11.2: AI-Assisted Claim Verification."""

import io
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from unittest.mock import patch, MagicMock

import pytest

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationClaim,
    EvidenceLink,
    ClaimStatus,
    EvidenceRelation,
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    Project,
    ProjectFile,
    GitState,
    ProjectGraph,
    GraphNode,
    ChangeSet,
    FileChange,
    ChangeType,
    EvidenceRecord,
)
from backend.project_model.db import Database
from backend.ai_gateway.gateway import AIGateway
from backend.ai_gateway.provider import AIProviderAdapter
from backend.ai_gateway.models import RawInteractionResponse, ConsentToken
from backend.ai_gateway.consent import ConsentManager
from backend.ai_gateway.credentials import CredentialStore
from backend.ai_gateway.exceptions import (
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAPIError,
    MalformedModelResponseError,
    ConsentViolationError,
)
from backend.conversation.verification_service import ConversationVerificationService
from backend.conversation.evidence_service import ConversationEvidenceService
from backend.cli.main import main


# ---------------------------------------------------------------------------
# MOCK PROVIDER ADAPTERS FOR ZERO-NETWORK TESTING
# ---------------------------------------------------------------------------

class MockAIProvider(AIProviderAdapter):
    """Configurable mock AI provider for testing Gateway interactions."""

    def __init__(
        self,
        canned_json: Optional[dict] = None,
        exception_to_raise: Optional[Exception] = None,
    ):
        self.canned_json = canned_json or {
            "summary": "Verification completed successfully.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:SUPPORTED | REASON: File auth.py changed as claimed.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth"],
                },
                {
                    "statement": "VERIFICATION_LIMITATION: Verified against latest changeset.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": [],
                },
            ],
            "unresolved_questions": [],
        }
        self.exception_to_raise = exception_to_raise
        self.call_count = 0
        self.last_user_input = ""

    def get_provider_name(self) -> str:
        return "gemini"

    def get_model_name(self) -> str:
        return "gemini-3.8-flash"

    def complete_interaction(
        self,
        system_instruction: str,
        user_input: str,
        response_format: dict,
        api_key: str,
        timeout: float = 30.0,
    ) -> RawInteractionResponse:
        self.call_count += 1
        self.last_user_input = user_input
        if self.exception_to_raise:
            raise self.exception_to_raise

        return RawInteractionResponse(
            status_code=200,
            raw_json=self.canned_json,
            raw_text=json.dumps(self.canned_json),
            latency_ms=85.0,
            tokens_prompt=300,
            tokens_candidate=60,
        )


# ---------------------------------------------------------------------------
# FIXTURE: Realistic project with files, graph, and changeset
# ---------------------------------------------------------------------------

@pytest.fixture
def verification_project(tmp_path: Path):
    """Sets up a complete project with files, graph, changeset, and saved conversation."""
    proj_dir = tmp_path / "verif_proj"
    proj_dir.mkdir()

    auth_file = proj_dir / "auth.py"
    auth_file.write_text("def authenticate(): pass\n", encoding="utf-8")

    db_path = proj_dir / ".buildcoach" / "state.db"
    db = Database(db_path)

    project = Project(
        id="proj_verif_1",
        name="verif_proj",
        root_path=str(proj_dir.resolve()),
    )
    db.upsert_project(project)

    files = [
        ProjectFile(
            path="auth.py",
            absolute_path=str(auth_file),
            file_size=auth_file.stat().st_size,
            last_modified=auth_file.stat().st_mtime,
            sha256_hash="hash_auth",
            file_type="python",
        ),
    ]
    db.sync_files(project.id, files)

    graph = ProjectGraph(project_id=project.id)
    node_auth = GraphNode(id="file:auth.py", project_id=project.id, node_type="FILE", name="auth.py", path="auth.py")
    graph.add_node(node_auth)
    db.save_graph(graph)

    change_set = ChangeSet(
        id="cs_verif_1",
        project_id=project.id,
        git_state=GitState(is_git_repo=True, is_dirty=True, modified_count=1),
        file_changes=[
            FileChange(
                id="fc_auth",
                change_set_id="cs_verif_1",
                new_path="auth.py",
                change_type=ChangeType.MODIFIED,
                is_staged=False,
            ),
        ],
        evidence=[
            EvidenceRecord(
                id="ev_git_auth",
                project_id=project.id,
                change_set_id="cs_verif_1",
                evidence_type="GIT_DIFF",
                source="git diff auth.py",
                file_path="auth.py",
                observation="Modified authentication handler in auth.py",
                confidence="HIGH",
            )
        ],
    )
    db.save_change_set(change_set)

    conv = Conversation(
        conversation_id="conv_verif_1",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_asst_1",
                role=ConversationRole.ASSISTANT,
                content="I updated `auth.py` to fix authentication.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    # Establish M11.1 analysis
    m11_svc = ConversationEvidenceService(db)
    m11_res = m11_svc.analyze_conversation(conv.conversation_id, project.id)
    target_claim = m11_res.claims[0]

    return {
        "proj_dir": proj_dir,
        "db": db,
        "project": project,
        "conversation": conv,
        "claim": target_claim,
        "m11_result": m11_res,
    }


def make_test_gateway(mock_provider: AIProviderAdapter, db: Database) -> AIGateway:
    """Creates an AIGateway configured with the mock provider and explicit dummy credentials."""
    cred_store = CredentialStore()
    cred_store.get_gemini_api_key = lambda explicit_key=None: "mock_gemini_api_key_12345"
    return AIGateway(
        provider_adapter=mock_provider,
        credential_store=cred_store,
        db=db,
    )



# ---------------------------------------------------------------------------
# CORE TESTS: Verification Outcomes & Grounding
# ---------------------------------------------------------------------------

def test_verify_supported_claim(verification_project):
    """Verifies that when AI interprets valid M11.1 evidence, it produces a grounded SUPPORTED verdict."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Claim is supported by git diff and file modification.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:SUPPORTED | REASON: Physical changes in auth.py match assistant claim.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth", "ev_git_auth"],
                },
                {
                    "statement": "VERIFICATION_LIMITATION: Evaluated against active changeset only.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": [],
                },
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    # Pre-grant consent token bound to packet
    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.SUPPORTED
    assert "auth.py" in result.explanation
    assert "fc_auth" in result.grounded_evidence_ids
    assert result.confidence == "HIGH"
    assert mock_provider.call_count == 1


def test_verify_partially_supported_claim(verification_project):
    """Verifies that PARTIALLY_SUPPORTED verdict is correctly preserved and interpreted."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Implementation changed but test coverage unobserved.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:PARTIALLY_SUPPORTED | REASON: auth.py was modified, but no test modifications were observed.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth"],
                },
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.PARTIALLY_SUPPORTED
    assert "auth.py was modified" in result.explanation


def test_verify_unsupported_claim(verification_project):
    """Verifies that conflicting or contradicted claims are tagged UNSUPPORTED."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Claim contradicts project evidence.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:UNSUPPORTED | REASON: Claimed file is missing or was deleted.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth"],
                },
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.UNSUPPORTED


def test_verify_unknown_insufficient_evidence(verification_project):
    """Verifies that when evidence is insufficient, UNKNOWN verdict is produced without false failure."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Evidence is insufficient to confirm feature correctness.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:UNKNOWN | REASON: Cannot determine if bug was resolved from diff alone.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": ["fc_auth"],
                },
            ],
            "unresolved_questions": ["Are edge cases covered?"],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.UNKNOWN


# ---------------------------------------------------------------------------
# ANTI-HALLUCINATION & EVIDENCE VALIDATION
# ---------------------------------------------------------------------------

def test_verify_invalid_or_hallucinated_evidence_reference(verification_project):
    """Verifies that if the AI model cites an invented or ungrounded evidence ID, the verdict is coerced to UNKNOWN."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    hallucinated_id = "hallucinated_evidence_9999"
    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Claim is supported.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:SUPPORTED | REASON: Everything looks great.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": [hallucinated_id],  # Hallucinated ID
                },
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    # Must be coerced to UNKNOWN because the AI hallucinated evidence
    assert result.verdict == VerificationVerdict.UNKNOWN
    assert any("hallucinated" in lim.lower() or "ungrounded" in lim.lower() for lim in result.limitations)
    assert hallucinated_id not in result.grounded_evidence_ids


def test_verify_observation_vs_inference(verification_project):
    """Verifies that OBSERVATION claims require evidence, while INFERENCES are clearly qualified."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Analysis of claim.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:SUPPORTED | REASON: Modification verified.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth"],
                },
                {
                    "statement": "Developer probably intends to deploy this tomorrow.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": [],
                },
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.SUPPORTED
    assert "fc_auth" in result.grounded_evidence_ids


# ---------------------------------------------------------------------------
# PROVIDER FAILURES & DETERMINISTIC FALLBACKS
# ---------------------------------------------------------------------------

def test_verify_provider_timeout_fallback(verification_project):
    """Verifies that provider timeouts return NOT_EVALUATED fallback without losing M11.1 evidence."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(exception_to_raise=ProviderTimeoutError("Connection timed out after 30s"))
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert "timed out" in result.explanation.lower()
    assert result.confidence == "LOW"
    # M11.1 evidence IDs remain preserved in the fallback result
    assert "fc_auth" in result.grounded_evidence_ids


def test_verify_provider_rate_limit_429(verification_project):
    """Verifies that HTTP 429 rate limit produces NOT_EVALUATED fallback."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(exception_to_raise=ProviderRateLimitError("Rate limit exceeded"))
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert "429" in result.explanation or "rate limit" in result.explanation.lower()


def test_verify_provider_server_error_503(verification_project):
    """Verifies that HTTP 503 provider errors return NOT_EVALUATED fallback."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(exception_to_raise=ProviderAPIError("Service Unavailable", status_code=503))
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert "503" in result.explanation


def test_verify_malformed_response_handling(verification_project):
    """Verifies that unparseable model output returns NOT_EVALUATED fallback."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(exception_to_raise=MalformedModelResponseError("Cannot parse JSON"))
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert "malformed" in result.explanation.lower()


# ---------------------------------------------------------------------------
# PRIVACY, CONSENT & SECURITY
# ---------------------------------------------------------------------------

def test_verify_consent_rejection(verification_project):
    """Verifies that calling verification without valid consent immediately aborts and returns NOT_EVALUATED."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider()
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    # Call with consent_token = None
    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=None,
    )

    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert "consent" in result.explanation.lower()
    # Provider must NEVER have been called
    assert mock_provider.call_count == 0


def test_verify_secret_redaction(verification_project):
    """Verifies that secrets present in claims or evidence are redacted before reaching packet and AI Gateway."""
    db = verification_project["db"]
    project = verification_project["project"]

    raw_secret = "sk-ant-api03-123456789012345678901234567890"
    conv_secret = Conversation(
        conversation_id="conv_secret_verify",
        provider="CLAUDE",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_sec",
                role=ConversationRole.ASSISTANT,
                content=f"I configured `auth.py` with secret key {raw_secret}.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv_secret)

    m11_svc = ConversationEvidenceService(db)
    m11_res = m11_svc.analyze_conversation(conv_secret.conversation_id, project.id)
    secret_claim = m11_res.claims[0]

    mock_provider = MockAIProvider()
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv_secret.conversation_id, secret_claim.claim_id, project.id)

    # Verify secret is not in packet
    assert raw_secret not in json.dumps(packet.model_dump())
    assert "[REDACTED]" in json.dumps(packet.model_dump())

    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")
    result = service.verify_claim(
        conversation_id=conv_secret.conversation_id,
        claim_id=secret_claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    assert raw_secret not in result.explanation
    assert raw_secret not in mock_provider.last_user_input


def test_deterministic_packet_construction(verification_project):
    """Verifies that repeated packet construction produces identical IDs, item counts, and SHA-256 hashes derived from conversation."""
    db = verification_project["db"]
    project = verification_project["project"]
    claim = verification_project["claim"]
    conv = verification_project["conversation"]

    service = ConversationVerificationService(db)
    req, pkt1 = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    pkt2 = service.build_verification_packet(req, project.id)

    assert pkt1.id == pkt2.id
    assert len(pkt1.items) == len(pkt2.items)
    from backend.ai_gateway.consent import compute_packet_hash
    assert compute_packet_hash(pkt1) == compute_packet_hash(pkt2)
    assert pkt1.generated_at == conv.created_at
    assert pkt2.generated_at == conv.created_at
    assert "2024-01-01T00:00:00Z" not in pkt1.generated_at
    assert "2024-01-01T00:00:00Z" not in pkt2.generated_at


def test_packet_hash_stability_prepare_and_verify(verification_project):
    """Regression test: Proves packet hash is stable across prepare + verify using conversation.created_at."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    mock_provider = MockAIProvider(
        canned_json={
            "summary": "Verified.",
            "claims": [
                {
                    "statement": "VERIFICATION_VERDICT:SUPPORTED | REASON: Changes in auth.py confirmed.",
                    "claim_type": "OBSERVATION",
                    "evidence_refs": ["fc_auth"],
                }
            ],
            "unresolved_questions": [],
        }
    )
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    # 1. Prepare verification generates packet with conversation.created_at
    _, prepared_pkt = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    assert prepared_pkt.generated_at == conv.created_at
    assert "2024-01-01" not in prepared_pkt.generated_at

    # 2. Grant consent with prepared packet
    token = ConsentManager.grant_consent(prepared_pkt, provider="gemini", model="gemini-3.8-flash")
    from backend.ai_gateway.consent import compute_packet_hash
    prepared_hash = compute_packet_hash(prepared_pkt)
    assert token.packet_hash == prepared_hash

    # 3. Verify claim regenerates packet internally; hash must match token exactly without ConsentViolationError
    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )
    assert result.verdict == VerificationVerdict.SUPPORTED


def test_provider_error_metadata_safety_and_no_leakage(verification_project, caplog):
    """Regression test: Proves raw provider exception bodies and sensitive tokens never leak into metadata, JSON, or logs."""
    db = verification_project["db"]
    project = verification_project["project"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    sensitive_token = "sk-live-LEAKED_PROVIDER_TOKEN_999888"
    sensitive_body = f"Provider internal dump: authorization failure for key {sensitive_token} with prompt payload."

    # Simulate provider raising error with sensitive body
    mock_provider = MockAIProvider(exception_to_raise=ProviderAPIError(sensitive_body, status_code=500))
    gateway = make_test_gateway(mock_provider, db)
    service = ConversationVerificationService(db, gateway=gateway)

    _, packet = service.prepare_verification(conv.conversation_id, claim.claim_id, project.id)
    token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    # 1. Verification Service Execution
    result = service.verify_claim(
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        consent_token=token,
    )

    # Assert structured safe metadata
    assert result.verdict == VerificationVerdict.NOT_EVALUATED
    assert result.provider_metadata["error"] == "PROVIDER_ERROR"
    assert result.provider_metadata["status_code"] == 500

    # Ensure sensitive string never appears in provider_metadata, explanation, or result dump
    assert sensitive_token not in json.dumps(result.provider_metadata)
    assert sensitive_token not in result.explanation
    assert sensitive_token not in json.dumps(result.model_dump())
    assert sensitive_body not in json.dumps(result.model_dump())

    # 2. CLI JSON Execution
    from backend.cli.runner import run_conversation_verify
    cli_res = run_conversation_verify(
        db=db,
        conversation_id=conv.conversation_id,
        claim_id=claim.claim_id,
        project_id=project.id,
        has_consent=True,
        gateway=gateway,
    )
    cli_json = json.dumps(cli_res)
    assert sensitive_token not in cli_json
    assert sensitive_body not in cli_json

    # 3. Logs check
    for record in caplog.records:
        assert sensitive_token not in record.message
        assert sensitive_body not in record.message


# ---------------------------------------------------------------------------
# CLI VERIFY TESTS
# ---------------------------------------------------------------------------

def test_cli_conversation_verify_json_success(verification_project):
    """Verifies that 'ai-build-coach conversation verify --json' produces clean JSON on stdout."""
    db = verification_project["db"]
    project = verification_project["project"]
    proj_dir = verification_project["proj_dir"]
    conv = verification_project["conversation"]
    claim = verification_project["claim"]

    # Patch AIGateway inside runner to use mock provider
    mock_provider = MockAIProvider()
    gateway = make_test_gateway(mock_provider, db)

    buf = io.StringIO()
    with patch("sys.stdout", buf), patch("backend.cli.main.run_conversation_verify") as mock_run:
        mock_run.return_value = {
            "status": "success",
            "verification": {
                "verification_id": "vr_test_123",
                "claim_id": claim.claim_id,
                "verdict": "SUPPORTED",
                "explanation": "Verified against M11.1 evidence.",
                "grounded_evidence_ids": ["fc_auth"],
                "confidence": "HIGH",
                "limitations": [],
                "provider_metadata": {},
                "verified_at": "2024-01-01T00:00:00Z",
            },
        }
        rc = main([
            "--project-root", str(proj_dir),
            "conversation", "verify",
            "--conversation-id", conv.conversation_id,
            "--claim-id", claim.claim_id,
            "--project-id", project.id,
            "--consent",
            "--json",
        ])

    assert rc == 0
    raw = buf.getvalue().strip()
    data = json.loads(raw)
    assert data["status"] == "success"
    assert data["command"] == "conversation"
    assert data["action"] == "verify"
    assert data["data"]["verification"]["verdict"] == "SUPPORTED"


def test_cli_conversation_verify_missing_args(verification_project):
    """Verifies that missing conversation or claim returns clean JSON error envelope with non-zero exit code."""
    proj_dir = verification_project["proj_dir"]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "conversation", "verify",
            "--conversation-id", "nonexistent_conv",
            "--claim-id", "claim_123",
            "--json",
        ])

    assert rc != 0
    raw = buf.getvalue().strip()
    data = json.loads(raw)
    assert data["status"] == "error"
    assert data["error"]["code"] == "CONVERSATION_VERIFY_ERROR"
    assert "Conversation not found" in data["error"]["message"]

