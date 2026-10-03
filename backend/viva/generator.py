"""Question Generator for Milestone 8: Viva Defence Engine.
Generates project-grounded viva interview questions across categories and difficulty tiers.
"""

import re
import uuid
from typing import List, Optional, Dict, Tuple

from backend.domain.models import ContextRequest, ContextPurpose, utc_now_iso
from backend.context_engine.engine import ContextEngine
from backend.ai_gateway.models import ConsentToken, ValidatedGatewayResult
from backend.ai_gateway.gateway import AIGateway
from backend.project_model.db import Database
from backend.viva.models import (
    VivaCategory,
    VivaDifficulty,
    VivaQuestion,
)
from backend.viva.index import ProjectArchitecturalIndex


DEFAULT_CATEGORY_CONCEPTS: Dict[VivaCategory, List[str]] = {
    VivaCategory.PROJECT_PURPOSE: [
        "Core application domain and user goals",
        "Primary entrypoint and CLI/API boundaries",
        "Key capabilities and expected outputs",
    ],
    VivaCategory.ARCHITECTURE_OVERVIEW: [
        "System layering and component responsibilities",
        "Modularity and dependency separation",
        "Integration of subsystems",
    ],
    VivaCategory.DATA_FLOW: [
        "Data ingestion, validation, and transformation",
        "Inter-component data contracts and schemas",
        "Pipeline state and immutability",
    ],
    VivaCategory.API_CONTRACTS: [
        "Endpoint and interface schemas",
        "Request/response validation and error models",
        "Decoupling of client and provider protocols",
    ],
    VivaCategory.STORAGE_PERSISTENCE: [
        "Database schema design and migration management",
        "ACID guarantees, transaction boundaries, and isolation",
        "Query efficiency and connection lifecycle",
    ],
    VivaCategory.SECURITY_AUTH: [
        "Zero-trust prompt injection boundaries and fences",
        "Credential isolation and BYOK key handling",
        "Path traversal defense and input sanitization",
    ],
    VivaCategory.DEPENDENCIES: [
        "Third-party library integration and version pinning",
        "Decoupling external SDKs behind adapters",
        "Supply chain and license posture",
    ],
    VivaCategory.FAILURE_MODES: [
        "Graceful degradation on provider failure or timeouts",
        "Deterministic fallbacks and circuit breakers",
        "Stale run detection and crash recovery",
    ],
    VivaCategory.TESTING_VERIFICATION: [
        "Unit, integration, and end-to-end coverage",
        "Deterministic fixtures and hermetic test isolation",
        "Verification of anti-hallucination and grounding guarantees",
    ],
}


def build_question_generation_objective(
    category: VivaCategory,
    difficulty: VivaDifficulty,
    target_files: List[str],
    is_follow_up: bool = False,
    parent_question_text: Optional[str] = None,
    followup_focus: Optional[str] = None,
) -> str:
    """Constructs prompt objective for AI Gateway viva question generation."""
    files_str = ", ".join(target_files) if target_files else "Entire repository"
    followup_ctx = ""
    if is_follow_up:
        followup_ctx = (
            f"\nTHIS IS AN ADAPTIVE FOLLOW-UP QUESTION.\n"
            f"Prior Question: {parent_question_text or 'N/A'}\n"
            f"Area of probed weakness/gap: {followup_focus or 'Technical specifics and failure modes'}\n"
            "Ask a pointed, deeper follow-up question testing the candidate on this specific gap.\n"
        )

    return (
        "TASK OBJECTIVE: Viva Defence Interview Question Generation.\n\n"
        f"CATEGORY: {category.value}\n"
        f"DIFFICULTY TIER: {difficulty.value}\n"
        f"TARGET REPOSITORY FILES: {files_str}\n"
        f"{followup_ctx}\n"
        "DIFFICULTY GUIDELINES:\n"
        "- EASY: Component identification, direct responsibilities, primary function of the components.\n"
        "- MEDIUM: Architectural purpose, component interactions, how data/control moves between components.\n"
        "- HARD: Failure modes, edge cases, boundary defenses, exception handling, race conditions.\n"
        "- DEEP: Invariant guarantees, system trade-offs, design alternatives, security boundary defenses.\n\n"
        "INSTRUCTIONS:\n"
        "Generate a single incisive oral defence question testing the candidate's understanding of this specific repository.\n"
        "TAGGED CLAIM PROTOCOL:\n"
        "Format your claims using these exact deterministic tags in statement:\n"
        "- 'VIVA_QUESTION | <The question text>'\n"
        "- 'VIVA_EXPECTED_CONCEPT | <Expected concept or principle candidate must demonstrate>'\n"
        "- 'VIVA_TARGET_MODULE | <Module or component path>'\n\n"
        "Every claim MUST cite valid ContextItem item_ids in evidence_refs from the provided evidence."
    )


