"""Deterministic validator and anti-hallucination engine for M12.6 Timeline Explanations."""

import re
from typing import Dict, Any, List, Set, Tuple
from backend.observation.models import (
    TimelineExplanationPacket,
    IncidentExplanation,
    StatementWithEvidence,
    FixStatus,
)


FORBIDDEN_CAUSALITY_PHRASES = [
    "definitely caused",
    "definitely the root cause",
    "was the sole cause",
    "sole cause",
    "ai fixed the backend",
    "ai fixed it",
    "completely correct",
    "bug fixed",
    "100% fixed",
    "proven root cause",
]


class TimelineExplanationValidator:
    """Validates that IncidentExplanation output is strictly grounded in the supplied TimelineExplanationPacket.
    
    Responsibilities:
    1. Validate schema and required section structures.
    2. Validate that every evidence_ref cited by a statement exists in the input packet's event IDs.
    3. Detect missing evidence references for factual statements in problem, changes, and verification.
    4. Reject claims of unproven causality ("definitely caused", "sole cause", etc.).
    5. Enforce deterministic fix_status consistency:
       - If packet has no recovery, forbid claiming VERIFIED or RECOVERED.
       - If packet has fix_status = UNKNOWN, explanation cannot declare success.
       - If packet has fix_status = RECOVERED, explanation cannot declare VERIFIED.
    6. Ensure epistemic unknowns and limitations are preserved.
    """

    @staticmethod
    def extract_valid_evidence_ids(packet: TimelineExplanationPacket) -> Set[str]:
        """Collects all valid canonical event and link IDs from the input packet."""
        valid_ids: Set[str] = set()
        if packet.incident and "event_id" in packet.incident:
            valid_ids.add(packet.incident["event_id"])
        
        for item in packet.timeline:
            if "event_id" in item:
                valid_ids.add(item["event_id"])
        
        for item in packet.changes:
            if "event_id" in item:
                valid_ids.add(item["event_id"])

        for item in packet.verification:
            if "event_id" in item:
                valid_ids.add(item["event_id"])

        for item in packet.correlations:
            if "link_id" in item:
                valid_ids.add(item["link_id"])
            if "source_event_id" in item:
                valid_ids.add(item["source_event_id"])
            if "target_event_id" in item:
                valid_ids.add(item["target_event_id"])

        return valid_ids

    @classmethod
    def validate_explanation(
        cls,
        explanation: IncidentExplanation,
        packet: TimelineExplanationPacket,
    ) -> Tuple[bool, List[str]]:
        """Performs rigorous deterministic validation.
        
        Returns (is_valid, rejection_reasons).
        """
        rejection_reasons: List[str] = []
        valid_evidence_ids = cls.extract_valid_evidence_ids(packet)

        # 1. Packet ID & Project ID matching
        if explanation.packet_id != packet.packet_id:
            rejection_reasons.append(
                f"Packet ID mismatch: explanation cites '{explanation.packet_id}', expected '{packet.packet_id}'."
            )
        if explanation.project_id != packet.project_id:
            rejection_reasons.append(
                f"Project ID mismatch: explanation cites '{explanation.project_id}', expected '{packet.project_id}'."
            )

        # 2. Fix status consistency
        if packet.fix_status == FixStatus.UNKNOWN and explanation.fix_status in (FixStatus.VERIFIED, FixStatus.RECOVERED):
            rejection_reasons.append(
                f"Fix status mismatch: packet status is UNKNOWN, but explanation claims '{explanation.fix_status}'."
            )
        elif packet.fix_status == FixStatus.RECOVERED and explanation.fix_status == FixStatus.VERIFIED:
            rejection_reasons.append(
                "Fix status mismatch: operational recovery is not equivalent to test-verified status."
            )
        elif packet.fix_status == FixStatus.PERSISTING and explanation.fix_status != FixStatus.PERSISTING:
            rejection_reasons.append(
                f"Fix status mismatch: packet indicates error is PERSISTING, but explanation claims '{explanation.fix_status}'."
            )

        # 3. Evidence Reference Integrity
        all_cited_refs: Set[str] = set()

        def validate_statements(section_name: str, statements: List[StatementWithEvidence], require_refs: bool = True):
            for idx, stmt in enumerate(statements):
                if not stmt.statement or not stmt.statement.strip():
                    rejection_reasons.append(f"{section_name}[{idx}] contains empty statement text.")
                    continue

                if require_refs and not stmt.evidence_refs:
                    rejection_reasons.append(
                        f"{section_name}[{idx}] '{stmt.statement[:40]}...' lacks evidence references."
                    )
                
                for ref in stmt.evidence_refs:
                    all_cited_refs.add(ref)
                    if ref not in valid_evidence_ids:
                        rejection_reasons.append(
                            f"{section_name}[{idx}] cited unknown evidence reference '{ref}'."
                        )

        validate_statements("problem", explanation.problem, require_refs=True)
        validate_statements("observed_sequence", explanation.observed_sequence, require_refs=True)
        validate_statements("changes", explanation.changes, require_refs=True)
        validate_statements("verification", explanation.verification, require_refs=len(packet.verification) > 0)

        # 4. Check forbidden causality language across factual and explanatory text
        text_corpus = " ".join([
            explanation.summary,
            " ".join(s.statement for s in explanation.problem),
            " ".join(s.statement for s in explanation.observed_sequence),
            " ".join(s.statement for s in explanation.changes),
            " ".join(s.statement for s in explanation.verification),
            " ".join(explanation.what_to_understand),
        ]).lower()

        for forbidden in FORBIDDEN_CAUSALITY_PHRASES:
            if forbidden in text_corpus:
                rejection_reasons.append(
                    f"Forbidden ungrounded causality language detected: '{forbidden}'."
                )

        # 5. Preservation of unknowns
        if not explanation.unknowns:
            rejection_reasons.append("Explanation failed to declare any epistemic unknowns.")
        else:
            # Check for core humility markers
            has_epistemic_qualification = any(
                ("sole cause" in u.lower() or "cannot prove" in u.lower() or "causal" in u.lower() or "unknown" in u.lower())
                for u in explanation.unknowns
            )
            if not has_epistemic_qualification:
                rejection_reasons.append(
                    "Explanation unknowns lack required epistemic qualifications regarding causal limits."
                )

        is_valid = len(rejection_reasons) == 0
        return is_valid, rejection_reasons
