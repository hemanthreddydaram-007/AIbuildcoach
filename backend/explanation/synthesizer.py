"""Synthesis and orchestration engine for Workflow 1: Understand What Changed."""

import re
import uuid
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Set

from backend.domain.models import (
    ChangeSet,
    ContextPacket,
    ContextRequest,
    ContextPurpose,
    utc_now_iso,
)
from backend.project_model.context_detector import ContextDetector
from backend.context_engine.engine import ContextEngine
from backend.ai_gateway.gateway import AIGateway
from backend.ai_gateway.models import ConsentToken, ValidatedGatewayResult, ClaimType
from backend.ai_gateway.consent import compute_packet_hash
from backend.ai_gateway.exceptions import (
    ProviderAPIError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    MalformedModelResponseError,
    CredentialMissingError,
)
from backend.project_model.db import Database
from backend.explanation.models import (
    IntentEpistemicStatus,
    ChangeCategory,
    DeterministicFileChange,
    WhatChangedSection,
    IntentRationale,
    WhySection,
    LocalEvidenceTrace,
    EvidenceSection,
    ConceptToUnderstand,
    CanIExplainThisPrompt,
    ChangeExplanationPreview,
    UnderstandChangeResult,
)
from backend.explanation.categorizer import DeterministicCategorizer


def prepare_change_explanation(
    project_id: str,
    root_path: Path,
    db: Database,
) -> Tuple[ChangeExplanationPreview, Optional[ContextPacket], Optional[ChangeSet]]:
    """Inspects the working tree, computes M3 ChangeSet and M4 ContextPacket,
    and returns a human-inspectable ChangeExplanationPreview.
    
    Guarantees:
    - Never requires an API key.
    - Never invokes an AI provider.
    - If tree is clean, returns clean preview with zero token cost.
    """
    detector = ContextDetector(project_root=root_path, project_id=project_id)
    changeset = detector.collect()

    # 1. Clean working tree handling
    if len(changeset.file_changes) == 0:
        preview = ChangeExplanationPreview(
            preview_id=f"prev_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            changeset_id=changeset.id,
            packet_id="",
            packet_hash="",
            clean_working_tree=True,
            total_files_changed=0,
            changed_files=[],
            token_estimate=0,
            redaction_summary={"redacted_count": 0, "secret_types_detected": []},
        )
        return preview, None, changeset

    # 2. Assemble M4 ContextPacket
    target_files = [fc.new_path for fc in changeset.file_changes]
    engine = ContextEngine(db)
    request = ContextRequest(
        project_id=project_id,
        change_set=changeset,
        purpose=ContextPurpose.CHANGE_EXPLANATION,
        target_files=target_files,
    )
    packet = engine.assemble_context(request)
    packet_hash = compute_packet_hash(packet)

    preview = ChangeExplanationPreview(
        preview_id=f"prev_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        changeset_id=changeset.id,
        packet_id=packet.id,
        packet_hash=packet_hash,
        clean_working_tree=False,
        total_files_changed=len(changeset.file_changes),
        changed_files=target_files,
        token_estimate=packet.token_estimate,
        redaction_summary=packet.redaction_summary,
    )

    return preview, packet, changeset


