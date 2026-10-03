"""Evidence validation and anti-hallucination engine for AI Gateway."""

from typing import Tuple, Dict, Any, Set
from backend.domain.models import ContextPacket
from backend.ai_gateway.models import ExplanationResponse, StructuredClaim, ClaimType


class EvidenceValidator:
    """Validates model claims against canonical ContextItem.item_id evidence in ContextPacket."""

    @staticmethod
    def validate_explanation(
        explanation: ExplanationResponse,
        packet: ContextPacket,
    ) -> Tuple[ExplanationResponse, Dict[str, Any]]:
        """Validates evidence references for all claims in an ExplanationResponse.
        
        Canonical Reference Rule:
        - Evidence references must match ContextItem.item_id in the packet.
        - If an evidence reference is invalid, the validator marks grounded=False
          and coerces the claim_type to UNKNOWN (or rejects it).
        - Unsupported factual observations are NEVER silently rewritten to INFERENCE.
        """
        valid_item_ids: Set[str] = {item.item_id for item in packet.items}
        valid_file_paths: Set[str] = {item.file_path for item in packet.items if item.file_path}

        grounded_count = 0
        unknown_count = 0
        observation_count = 0
        inference_count = 0
        recommendation_count = 0

        validated_claims = []

        for claim in explanation.claims:
            is_grounded = True
            notes = []

            # 1. Validate evidence_refs against canonical ContextItem.item_id namespace
            if not claim.evidence_refs:
                is_grounded = False
                if claim.claim_type == ClaimType.OBSERVATION:
                    claim.claim_type = ClaimType.UNKNOWN
                    notes.append("Observation lacks evidence references; coerced to UNKNOWN.")
            else:
                invalid_refs = [ref for ref in claim.evidence_refs if ref not in valid_item_ids]
                if invalid_refs:
                    is_grounded = False
                    notes.append(f"Cited unknown evidence references: {invalid_refs}.")
                    # Non-silent enforcement: Never silently convert to INFERENCE
                    if claim.claim_type == ClaimType.OBSERVATION:
                        claim.claim_type = ClaimType.UNKNOWN
                        notes.append("Invalid evidence citations; coerced from OBSERVATION to UNKNOWN.")

            # 2. Validate file_path if specified
            if claim.file_path:
                norm_path = claim.file_path.strip().replace("\\", "/").lstrip("/")
                if norm_path not in valid_file_paths:
                    is_grounded = False
                    notes.append(f"Referenced uninspected file path '{claim.file_path}'.")
                    if claim.claim_type == ClaimType.OBSERVATION:
                        claim.claim_type = ClaimType.UNKNOWN
                        notes.append("Hallucinated file path; coerced to UNKNOWN.")

            claim.grounded = is_grounded
            if notes:
                claim.validation_notes = " ".join(notes)
            else:
                claim.validation_notes = "Grounded in valid ContextItem evidence."

            # Update metrics
            if claim.grounded:
                grounded_count += 1
            if claim.claim_type == ClaimType.UNKNOWN:
                unknown_count += 1
            elif claim.claim_type == ClaimType.OBSERVATION:
                observation_count += 1
            elif claim.claim_type == ClaimType.INFERENCE:
                inference_count += 1
            elif claim.claim_type == ClaimType.RECOMMENDATION:
                recommendation_count += 1

            validated_claims.append(claim)

        explanation.claims = validated_claims

        summary = {
            "total_claims": len(validated_claims),
            "grounded_claims": grounded_count,
            "unknown_claims": unknown_count,
            "observation_claims": observation_count,
            "inference_claims": inference_count,
            "recommendation_claims": recommendation_count,
            "grounding_ratio": round(grounded_count / max(1, len(validated_claims)), 3),
        }

        return explanation, summary
