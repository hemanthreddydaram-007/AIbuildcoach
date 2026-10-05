"""Deterministic Mock AI Gateway Adapter supporting all rating and fault injection scenarios."""

import json
from typing import Dict, Any, Optional

from backend.ai_gateway.provider import AIProviderAdapter
from backend.ai_gateway.models import RawInteractionResponse
from backend.ai_gateway.exceptions import (
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAPIError,
    MalformedModelResponseError,
)


class MockAIProviderAdapter(AIProviderAdapter):
    """Deterministic mock provider adapter for multi-repo validation and fault injection testing."""

    def __init__(self, scenario: str = "STRONG", fixed_evidence_ref: Optional[str] = None):
        """Initializes mock adapter with specific scenario behavior.
        
        Scenarios:
            - STRONG: Returns strong ratings with grounded evidence citations
            - ADEQUATE: Returns adequate ratings
            - PARTIAL: Returns partial ratings with minor gaps
            - WEAK: Returns weak ratings with critical gaps
            - UNKNOWN: Returns unknown ratings
            - malformed_json: Raises MalformedModelResponseError
            - timeout: Raises ProviderTimeoutError
            - http_429: Raises ProviderRateLimitError
            - http_503: Raises ProviderAPIError
        """
        self.scenario = scenario
        self.fixed_evidence_ref = fixed_evidence_ref
        self.invocation_count = 0

    def get_provider_name(self) -> str:
        return "gemini"

    def get_model_name(self) -> str:
        return "gemini-3.8-flash"

    def complete_interaction(
        self,
        system_instruction: str,
        user_input: str,
        response_format: Dict[str, Any],
        api_key: str,
        timeout: float = 30.0,
    ) -> RawInteractionResponse:
        self.invocation_count += 1

        # 1. Fault injection scenarios
        if self.scenario == "timeout":
            raise ProviderTimeoutError(f"Mock connection timed out after {timeout}s")
        elif self.scenario == "http_429":
            raise ProviderRateLimitError("Rate limit exceeded: 429 Too Many Requests")
        elif self.scenario == "http_503":
            raise ProviderAPIError("Service Unavailable: 503", status_code=503)
        elif self.scenario == "malformed_json":
            raise MalformedModelResponseError("Malformed JSON response from mock provider")

        # 2. Extract context item IDs from user_input if present to ensure claims are grounded
        import re
        item_ids = re.findall(r'<context_item\s+id="([^"]+)"', user_input)
        if not item_ids:
            item_ids = re.findall(r"\bci_[a-f0-9]+\b", user_input)
        ref_id = self.fixed_evidence_ref or (item_ids[0] if item_ids else "ci_mock_001")
        all_refs = [ref_id]

        # 3. Detect interaction type from prompt objective
        is_question_gen = (
            "Question Generation" in user_input
            or "VIVA QUESTION GENERATOR" in user_input
            or "Viva Defence Interview Question Generation" in user_input
        )
        is_comprehension = "Comprehension Evaluation" in user_input
        is_viva = (not is_question_gen) and (not is_comprehension) and (
            "TASK OBJECTIVE: Oral Defence Viva" in user_input
            or "Oral Defence" in user_input
            or "Viva" in user_input
        )

        if is_question_gen:
            # Viva question generator response
            payload = {
                "summary": "Generated viva defence question.",
                "claims": [
                    {
                        "statement": "VIVA_QUESTION | How does the module handle authentication and token validation?",
                        "claim_type": "OBSERVATION",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    },
                    {
                        "statement": "VIVA_EXPECTED_CONCEPT | Token Verification and Authentication Lifecycle",
                        "claim_type": "INFERENCE",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    },
                    {
                        "statement": "VIVA_TARGET_MODULE | auth",
                        "claim_type": "OBSERVATION",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    }
                ],
                "unresolved_questions": [],
            }
        elif is_viva:
            # Viva turn evaluation response
            rating_map = {
                "STRONG": ("STRONG", "TRUE", "Clear, accurate, project-grounded mastery."),
                "ADEQUATE": ("ADEQUATE", "TRUE", "Adequate understanding of core mechanics."),
                "PARTIAL": ("PARTIAL", "TRUE", "Partial understanding; missing key module details."),
                "WEAK": ("WEAK", "FALSE", "Significant misconceptions identified."),
                "UNKNOWN": ("UNKNOWN", "FALSE", "Unable to evaluate candidate response."),
            }
            rating, grounded, feedback = rating_map.get(self.scenario, ("STRONG", "TRUE", "Evaluated."))

            claims = [
                {
                    "statement": f"VIVA_EVAL:{rating}:{grounded} | {feedback}",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                }
            ]
            if rating in ("PARTIAL", "WEAK"):
                claims.append({
                    "statement": "VIVA_GAP:MODERATE | Expected: Token verification logic | Misconception: Assumed handled externally",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                })

            payload = {
                "summary": f"Viva turn evaluated as {rating}.",
                "claims": claims,
                "unresolved_questions": [],
            }
        elif is_comprehension:
            # Comprehension evaluation response (M7)
            rating_map = {
                "STRONG": "UNDERSTOOD",
                "ADEQUATE": "UNDERSTOOD",
                "PARTIAL": "PARTIALLY_UNDERSTOOD",
                "WEAK": "NEEDS_REVIEW",
                "UNKNOWN": "UNKNOWN",
            }
            comp_rating = rating_map.get(self.scenario, "UNDERSTOOD")

            claims = [
                {
                    "statement": f"DIMENSION:PURPOSE:{comp_rating} | Correct purpose demonstrated.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                },
                {
                    "statement": f"DIMENSION:MECHANISM:{comp_rating} | Mechanism accurately explained.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                },
                {
                    "statement": f"DIMENSION:FAILURE_MODES:{comp_rating} | Failure handling understood.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                },
                {
                    "statement": f"DIMENSION:DOWNSTREAM_IMPACT:{comp_rating} | Downstream effects recognized.",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                },
            ]
            if comp_rating in ("PARTIALLY_UNDERSTOOD", "NEEDS_REVIEW"):
                claims.append({
                    "statement": "GAP:MECHANISM:MODERATE | Expected: Validated middleware execution | Misconception: Direct db write",
                    "claim_type": "INFERENCE",
                    "evidence_refs": all_refs,
                    "confidence": "HIGH",
                })

            payload = {
                "summary": f"Comprehension evaluated as {comp_rating}.",
                "claims": claims,
                "unresolved_questions": [],
            }
        elif self.scenario in ("UNDOCUMENTED", "UNKNOWN"):
            # Intentionally undocumented rationale: observations only, zero inferred claims
            payload = {
                "summary": "Detected file modifications without identifiable architectural rationale.",
                "claims": [
                    {
                        "statement": "The authentication route was updated to enforce token validation.",
                        "claim_type": "OBSERVATION",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    },
                ],
                "unresolved_questions": ["Architectural rationale could not be determined from unannotated code."],
            }
        else:
            # Standard M6 change explanation response with supported inference
            payload = {
                "summary": "Grounded explanation of repository changes.",
                "claims": [
                    {
                        "statement": "The authentication route was updated to enforce token validation.",
                        "claim_type": "OBSERVATION",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    },
                    {
                        "statement": "Improves endpoint security by rejecting unauthenticated callers.",
                        "claim_type": "INFERENCE",
                        "evidence_refs": all_refs,
                        "confidence": "HIGH",
                    },
                ],
                "unresolved_questions": [],
            }

        return RawInteractionResponse(
            status_code=200,
            raw_json=payload,
            raw_text=json.dumps(payload),
            latency_ms=15.0,
            tokens_prompt=120,
            tokens_candidate=85,
        )
