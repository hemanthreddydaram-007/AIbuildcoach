"""Comprehensive tests for Milestone 6: Understand What Changed workflow."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.domain.models import (
    ContextPacket,
    ContextItem,
    ContextPurpose,
    ContextSourceType,
    ChangeSet,
    FileChange,
    DiffHunk,
    EvidenceRecord,
    ChangeType,
    GitState,
)
from backend.ai_gateway.models import (
    ClaimType,
    StructuredClaim,
    ConsentToken,
    ValidatedGatewayResult,
)
from backend.ai_gateway.consent import ConsentManager
from backend.ai_gateway.exceptions import (
    ConsentViolationError,
    ProviderAPIError,
    ProviderTimeoutError,
)
from backend.project_model.db import Database
from backend.explanation.models import (
    IntentEpistemicStatus,
    ChangeCategory,
    UnderstandChangeResult,
    ChangeExplanationPreview,
)
from backend.explanation.categorizer import DeterministicCategorizer
from backend.explanation.synthesizer import (
    prepare_change_explanation,
    explain_changes,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_m6.db"
    return Database(db_file)


@pytest.fixture
def sample_m3_changeset() -> ChangeSet:
    git_state = GitState(
        is_git_repo=True,
        current_branch="main",
        head_commit="abc1234",
        is_dirty=True,
        untracked_count=0,
        modified_count=2,
        staged_count=0,
    )

    fc1 = FileChange(
        id="fc_001",
        change_set_id="cs_test_100",
        project_id="proj_test",
        old_path="src/auth.py",
        new_path="src/auth.py",
        change_type=ChangeType.MODIFIED,
        is_staged=False,
        is_untracked=False,
        old_line_count=5,
        new_line_count=18,
        line_ranges=[(10, 27)],
    )

    fc2 = FileChange(
        id="fc_002",
        change_set_id="cs_test_100",
        project_id="proj_test",
        old_path="tests/test_auth.py",
        new_path="tests/test_auth.py",
        change_type=ChangeType.MODIFIED,
        is_staged=False,
        is_untracked=False,
        old_line_count=2,
        new_line_count=8,
        line_ranges=[(1, 8)],
    )

    hunk1 = DiffHunk(
        id="hk_001",
        file_change_id="fc_001",
        change_set_id="cs_test_100",
        old_start=10,
        old_lines=5,
        new_start=10,
        new_lines=18,
        header="@@ -10,5 +10,18 @@",
        content="""+ # Why: Require standard RFC 6750 bearer tokens for API security
+ def verify_token(token: str) -> bool:
+     return token.startswith('bearer_')""",
    )

    ev1 = EvidenceRecord(
        id="ev_001",
        project_id="proj_test",
        change_set_id="cs_test_100",
        evidence_type="GIT_DIFF",
        source="working_tree",
        file_path="src/auth.py",
        observation="Modified verify_token function in src/auth.py",
    )

    return ChangeSet(
        id="cs_test_100",
        project_id="proj_test",
        git_state=git_state,
        file_changes=[fc1, fc2],
        evidence=[ev1],
        summary={"total_files": 2, "lines_added": 26, "lines_removed": 7},
    )


@pytest.fixture
def sample_m4_packet() -> ContextPacket:
    item1 = ContextItem(
        item_id="ci_1001",
        source_type=ContextSourceType.FILE,
        source_reference="src/auth.py",
        file_path="src/auth.py",
        line_start=10,
        line_end=27,
        evidence_refs=["ev_001"],
        relevance_reason="Directly modified authentication logic",
        relevance_score=95.0,  # Critical >= 90.0
        redacted=False,
        content="""# Why: Require standard RFC 6750 bearer tokens for API security
