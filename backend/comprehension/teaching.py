"""Targeted teaching synthesis for Milestone 7: Can I Explain This? Comprehension Loop."""

import re
import uuid
from typing import List, Dict, Set, Optional

from backend.ai_gateway.models import ValidatedGatewayResult, StructuredClaim
from backend.comprehension.models import (
    ComprehensionRating,
    KnowledgeGap,
    ReverificationPrompt,
    StudentExplanationSubmission,
    TargetedTeaching,
)


def synthesize_targeted_teaching(
    gateway_result: ValidatedGatewayResult,
    submission: StudentExplanationSubmission,
    all_gaps: List[KnowledgeGap],
    teaching_takeaways: List[str],
    teaching_annotations: Dict[str, str],
    consumed_claims: List[StructuredClaim],
    valid_item_ids: Set[str],
    overall_state: ComprehensionRating,
    is_evaluation_fully_grounded: bool,
) -> TargetedTeaching:
    """Synthesizes pedagogical feedback strictly from grounded takeaways and context items.
    
    Invariants:
    - ValidatedGatewayResult.summary is NEVER assigned to teaching summary.
    - Summary is constructed from grounded takeaways; defaults to safe fallback if none exist.
    - ReverificationPrompt is generated only if gaps exist and attempt_number < 3.
    """
    # 1. Synthesize teaching summary strictly from grounded takeaways
    if teaching_takeaways:
        teaching_summary = " ".join(teaching_takeaways)
    else:
        teaching_summary = "Review the verified repository evidence below."

    # 2. Reverification prompt generation
    reverification_prompt: Optional[ReverificationPrompt] = None
    if overall_state != ComprehensionRating.UNDERSTOOD and submission.attempt_number < 3:
        focus_qs: List[str] = [g.expected_understanding for g in all_gaps if g.grounded][:2]
        focus_qs.extend(gateway_result.unresolved_questions[:2])
        if not focus_qs:
            focus_qs = ["Review the diff and verify the failure modes and downstream impacts."]

        reverification_prompt = ReverificationPrompt(
            prompt_id=f"rev_{uuid.uuid4().hex[:12]}",
            parent_submission_id=submission.submission_id,
            attempt_number=submission.attempt_number + 1,
            focus_questions=focus_qs,
        )

    # 3. Collect grounded evidence IDs
    grounded_evidence_ids = [
        cid
        for claim in consumed_claims
        for cid in claim.evidence_refs
        if cid in valid_item_ids
    ]

    return TargetedTeaching(
        teaching_id=f"teach_{uuid.uuid4().hex[:12]}",
        summary=teaching_summary,
        key_takeaways=teaching_takeaways,
        code_annotations=teaching_annotations,
        grounded_evidence_ids=list(dict.fromkeys(grounded_evidence_ids)),
        reverification_prompt=reverification_prompt,
        grounded=is_evaluation_fully_grounded,
    )