def _build_fallback_question(
    category: VivaCategory,
    difficulty: VivaDifficulty,
    target_files: List[str],
    is_follow_up: bool = False,
    parent_question_text: Optional[str] = None,
    followup_focus: Optional[str] = None,
) -> Tuple[str, List[str], List[str]]:
    """Builds a deterministic fallback question if AI Gateway returns empty or fails."""
    target_str = ", ".join(target_files[:2]) if target_files else category.value
    expected_concepts = list(DEFAULT_CATEGORY_CONCEPTS.get(category, ["System architecture", "Implementation details"]))
    modules = target_files[:2] if target_files else [category.value]

    if is_follow_up:
        focus = followup_focus or "concrete implementation details and edge cases"
        q_text = f"Following up on {target_str}: Can you explain specifically how the system handles {focus}?"
    else:
        if difficulty == VivaDifficulty.EASY:
            q_text = f"What is the primary architectural responsibility of {target_str} in this system?"
        elif difficulty == VivaDifficulty.MEDIUM:
            q_text = f"How does {target_str} coordinate with other components and manage its internal data flow?"
        elif difficulty == VivaDifficulty.HARD:
            q_text = f"What failure modes or edge cases arise in {target_str}, and how does the implementation defend against them?"
        else:  # DEEP
            q_text = f"What core invariant guarantees and architectural trade-offs govern the design of {target_str}?"

    return q_text, expected_concepts, modules


def generate_viva_question(
    project_id: str,
    session_id: str,
    turn_index: int,
    category: VivaCategory,
    difficulty: VivaDifficulty,
    index: ProjectArchitecturalIndex,
    context_engine: ContextEngine,
    gateway: AIGateway,
    consent_token: ConsentToken,
    db: Database,
    explicit_api_key: Optional[str] = None,
    is_follow_up: bool = False,
    parent_question_id: Optional[str] = None,
    parent_question_text: Optional[str] = None,
    followup_focus: Optional[str] = None,
) -> VivaQuestion:
    """Generates a project-grounded viva question and persists it to the database for restart safety."""
    target_files = index.get_category_files(category, limit=5)

    # 1. Assemble M4 context packet targeting category files
    request = ContextRequest(
        project_id=project_id,
        purpose=ContextPurpose.PROJECT_OVERVIEW,
        target_files=target_files,
        budget_tokens=4000,
    )
    packet = context_engine.build_context_packet(request)
    valid_item_ids = {item.item_id for item in packet.items}

    # 2. Invoke frozen M5 AIGateway
    question_text: Optional[str] = None
    expected_concepts: List[str] = []
    target_modules: List[str] = []
    supporting_evidence_ids: List[str] = []

    objective = build_question_generation_objective(
        category=category,
        difficulty=difficulty,
        target_files=target_files,
        is_follow_up=is_follow_up,
        parent_question_text=parent_question_text,
        followup_focus=followup_focus,
    )

    try:
        gateway_result: ValidatedGatewayResult = gateway.generate_explanation(
            packet=packet,
            consent_token=consent_token,
            objective=objective,
            explicit_api_key=explicit_api_key,
        )

        for claim in gateway_result.claims:
            stmt = claim.statement.strip()
            # Parse tags
            if stmt.startswith("VIVA_QUESTION:") or stmt.startswith("VIVA_QUESTION |"):
                parts = re.split(r"[:|]\s*", stmt, maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    question_text = parts[1].strip()
                    for ref in claim.evidence_refs:
                        if ref in valid_item_ids and ref not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ref)
            elif stmt.startswith("VIVA_EXPECTED_CONCEPT:") or stmt.startswith("VIVA_EXPECTED_CONCEPT |"):
                parts = re.split(r"[:|]\s*", stmt, maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    expected_concepts.append(parts[1].strip())
                    for ref in claim.evidence_refs:
                        if ref in valid_item_ids and ref not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ref)
            elif stmt.startswith("VIVA_TARGET_MODULE:") or stmt.startswith("VIVA_TARGET_MODULE |"):
                parts = re.split(r"[:|]\s*", stmt, maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    target_modules.append(parts[1].strip())
                    for ref in claim.evidence_refs:
                        if ref in valid_item_ids and ref not in supporting_evidence_ids:
                            supporting_evidence_ids.append(ref)

        if not question_text and gateway_result.summary and "?" in gateway_result.summary:
            question_text = gateway_result.summary.strip()
    except Exception:
        # Fallback gracefully on any gateway or parsing failure
        pass

    # 3. Deterministic fallback if model did not produce a valid question
    if not question_text:
        fallback_q, fallback_concepts, fallback_mods = _build_fallback_question(
            category=category,
            difficulty=difficulty,
            target_files=target_files,
            is_follow_up=is_follow_up,
            parent_question_text=parent_question_text,
            followup_focus=followup_focus,
        )
        question_text = fallback_q
        if not expected_concepts:
            expected_concepts = fallback_concepts
        if not target_modules:
            target_modules = fallback_mods

    # 4. Construct VivaQuestion domain model
    question_id = f"vq_{uuid.uuid4().hex[:12]}"
    created_at = utc_now_iso()

    question = VivaQuestion(
        question_id=question_id,
        session_id=session_id,
        turn_index=turn_index,
        category=category,
        difficulty=difficulty,
        question_text=question_text,
        target_modules=target_modules,
        target_files=target_files,
        expected_concepts=expected_concepts,
        supporting_evidence_ids=supporting_evidence_ids,
        is_follow_up=is_follow_up,
        parent_question_id=parent_question_id,
        packet_id=packet.id,
        created_at=created_at,
    )

    # 5. Persist to SQLite for restartability
    db.save_viva_question(
        question_id=question.question_id,
        session_id=question.session_id,
        turn_index=question.turn_index,
        category=question.category.value,
        difficulty=question.difficulty.value,
        question_text=question.question_text,
        target_modules=question.target_modules,
        target_files=question.target_files,
        expected_concepts=question.expected_concepts,
        supporting_evidence_ids=question.supporting_evidence_ids,
        is_follow_up=question.is_follow_up,
        parent_question_id=question.parent_question_id,
        packet_id=question.packet_id,
        created_at=question.created_at,
    )

    return question