def verify_token(token: str) -> bool:
    if not token.startswith('bearer_'):
        return False
    return True""",
    )

    item2 = ContextItem(
        item_id="ci_1002",
        source_type=ContextSourceType.FILE,
        file_path="tests/test_auth.py",
        source_reference="tests/test_auth.py",
        line_start=1,
        line_end=8,
        evidence_refs=[],
        relevance_reason="Test coverage for auth changes",
        relevance_score=60.0,  # Non-critical < 90.0
        redacted=False,
        content="""def test_verify_token():
    assert verify_token('bearer_valid') == True
    assert verify_token('invalid') == False""",
    )

    return ContextPacket(
        id="pkt_test_200",
        project_id="proj_test",
        purpose=ContextPurpose.CHANGE_EXPLANATION,
        generated_at="2026-10-03T12:00:00Z",
        packet_version="1.0.0",
        items=[item1, item2],
        evidence_refs=["ev_001"],
        redaction_summary={"redacted_count": 0, "secret_types_detected": []},
        token_estimate=150,
        truncation_status="NONE",
    )


@pytest.fixture
def sample_gateway_result() -> ValidatedGatewayResult:
    claims = [
        StructuredClaim(
            statement="verify_token checks that the input token has a bearer_ prefix",
            claim_type=ClaimType.OBSERVATION,
            evidence_refs=["ci_1001"],
            file_path="src/auth.py",
            confidence="HIGH",
            grounded=True,
            validation_notes="Grounded in valid ContextItem evidence.",
        ),
        StructuredClaim(
            statement="The system appears to be adopting standard OAuth2 bearer token conventions",
            claim_type=ClaimType.INFERENCE,
            evidence_refs=["ci_1001"],
            file_path="src/auth.py",
            confidence="MEDIUM",
            grounded=True,
            validation_notes="Grounded in valid ContextItem evidence.",
        ),
    ]

    return ValidatedGatewayResult(
        id="gw_test_300",
        packet_id="pkt_test_200",
        provider="gemini",
        model="gemini-3.8-flash",
        summary="Updated token verification to require standard bearer tokens and added accompanying test coverage.",
        claims=claims,
        unresolved_questions=["Does the legacy client pass bearer prefixes?"],
        tokens_prompt=250,
        tokens_candidate=80,
        latency_ms=115.0,
        validation_summary={"total_claims": 2, "grounded_claims": 2},
    )


# ============================================================================
# 1. DETERMINISTIC CATEGORIZATION TESTS
# ============================================================================

def test_deterministic_categorizer():
    assert DeterministicCategorizer.classify_file("src/auth/jwt.py") == ChangeCategory.SECURITY_AUTH
    assert DeterministicCategorizer.classify_file("tests/test_api.py") == ChangeCategory.TESTING
    assert DeterministicCategorizer.classify_file("routes/users.py") == ChangeCategory.API_CONTRACT
    assert DeterministicCategorizer.classify_file("models/user.py") == ChangeCategory.DATA_STORAGE
    assert DeterministicCategorizer.classify_file("docker-compose.yml") == ChangeCategory.CONFIG_INFRA
    assert DeterministicCategorizer.classify_file("docs/index.md") == ChangeCategory.DOCUMENTATION
    assert DeterministicCategorizer.classify_file("backend/service.py") == ChangeCategory.BUSINESS_LOGIC
    assert DeterministicCategorizer.classify_file("unknown.xyz") == ChangeCategory.UNKNOWN


# ============================================================================
# 2. PREPARATION & PREVIEW TESTS
# ============================================================================

def test_prepare_change_explanation_clean_tree(tmp_path: Path, temp_db: Database):
    """Clean tree should immediately return a clean preview with zero token cost."""
    # Create empty repo structure without git repo
    preview, packet, changeset = prepare_change_explanation("proj_clean", tmp_path, temp_db)

    assert isinstance(preview, ChangeExplanationPreview)
    assert preview.clean_working_tree is True
    assert preview.total_files_changed == 0
    assert preview.token_estimate == 0
    assert packet is None
    assert changeset is not None


# ============================================================================
# 3. PHYSICAL TRUTH IMMUTABILITY TESTS
# ============================================================================

def test_what_changed_physical_truth_immutability(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """The changed files, counts, and types MUST come directly from M3, never from AI."""
    mock_gateway = MagicMock()
    # Model claims a hallucinated summary mentioning a file that never changed
    sample_gateway_result.summary = "I also modified src/non_existent.py and deleted 50 files."
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    assert isinstance(result, UnderstandChangeResult)
    assert result.clean_working_tree is False

    # Physical truth check: exactly the 2 M3 files
    assert result.what_changed.total_files_changed == 2
    assert len(result.what_changed.files) == 2
    assert result.what_changed.files[0].file_path == "src/auth.py"
    assert result.what_changed.files[0].change_type == "MODIFIED"
    assert result.what_changed.files[0].lines_added == 18
    assert result.what_changed.files[0].lines_removed == 5
    assert result.what_changed.files[1].file_path == "tests/test_auth.py"

    # Line counts sum directly from M3
    assert result.what_changed.total_lines_added == 26
    assert result.what_changed.total_lines_removed == 7

    # Supplementary AI narrative is labeled as supplementary text and did NOT alter files
    assert result.what_changed.supplementary_ai_narrative == sample_gateway_result.summary
    assert "src/non_existent.py" not in [f.file_path for f in result.what_changed.files]


# ============================================================================
# 4. FILE CRITICALITY SPECIFICATION TEST
# ============================================================================

def test_file_criticality_logic(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """File is critical when ANY ContextItem for that file has relevance_score >= 90.0."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    # src/auth.py has item1 with relevance 95.0 -> is_critical = True
    assert result.what_changed.files[0].file_path == "src/auth.py"
    assert result.what_changed.files[0].is_critical is True

    # tests/test_auth.py has item2 with relevance 60.0 -> is_critical = False
    assert result.what_changed.files[1].file_path == "tests/test_auth.py"
    assert result.what_changed.files[1].is_critical is False