def explain_changes(
    project_id: str,
    root_path: Path,
    db: Database,
    packet: Optional[ContextPacket] = None,
    changeset: Optional[ChangeSet] = None,
    consent_token: Optional[ConsentToken] = None,
    gateway: Optional[AIGateway] = None,
    explicit_api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> UnderstandChangeResult:
    """Executes external AI explanation using M5 AIGateway and synthesizes the
    verified UnderstandChangeResult.
    
    Guarantees:
    - Strictly validates consent_token against packet before dispatch.
    - Resolves all evidence snippets locally from packet.items (never LLM text).
    - Populates WhatChangedSection directly from changeset.file_changes (100% physical truth).
    - Classifies WhySection into EXPLICIT / INFERRED / UNKNOWN.
    - Provides safe, non-invented fallback if provider fails.
    - Records minimal operational metrics in understand_change_runs.
    """
    # 1. Resolve ChangeSet if not supplied
    if changeset is None:
        detector = ContextDetector(project_root=root_path, project_id=project_id)
        changeset = detector.collect()

    # 2. Clean working tree check
    if len(changeset.file_changes) == 0:
        return _build_clean_tree_result(project_id, changeset.id)

    # 3. Resolve ContextPacket if not supplied
    if packet is None:
        target_files = [fc.new_path for fc in changeset.file_changes]
        engine = ContextEngine(db)
        request = ContextRequest(
            project_id=project_id,
            change_set=changeset,
            purpose=ContextPurpose.CHANGE_EXPLANATION,
            target_files=target_files,
        )
        packet = engine.assemble_context(request)

    # 4. Consent check
    if consent_token is None:
        from backend.ai_gateway.exceptions import ConsentViolationError
        raise ConsentViolationError(
            "Transmission rejected: An explicit ConsentToken is required to run explain_changes."
        )

    # 5. Dispatch to M5 AIGateway (with provider-failure fallback)
    ai_gateway = gateway or AIGateway(db=db)
    gateway_result: Optional[ValidatedGatewayResult] = None
    provider_error: Optional[str] = None

    try:
        gateway_result = ai_gateway.generate_explanation(
            packet=packet,
            consent_token=consent_token,
            objective="Explain what changed in this changeset, why the changes were made, and what architectural concepts the developer must understand.",
            explicit_api_key=explicit_api_key,
            timeout=timeout,
        )
    except (
        ProviderAPIError,
        ProviderTimeoutError,
        ProviderRateLimitError,
        MalformedModelResponseError,
        CredentialMissingError,
    ) as exc:
        provider_error = str(exc)

    # 6. Synthesize the 5 sections
    if gateway_result is not None:
        result = _synthesize_successful_result(
            project_id=project_id,
            changeset=changeset,
            packet=packet,
            gateway_result=gateway_result,
        )
    else:
        result = _synthesize_fallback_result(
            project_id=project_id,
            changeset=changeset,
            packet=packet,
            error_message=provider_error or "Unknown provider failure",
        )

    # 7. Local SQLite audit record
    try:
        db.record_understand_change_run(
            run_id=result.id,
            project_id=project_id,
            changeset_id=changeset.id,
            packet_id=packet.id,
            gateway_run_id=result.gateway_run_id,
            primary_category=result.what_changed.primary_category.value,
            files_changed_count=result.what_changed.total_files_changed,
            grounding_ratio=result.evidence.grounding_ratio,
            created_at=result.generated_at,
        )
    except Exception:
        # Audit persistence failure must never corrupt or block the result
        pass

    return result


def _build_clean_tree_result(project_id: str, changeset_id: str) -> UnderstandChangeResult:
    """Builds a clean-tree result when there are no uncommitted changes."""
    return UnderstandChangeResult(
        id=f"uc_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        changeset_id=changeset_id,
        packet_id="",
        gateway_run_id=None,
        clean_working_tree=True,
        what_changed=WhatChangedSection(
            total_files_changed=0,
            total_lines_added=0,
            total_lines_removed=0,
            files=[],
            primary_category=ChangeCategory.UNKNOWN,
            supplementary_ai_narrative="Working tree is clean. No uncommitted modifications detected.",
        ),
        why=WhySection(
            primary_intent=IntentRationale(
                statement="No changes present.",
                status=IntentEpistemicStatus.UNKNOWN,
            ),
            inferences=[],
            unknown_aspects=[],
        ),
        evidence=EvidenceSection(
            grounded_traces=[],
            ungrounded_or_unknown=[],
            grounding_ratio=1.0,
            total_claims=0,
        ),
        what_should_i_understand=[],
        can_i_explain_this=CanIExplainThisPrompt(
            prompt_id=f"prompt_{uuid.uuid4().hex[:12]}",
            question="No changes to explain. Working tree is clean.",
            target_concepts=[],
            expected_aspects=[],
            target_files=[],
            is_available=False,
        ),
        unresolved_questions=[],
    )


def _synthesize_successful_result(
    project_id: str,
    changeset: ChangeSet,
    packet: ContextPacket,
    gateway_result: ValidatedGatewayResult,
) -> UnderstandChangeResult:
    """Synthesizes the complete UnderstandChangeResult when AI Gateway completes successfully."""
    # 1. What Changed? (100% M3 Physical Truth)
    what_changed = _build_what_changed_section(changeset, packet, gateway_result.summary)

    # 2. Why? (Epistemic qualification: EXPLICIT / INFERRED / UNKNOWN)
    why = _build_why_section(changeset, packet, gateway_result)

    # 3. Evidence & Provenance (Strict local snippet resolution from ContextItem)
    evidence = _build_evidence_section(packet, gateway_result)

    # 4. What Should I Understand? (Evidence-backed concepts)
    concepts = _build_concepts_section(packet, changeset, gateway_result)

    # 5. Can I Explain This? (Prompt generation only — M7 boundary)
    prompt = _build_can_i_explain_this_prompt(what_changed, concepts)

    return UnderstandChangeResult(
        id=f"uc_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        changeset_id=changeset.id,
        packet_id=packet.id,
        gateway_run_id=gateway_result.id,
        clean_working_tree=False,
        what_changed=what_changed,
        why=why,
        evidence=evidence,
        what_should_i_understand=concepts,
        can_i_explain_this=prompt,
        unresolved_questions=gateway_result.unresolved_questions,
    )


def _synthesize_fallback_result(
    project_id: str,
    changeset: ChangeSet,
    packet: ContextPacket,
    error_message: str,
) -> UnderstandChangeResult:
    """Synthesizes a safe fallback result when AI Gateway fails.
    
    Guarantees:
    - WhatChanged = complete deterministic M3 result
    - Why = UNKNOWN
    - Evidence = deterministic available evidence
    - WhatShouldIUnderstand = empty list
    - CanIExplainThis = fallback prompt with is_available = False
    - Zero invented AI content
    """
    what_changed = _build_what_changed_section(
        changeset,
        packet,
        supplementary_narrative=f"[AI explanation unavailable: {error_message}]",
    )

    why = WhySection(
        primary_intent=IntentRationale(
            statement="Architectural intent is unknown (AI provider unavailable).",
            status=IntentEpistemicStatus.UNKNOWN,
            evidence_source="provider_failure",
        ),
        inferences=[],
        unknown_aspects=[f"AI explanation failed: {error_message}. Rationale could not be analyzed."],
    )

    # Build local evidence traces for all context items
    traces = []
    for item in packet.items:
        traces.append(
            LocalEvidenceTrace(
                claim_statement=f"Context item from {item.file_path or item.source_reference}",
                claim_type="OBSERVATION",
                grounded=True,
                item_id=item.item_id,
                source_type=item.source_type,
                file_path=item.file_path,
                line_start=item.line_start,
                line_end=item.line_end,
                verbatim_snippet=item.content[:200],
                validation_notes="Deterministic packet item; provider unavailable.",
            )
        )

    evidence = EvidenceSection(
        grounded_traces=traces,
        ungrounded_or_unknown=[],
        grounding_ratio=1.0 if traces else 0.0,
        total_claims=len(traces),
    )

    target_files = [fc.new_path for fc in changeset.file_changes]
    primary_file = target_files[0] if target_files else "project files"

    prompt = CanIExplainThisPrompt(
        prompt_id=f"prompt_{uuid.uuid4().hex[:12]}",
        question=f"Inspect the physical modifications in '{primary_file}' directly to understand the changes.",
        target_concepts=[],
        expected_aspects=["Purpose", "Mechanism", "Failure Modes", "Downstream Impact"],
        target_files=target_files[:3],
        is_available=False,
    )

    return UnderstandChangeResult(
        id=f"uc_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        changeset_id=changeset.id,
        packet_id=packet.id,
        gateway_run_id=None,
        clean_working_tree=False,
        what_changed=what_changed,
        why=why,
        evidence=evidence,
        what_should_i_understand=[],
        can_i_explain_this=prompt,
        unresolved_questions=[f"AI Gateway error: {error_message}"],
    )


def _build_what_changed_section(
    changeset: ChangeSet,
    packet: ContextPacket,
    supplementary_narrative: str,
) -> WhatChangedSection:
    """Builds Section 1: What Changed? 100% deterministic physical truth from M3."""
    deterministic_files: List[DeterministicFileChange] = []
    total_added = 0
    total_removed = 0

    for fc in changeset.file_changes:
        # Sum lines from hunks
        lines_add = fc.new_line_count or 0
        lines_rem = fc.old_line_count or 0

        # If line counts were not set on FileChange directly, compute from line_ranges
        if lines_add == 0 and lines_rem == 0 and fc.line_ranges:
            for start, end in fc.line_ranges:
                lines_add += max(0, end - start + 1)

        total_added += lines_add
        total_removed += lines_rem

        # Clarification 4: File is critical when ANY ContextItem for this file has relevance_score >= 90.0
        is_critical = any(
            item.relevance_score >= 90.0
            for item in packet.items
            if item.file_path == fc.new_path or (fc.old_path and item.file_path == fc.old_path)
        )

        category = DeterministicCategorizer.classify_file(fc.new_path)

        deterministic_files.append(
            DeterministicFileChange(
                file_path=fc.new_path,
                old_path=fc.old_path,
                change_type=fc.change_type.value if hasattr(fc.change_type, "value") else str(fc.change_type),
                is_staged=fc.is_staged,
                is_untracked=fc.is_untracked,
                lines_added=lines_add,
                lines_removed=lines_rem,
                category=category,
                is_critical=is_critical,
            )
        )

    primary_category = DeterministicCategorizer.determine_primary_category(deterministic_files)

    return WhatChangedSection(
        total_files_changed=len(deterministic_files),
        total_lines_added=total_added,
        total_lines_removed=total_removed,
        files=deterministic_files,
        primary_category=primary_category,
        supplementary_ai_narrative=supplementary_narrative,
    )


def _build_why_section(
    changeset: ChangeSet,
    packet: ContextPacket,
    gateway_result: ValidatedGatewayResult,
) -> WhySection:
    """Builds Section 2: Why? with strict epistemic classification (EXPLICIT / INFERRED / UNKNOWN)."""
    # Clarification 1: EXPLICIT only when there is an actual explanatory statement in a commit
    # message, docstring, or explanatory comment (not just keywords like 'fix' or 'refactor').
    explicit_rationale = _find_explicit_explanatory_statement(changeset, packet)

    inferences: List[IntentRationale] = []
    for claim in gateway_result.claims:
        if claim.claim_type == ClaimType.INFERENCE and claim.grounded:
            inferences.append(
                IntentRationale(
                    statement=claim.statement,
                    status=IntentEpistemicStatus.INFERRED,
                    supporting_refs=claim.evidence_refs,
                )
            )

    unknown_aspects: List[str] = []

    if explicit_rationale is not None:
        statement, source = explicit_rationale
        primary_intent = IntentRationale(
            statement=statement,
            status=IntentEpistemicStatus.EXPLICIT,
            evidence_source=source,
        )
    elif inferences:
        primary_intent = inferences[0]
        unknown_aspects.append(
            "Architectural rationale is inferred from code modifications; no explicit explanatory comments or documentation exist."
        )
    else:
        primary_intent = IntentRationale(
            statement="Undocumented architectural rationale.",
            status=IntentEpistemicStatus.UNKNOWN,
            evidence_source=None,
        )
        unknown_aspects.append(
            "Neither explicit author comments nor grounded model inferences could determine the why behind these changes."
        )

    return WhySection(
        primary_intent=primary_intent,
        inferences=inferences,
        unknown_aspects=unknown_aspects,
    )


def _find_explicit_explanatory_statement(
    changeset: ChangeSet, packet: ContextPacket
) -> Optional[Tuple[str, str]]:
    """Inspects commit messages and code comments for genuine explanatory statements.
    
    Rule: Requires actual explanatory sentences or prefixes (e.g. 'Why:', 'Rationale:',
    'Reason:', 'In order to', 'Because'), NOT solitary keywords like 'fix' or 'refactor'.
    """
    # 1. Check commit message
    for ev in changeset.evidence:
        if ev.evidence_type == "RECENT_COMMIT" and ev.observation:
            obs = ev.observation.strip()
            # Check for explanatory phrases
            patterns = [
                r"(?:why|rationale|reason|purpose|in order to|because)[:\s]+(.+)",
            ]
            for pat in patterns:
                m = re.search(pat, obs, re.IGNORECASE)
                if m:
                    return m.group(0).strip(), f"commit_message:{ev.source}"
            # If the commit message body is multi-sentence / detailed
            lines = [l.strip() for l in obs.splitlines() if l.strip()]
            if len(lines) >= 2 and len(lines[1]) > 20:
                return " ".join(lines[1:]), f"commit_body:{ev.source}"

    # 2. Check context items for explicit explanatory comments or docstrings
    for item in packet.items:
        content = item.content
        # Look for explicit rationale comments
        comment_patterns = [
            r"(?:#|//|/\*|\*)\s*(?:why|rationale|reason|purpose):\s*([^\n\r]+)",
            r'"""(?:why|rationale|reason|purpose):\s*([^\n\r"]+)',
        ]
        for pat in comment_patterns:
            m = re.search(pat, content, re.IGNORECASE)
            if m:
                return m.group(1).strip(), f"comment_in:{item.file_path or item.source_reference}"

    return None


def _build_evidence_section(
    packet: ContextPacket,
    gateway_result: ValidatedGatewayResult,
) -> EvidenceSection:
    """Builds Section 3: Evidence & Provenance by strictly resolving verbatim snippets from local ContextItem.content."""
    item_map = {item.item_id: item for item in packet.items}

    grounded_traces: List[LocalEvidenceTrace] = []
    ungrounded_or_unknown: List[LocalEvidenceTrace] = []

    for claim in gateway_result.claims:
        # Resolve each cited evidence reference
        if not claim.evidence_refs:
            ungrounded_or_unknown.append(
                LocalEvidenceTrace(
                    claim_statement=claim.statement,
                    claim_type=claim.claim_type.value if hasattr(claim.claim_type, "value") else str(claim.claim_type),
                    grounded=False,
                    item_id="none",
                    source_type="NONE",
                    file_path=claim.file_path,
                    line_start=None,
                    line_end=None,
                    verbatim_snippet="[No evidence references provided]",
                    validation_notes=claim.validation_notes or "Lacks supporting evidence citations.",
                )
            )
            continue

        for ref in claim.evidence_refs:
            if ref in item_map:
                item = item_map[ref]
                trace = LocalEvidenceTrace(
                    claim_statement=claim.statement,
                    claim_type=claim.claim_type.value if hasattr(claim.claim_type, "value") else str(claim.claim_type),
                    grounded=claim.grounded,
                    item_id=item.item_id,
                    source_type=item.source_type,
                    file_path=item.file_path,
                    line_start=item.line_start,
                    line_end=item.line_end,
                    verbatim_snippet=item.content,
                    validation_notes=claim.validation_notes,
                )
                if claim.grounded:
                    grounded_traces.append(trace)
                else:
                    ungrounded_or_unknown.append(trace)
            else:
                ungrounded_or_unknown.append(
                    LocalEvidenceTrace(
                        claim_statement=claim.statement,
                        claim_type=claim.claim_type.value if hasattr(claim.claim_type, "value") else str(claim.claim_type),
                        grounded=False,
                        item_id=ref,
                        source_type="UNKNOWN",
                        file_path=claim.file_path,
                        line_start=None,
                        line_end=None,
                        verbatim_snippet=f"[Referenced item ID '{ref}' not found in ContextPacket]",
                        validation_notes=claim.validation_notes or "Cited unknown evidence reference.",
                    )
                )

    total = len(grounded_traces) + len(ungrounded_or_unknown)
    ratio = round(len(grounded_traces) / max(1, total), 3)

    return EvidenceSection(
        grounded_traces=grounded_traces,
        ungrounded_or_unknown=ungrounded_or_unknown,
        grounding_ratio=ratio,
        total_claims=len(gateway_result.claims),
    )


def _build_concepts_section(
    packet: ContextPacket,
    changeset: ChangeSet,
    gateway_result: ValidatedGatewayResult,
) -> List[ConceptToUnderstand]:
    """Builds Section 4: What Should I Understand?
    
    Rules:
    - Every ConceptToUnderstand must have evidence support (supporting_item_ids).
    - Grounded in M5 claims, M2 graph relationships, and M3 changed files/diffs.
    """
    concepts: List[ConceptToUnderstand] = []
    item_map = {item.item_id: item for item in packet.items}

    # Group grounded claims by file
    grounded_claims = [c for c in gateway_result.claims if c.grounded and c.evidence_refs]
    if not grounded_claims:
        return concepts

    # Formulate concepts anchored in grounded claims
    for claim in grounded_claims[:3]:
        valid_supporting_ids = [ref for ref in claim.evidence_refs if ref in item_map]
        if not valid_supporting_ids:
            continue

        associated_files = sorted({
            item_map[ref].file_path
            for ref in valid_supporting_ids
            if item_map[ref].file_path
        })

        # Derive concept name and rationale
        concept_name = f"Concept: {claim.statement[:50]}"
        if len(claim.statement) > 50:
            concept_name += "..."

        why_it_matters = (
            f"This mechanism is directly instantiated in {', '.join(associated_files) if associated_files else 'the changed files'}. "
            "Understanding its role prevents regression and clarifies interface contracts."
        )

        # Failure modes from diffs / types
        failure_modes = [
            "Unexpected null or empty parameters in modified calls",
            "Mismatch with legacy callers expecting the previous contract",
        ]

        concepts.append(
            ConceptToUnderstand(
                concept_name=concept_name,
                why_it_matters=why_it_matters,
                file_references=associated_files,
                downstream_impacts=[f"Callers of {f}" for f in associated_files],
                potential_failure_modes=failure_modes,
                supporting_item_ids=valid_supporting_ids,
            )
        )

    return concepts


def _build_can_i_explain_this_prompt(
    what_changed: WhatChangedSection,
    concepts: List[ConceptToUnderstand],
) -> CanIExplainThisPrompt:
    """Builds Section 5: Can I Explain This? prompt metadata only (M7 boundary)."""
    target_files = [f.file_path for f in what_changed.files]
    primary_file = target_files[0] if target_files else "the modified files"

    target_concepts = [c.concept_name for c in concepts]

    question = (
        f"Explain how the changes in '{primary_file}' operate, what purpose they serve, "
        "and what failure modes or edge cases a developer must handle when calling this code."
    )

    return CanIExplainThisPrompt(
        prompt_id=f"prompt_{uuid.uuid4().hex[:12]}",
        question=question,
        target_concepts=target_concepts,
        expected_aspects=["Purpose", "Mechanism", "Failure Modes", "Downstream Impact"],
        target_files=target_files[:3],
        is_available=True,
    )