# ============================================================================
# 5. EPISTEMIC CLASSIFICATION IN WHY SECTION TESTS
# ============================================================================

def test_why_section_explicit_classification(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """When an actual explanatory statement exists in comments, status is EXPLICIT."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    # In sample_m4_packet item1 content: "# Why: Require standard RFC 6750 bearer tokens for API security"
    assert result.why.primary_intent.status == IntentEpistemicStatus.EXPLICIT
    assert "RFC 6750 bearer tokens" in result.why.primary_intent.statement
    assert "comment_in:src/auth.py" in str(result.why.primary_intent.evidence_source)


def test_why_section_inferred_when_unannotated(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """When code is unannotated and commit has no rationale, intent must be INFERRED."""
    # Strip explicit comment from packet
    sample_m4_packet.items[0].content = "def verify_token(token: str) -> bool:\n    return token.startswith('bearer_')"

    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    assert result.why.primary_intent.status == IntentEpistemicStatus.INFERRED
    assert len(result.why.inferences) >= 1
    # Must report unknown gap
    assert any("no explicit explanatory comments" in gap for gap in result.why.unknown_aspects)


# ============================================================================
# 6. LOCAL EVIDENCE SNIPPET RESOLUTION TEST
# ============================================================================

def test_local_evidence_snippet_integrity(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """Evidence snippets MUST come from local ContextItem.content, NEVER model text."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    assert len(result.evidence.grounded_traces) >= 1
    trace = result.evidence.grounded_traces[0]
    assert trace.item_id == "ci_1001"
    assert trace.grounded is True
    # Verbatim snippet matches item1.content
    assert trace.verbatim_snippet == sample_m4_packet.items[0].content
    assert trace.line_start == 10
    assert trace.line_end == 27


# ============================================================================
# 7. CONCEPTS & PROMPT BOUNDARY TESTS
# ============================================================================

def test_concepts_and_prompt_boundary(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """Concepts must have supporting item IDs, and CanIExplainThis must only generate a prompt."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    # Every concept has valid supporting_item_ids from the packet
    assert len(result.what_should_i_understand) >= 1
    for concept in result.what_should_i_understand:
        assert len(concept.supporting_item_ids) > 0
        assert concept.supporting_item_ids[0] in [i.item_id for i in sample_m4_packet.items]

    # Prompt generation only (M7 boundary)
    assert result.can_i_explain_this.is_available is True
    assert "src/auth.py" in result.can_i_explain_this.question
    assert "Purpose" in result.can_i_explain_this.expected_aspects
    assert "Mechanism" in result.can_i_explain_this.expected_aspects


# ============================================================================
# 8. PROVIDER-FAILURE FALLBACK TEST
# ============================================================================

def test_provider_failure_fallback(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    temp_db: Database,
    tmp_path: Path,
):
    """When the provider fails, returns deterministic M3 truth and UNKNOWN why without crashing."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.side_effect = ProviderTimeoutError("Gemini request timed out after 30s")

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    # 1. WhatChanged is complete deterministic M3 result
    assert result.what_changed.total_files_changed == 2
    assert result.what_changed.total_lines_added == 26
    assert "[AI explanation unavailable" in result.what_changed.supplementary_ai_narrative

    # 2. Why is UNKNOWN
    assert result.why.primary_intent.status == IntentEpistemicStatus.UNKNOWN
    assert result.why.inferences == []

    # 3. WhatShouldIUnderstand is empty list
    assert result.what_should_i_understand == []

    # 4. CanIExplainThis has is_available = False
    assert result.can_i_explain_this.is_available is False

    # 5. Audit row recorded
    audit_row = temp_db.get_understand_change_run_by_id(result.id)
    assert audit_row is not None
    assert audit_row["changeset_id"] == sample_m3_changeset.id
    assert audit_row["files_changed_count"] == 2


# ============================================================================
# 9. CONSENT ENFORCEMENT TEST
# ============================================================================

def test_consent_token_required(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    temp_db: Database,
    tmp_path: Path,
):
    """Running explain_changes without consent_token must raise ConsentViolationError."""
    with pytest.raises(ConsentViolationError, match="ConsentToken is required"):
        explain_changes(
            project_id="proj_test",
            root_path=tmp_path,
            db=temp_db,
            packet=sample_m4_packet,
            changeset=sample_m3_changeset,
            consent_token=None,
        )


# ============================================================================
# 10. UNGROUNDED ITEMS IN EVIDENCE SECTION TEST
# ============================================================================

def test_ungrounded_and_missing_items_in_evidence(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    temp_db: Database,
    tmp_path: Path,
):
    """Claims citing non-existent items or ungrounded claims appear in ungrounded_or_unknown."""
    gateway_result = ValidatedGatewayResult(
        id="gw_test_bad_claim",
        packet_id="pkt_test_200",
        provider="gemini",
        model="gemini-3.8-flash",
        summary="Summary with ungrounded claims.",
        claims=[
            StructuredClaim(
                statement="Hallucinated observation citing missing context item",
                claim_type=ClaimType.UNKNOWN,
                evidence_refs=["ci_9999_missing"],
                file_path="src/auth.py",
                confidence="LOW",
                grounded=False,
                validation_notes="Cited unknown evidence references: ['ci_9999_missing'].",
            ),
            StructuredClaim(
                statement="Claim without any evidence refs at all",
                claim_type=ClaimType.UNKNOWN,
                evidence_refs=[],
                file_path="src/auth.py",
                confidence="LOW",
                grounded=False,
                validation_notes="Observation lacks evidence references.",
            ),
        ],
        tokens_prompt=100,
        tokens_candidate=50,
        latency_ms=90.0,
        validation_summary={"total_claims": 2, "grounded_claims": 0},
    )

    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    assert result.evidence.grounding_ratio == 0.0
    assert len(result.evidence.ungrounded_or_unknown) == 2
    assert "ci_9999_missing" in result.evidence.ungrounded_or_unknown[0].item_id
    assert "[Referenced item ID 'ci_9999_missing' not found" in result.evidence.ungrounded_or_unknown[0].verbatim_snippet
    assert result.evidence.ungrounded_or_unknown[1].item_id == "none"


# ============================================================================
# 11. AUDIT PERSISTENCE ON SUCCESS TEST
# ============================================================================

def test_audit_persistence_on_success(
    sample_m3_changeset: ChangeSet,
    sample_m4_packet: ContextPacket,
    sample_gateway_result: ValidatedGatewayResult,
    temp_db: Database,
    tmp_path: Path,
):
    """Successful execution must record audit row in understand_change_runs."""
    mock_gateway = MagicMock()
    mock_gateway.generate_explanation.return_value = sample_gateway_result

    token = ConsentManager.grant_consent(sample_m4_packet)

    result = explain_changes(
        project_id="proj_test",
        root_path=tmp_path,
        db=temp_db,
        packet=sample_m4_packet,
        changeset=sample_m3_changeset,
        consent_token=token,
        gateway=mock_gateway,
    )

    audit_row = temp_db.get_understand_change_run_by_id(result.id)
    assert audit_row is not None
    assert audit_row["id"] == result.id
    assert audit_row["project_id"] == "proj_test"
    assert audit_row["changeset_id"] == sample_m3_changeset.id
    assert audit_row["packet_id"] == sample_m4_packet.id
    assert audit_row["gateway_run_id"] == sample_gateway_result.id
    assert audit_row["primary_category"] == result.what_changed.primary_category.value
    assert audit_row["files_changed_count"] == 2
    assert audit_row["grounding_ratio"] == result.evidence.grounding_ratio

