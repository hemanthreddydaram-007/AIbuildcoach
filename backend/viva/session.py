"""Session Orchestrator for Milestone 8: Viva Defence Engine.
Manages viva examination lifecycle, turn transitions, adaptive progression, and crash recovery.
"""

from datetime import datetime, timezone
import uuid
from typing import List, Optional, Tuple, Dict, Any

from backend.domain.models import ContextRequest, ContextPurpose, utc_now_iso
from backend.context_engine.engine import ContextEngine
from backend.ai_gateway.models import ConsentToken
from backend.ai_gateway.gateway import AIGateway
from backend.project_model.db import Database
from backend.viva.models import (
    VivaCategory,
    VivaDifficulty,
    VivaRating,
    VivaSessionStatus,
    VivaSessionMode,
    VivaQuestion,
    VivaAnswerSubmission,
    VivaTurnEvaluation,
    VivaSessionRecord,
)
from backend.viva.exceptions import (
    VivaSessionNotFoundError,
    InvalidTurnProgressionError,
    ConcurrentSessionError,
    TurnLimitExceededError,
)
from backend.viva.index import ProjectArchitecturalIndex
from backend.viva.generator import generate_viva_question
from backend.viva.evaluator import evaluate_viva_answer
from backend.viva.reporter import compile_viva_report


MAX_BASE_QUESTIONS = 5
MAX_FOLLOWUP_PER_BASE = 1
MAX_FOLLOWUPS_PER_SESSION = 3
MAX_TOTAL_TURNS = 8
STALE_RECOVERY_TIMEOUT_SECONDS = 180

DIFFICULTY_TIERS = [
    VivaDifficulty.EASY,
    VivaDifficulty.MEDIUM,
    VivaDifficulty.HARD,
    VivaDifficulty.DEEP,
]


def promote_difficulty(curr: VivaDifficulty) -> VivaDifficulty:
    """Promotes difficulty tier on strong candidate answers."""
    idx = DIFFICULTY_TIERS.index(curr)
    if idx < len(DIFFICULTY_TIERS) - 1:
        return DIFFICULTY_TIERS[idx + 1]
    return curr


def demote_difficulty(curr: VivaDifficulty) -> VivaDifficulty:
    """Demotes difficulty tier on weak answers."""
    idx = DIFFICULTY_TIERS.index(curr)
    if idx > 0:
        return DIFFICULTY_TIERS[idx - 1]
    return curr


def recover_stale_session_if_needed(db: Database, session_id: str) -> Dict[str, Any]:
    """Recovers a session stranded in EVALUATING beyond the timeout.
    viva_turns.rating records as UNKNOWN (no FAILED rating in enum),
    while session status transitions to FAILED.
    """
    session_data = db.get_viva_session(session_id)
    if not session_data:
        raise VivaSessionNotFoundError(f"Viva session {session_id} not found.")

    if session_data["status"] == VivaSessionStatus.EVALUATING.value:
        try:
            updated_at = datetime.fromisoformat(session_data["updated_at"])
            now = datetime.now(timezone.utc)
            if (now - updated_at).total_seconds() > STALE_RECOVERY_TIMEOUT_SECONDS:
                turn_idx = session_data["current_turn"]
                existing_turns = db.get_viva_turns_for_session(session_id)
                turn_recorded = any(t["turn_index"] == turn_idx for t in existing_turns)
                if not turn_recorded:
                    q = db.get_viva_question(session_id, turn_idx)
                    q_cat = q["category"] if q else VivaCategory.ARCHITECTURE_OVERVIEW.value
                    q_diff = q["difficulty"] if q else session_data["current_difficulty"]
                    q_is_followup = q["is_follow_up"] if q else False
                    if q:
                        q_id = q["question_id"]
                    else:
                        q_id = f"vq_stale_{turn_idx}"
                        db.save_viva_question(
                            question_id=q_id,
                            session_id=session_id,
                            turn_index=turn_idx,
                            category=q_cat,
                            difficulty=q_diff,
                            question_text="Stale evaluation fallback question",
                            target_modules=[],
                            target_files=[],
                            expected_concepts=[],
                            supporting_evidence_ids=[],
                            is_follow_up=q_is_followup,
                            parent_question_id=None,
                            packet_id="pkt_stale",
                            created_at=utc_now_iso(),
                        )

                    db.record_viva_turn(
                        turn_id=f"ve_stale_{uuid.uuid4().hex[:8]}",
                        session_id=session_id,
                        turn_index=turn_idx,
                        question_id=q_id,
                        category=q_cat,
                        difficulty=q_diff,
                        rating=VivaRating.UNKNOWN.value,
                        is_project_grounded=False,
                        gap_count=0,
                        is_follow_up=q_is_followup,
                        evaluated_at=utc_now_iso(),
                    )
                db.update_viva_session(session_id, status=VivaSessionStatus.AWAITING_ANSWER.value)
                session_data = db.get_viva_session(session_id)
        except Exception:
            pass

    return session_data


def start_viva_session(
    db: Database,
    project_id: str,
    mode: VivaSessionMode = VivaSessionMode.PROJECT_WIDE,
    target_categories: Optional[List[VivaCategory]] = None,
    initial_difficulty: VivaDifficulty = VivaDifficulty.EASY,
    index: Optional[ProjectArchitecturalIndex] = None,
    context_engine: Optional[ContextEngine] = None,
    gateway: Optional[AIGateway] = None,
    consent_token: Optional[ConsentToken] = None,
    explicit_api_key: Optional[str] = None,
) -> Tuple[VivaSessionRecord, VivaQuestion]:
    """Initializes a new viva session and generates the first question."""
    session_id = f"vs_{uuid.uuid4().hex[:12]}"
    now_iso = utc_now_iso()

    # Determine target categories
    if target_categories and len(target_categories) > 0:
        resolved_categories = target_categories
    elif index and len(index.get_available_categories()) > 0:
        resolved_categories = index.get_available_categories()
    else:
        resolved_categories = list(VivaCategory)

    first_cat = resolved_categories[0]

    # Create session in SQLite
    db.create_viva_session(
        session_id=session_id,
        project_id=project_id,
        mode=mode.value,
        initial_difficulty=initial_difficulty.value,
        target_categories=[c.value for c in resolved_categories],
        started_at=now_iso,
    )

    # Instantiate fallbacks if components not provided
    active_index = index or ProjectArchitecturalIndex(files=[], graph=None)
    active_engine = context_engine or ContextEngine(db)
    active_gateway = gateway or AIGateway(db=db)
    active_consent = consent_token or ConsentToken(
        token_id=f"token_{uuid.uuid4().hex[:8]}",
        user_id="viva_student",
        purpose="Milestone 8 Viva Defence",
        packet_id="",
        packet_hash="",
        created_at=now_iso,
    )

    # Generate initial viva question (turn 0)
    first_question = generate_viva_question(
        project_id=project_id,
        session_id=session_id,
        turn_index=0,
        category=first_cat,
        difficulty=initial_difficulty,
        index=active_index,
        context_engine=active_engine,
        gateway=active_gateway,
        consent_token=active_consent,
        db=db,
        explicit_api_key=explicit_api_key,
        is_follow_up=False,
    )

    # Update session to AWAITING_ANSWER
    db.update_viva_session(
        session_id=session_id,
        status=VivaSessionStatus.AWAITING_ANSWER.value,
    )

    session_record = VivaSessionRecord(
        session_id=session_id,
        project_id=project_id,
        status=VivaSessionStatus.AWAITING_ANSWER,
        mode=mode,
        current_turn=0,
        base_questions_asked=0,
        followups_asked=0,
        current_difficulty=initial_difficulty,
        target_categories=resolved_categories,
        evaluated_categories=[],
        started_at=now_iso,
        updated_at=now_iso,
    )

    return session_record, first_question


def get_current_question(db: Database, session_id: str) -> Optional[VivaQuestion]:
    """Retrieves the active question for the current turn in the session."""
    session_data = db.get_viva_session(session_id)
    if not session_data:
        raise VivaSessionNotFoundError(f"Viva session {session_id} not found.")

    current_turn = session_data["current_turn"]
    q_data = db.get_viva_question(session_id, current_turn)
    if not q_data:
        return None

    return VivaQuestion(
        question_id=q_data["question_id"],
        session_id=session_id,
        turn_index=q_data["turn_index"],
        category=VivaCategory(q_data["category"]),
        difficulty=VivaDifficulty(q_data["difficulty"]),
        question_text=q_data["question_text"],
        target_modules=q_data["target_modules"],
        target_files=q_data["target_files"],
        expected_concepts=q_data["expected_concepts"],
        supporting_evidence_ids=q_data["supporting_evidence_ids"],
        is_follow_up=q_data["is_follow_up"],
        parent_question_id=q_data["parent_question_id"],
        packet_id=q_data["packet_id"],
        created_at=q_data["created_at"],
    )


def submit_viva_answer_and_step(
    db: Database,
    session_id: str,
    submission: VivaAnswerSubmission,
    index: ProjectArchitecturalIndex,
    context_engine: ContextEngine,
    gateway: AIGateway,
    consent_token: ConsentToken,
    explicit_api_key: Optional[str] = None,
) -> Tuple[VivaTurnEvaluation, Optional[VivaQuestion], VivaSessionRecord]:
    """Evaluates the submitted answer, determines progression, and returns next question (if any)."""
    # 1. Recover stale session if stranded in EVALUATING
    session_data = recover_stale_session_if_needed(db, session_id)

    # 2. Validate current turn and active question
    current_turn = session_data["current_turn"]
    if submission.turn_index != current_turn:
        raise InvalidTurnProgressionError(
            f"Submission turn {submission.turn_index} does not match active session turn {current_turn}."
        )

    # 3. Retrieve active question
    q_data = db.get_viva_question(session_id, current_turn)
    if not q_data:
        raise InvalidTurnProgressionError(f"No question found for turn {current_turn} in session {session_id}.")

    if submission.question_id != q_data["question_id"]:
        raise InvalidTurnProgressionError(
            f"Submission question_id {submission.question_id} does not match active question {q_data['question_id']}."
        )

    # 4. Atomically transition session from AWAITING_ANSWER to EVALUATING (require exactly 1 affected row)
    transitioned = db.atomic_transition_viva_session_to_evaluating(session_id)
    if not transitioned:
        fresh_data = db.get_viva_session(session_id)
        current_status = fresh_data["status"] if fresh_data else "UNKNOWN"
        if current_status == VivaSessionStatus.EVALUATING.value:
            raise ConcurrentSessionError(f"Session {session_id} is already evaluating an answer.")
        else:
            raise InvalidTurnProgressionError(
                f"Session is in {current_status} state, expected AWAITING_ANSWER."
            )

    question = VivaQuestion(
        question_id=q_data["question_id"],
        session_id=session_id,
        turn_index=q_data["turn_index"],
        category=VivaCategory(q_data["category"]),
        difficulty=VivaDifficulty(q_data["difficulty"]),
        question_text=q_data["question_text"],
        target_modules=q_data["target_modules"],
        target_files=q_data["target_files"],
        expected_concepts=q_data["expected_concepts"],
        supporting_evidence_ids=q_data["supporting_evidence_ids"],
        is_follow_up=q_data["is_follow_up"],
        parent_question_id=q_data["parent_question_id"],
        packet_id=q_data["packet_id"],
        created_at=q_data["created_at"],
    )

    # 5. Assemble context packet for the question category
    target_files = index.get_category_files(question.category, limit=5)
    packet = context_engine.build_context_packet(
        ContextRequest(
            project_id=session_data["project_id"],
            purpose=ContextPurpose.PROJECT_OVERVIEW,
            target_files=target_files,
            budget_tokens=4000,
        )
    )

    # 6. Evaluate with terminal failure guarantee
    try:
        evaluation = evaluate_viva_answer(
            db=db,
            session_id=session_id,
            submission=submission,
            question=question,
            packet=packet,
            consent_token=consent_token,
            gateway=gateway,
            explicit_api_key=explicit_api_key,
        )
    except Exception:
        # Failure recovery: record turn as UNKNOWN and mark session as FAILED
        try:
            db.record_viva_turn(
                turn_id=f"ve_fail_{uuid.uuid4().hex[:8]}",
                session_id=session_id,
                turn_index=current_turn,
                question_id=question.question_id,
                category=question.category.value,
                difficulty=question.difficulty.value,
                rating=VivaRating.UNKNOWN.value,
                is_project_grounded=False,
                gap_count=0,
                is_follow_up=question.is_follow_up,
                evaluated_at=utc_now_iso(),
            )
            db.update_viva_session(session_id, status=VivaSessionStatus.FAILED.value)
        except Exception:
            pass
        raise

    # 7. Progression & Adaptive Stepping
    current_difficulty = VivaDifficulty(session_data["current_difficulty"])
    if evaluation.rating == VivaRating.STRONG:
        next_difficulty = promote_difficulty(current_difficulty)
    elif evaluation.rating == VivaRating.WEAK:
        next_difficulty = demote_difficulty(current_difficulty)
    else:
        next_difficulty = current_difficulty

    base_asked = session_data["base_questions_asked"]
    followups_asked = session_data["followups_asked"]
    target_categories = [VivaCategory(c) for c in session_data["target_categories"]]

    # Follow-up determination
    can_followup = (
        not question.is_follow_up
        and evaluation.rating in (VivaRating.WEAK, VivaRating.PARTIAL)
        and followups_asked < MAX_FOLLOWUPS_PER_SESSION
        and (current_turn + 1) < MAX_TOTAL_TURNS
    )

    if not question.is_follow_up:
        base_asked += 1

    next_question: Optional[VivaQuestion] = None

    if can_followup:
        # Issue adaptive follow-up
        followups_asked += 1
        next_turn = current_turn + 1
        followup_focus = None
        if evaluation.gaps:
            followup_focus = evaluation.gaps[0].expected_understanding

        next_question = generate_viva_question(
            project_id=session_data["project_id"],
            session_id=session_id,
            turn_index=next_turn,
            category=question.category,
            difficulty=next_difficulty,
            index=index,
            context_engine=context_engine,
            gateway=gateway,
            consent_token=consent_token,
            db=db,
            explicit_api_key=explicit_api_key,
            is_follow_up=True,
            parent_question_id=question.question_id,
            parent_question_text=question.question_text,
            followup_focus=followup_focus,
        )

        db.update_viva_session(
            session_id=session_id,
            status=VivaSessionStatus.AWAITING_ANSWER.value,
            current_turn=next_turn,
            base_questions_asked=base_asked,
            followups_asked=followups_asked,
            current_difficulty=next_difficulty.value,
        )

    else:
        # Check if session has concluded
        is_completed = (
            base_asked >= min(MAX_BASE_QUESTIONS, len(target_categories))
            or (current_turn + 1) >= MAX_TOTAL_TURNS
        )

        if is_completed:
            db.update_viva_session(
                session_id=session_id,
                status=VivaSessionStatus.COMPLETED.value,
                current_turn=current_turn + 1,
                base_questions_asked=base_asked,
                current_difficulty=next_difficulty.value,
                completed_at=utc_now_iso(),
            )
            # Compile final defence report
            compile_viva_report(db, session_id, index=index)
            next_question = None

        else:
            # Advance to next base category
            all_turns = db.get_viva_turns_for_session(session_id)
            evaluated_cats = {t["category"] for t in all_turns}
            remaining_cats = [c for c in target_categories if c.value not in evaluated_cats]
            if remaining_cats:
                next_cat = remaining_cats[0]
            else:
                next_cat = target_categories[base_asked % len(target_categories)]

            next_turn = current_turn + 1
            next_question = generate_viva_question(
                project_id=session_data["project_id"],
                session_id=session_id,
                turn_index=next_turn,
                category=next_cat,
                difficulty=next_difficulty,
                index=index,
                context_engine=context_engine,
                gateway=gateway,
                consent_token=consent_token,
                db=db,
                explicit_api_key=explicit_api_key,
                is_follow_up=False,
            )

            db.update_viva_session(
                session_id=session_id,
                status=VivaSessionStatus.AWAITING_ANSWER.value,
                current_turn=next_turn,
                base_questions_asked=base_asked,
                current_difficulty=next_difficulty.value,
            )

    # 8. Reload session record
    updated_data = db.get_viva_session(session_id)
    all_turns = db.get_viva_turns_for_session(session_id)
    evaluated_cats = list({VivaCategory(t["category"]) for t in all_turns})

    session_record = VivaSessionRecord(
        session_id=session_id,
        project_id=updated_data["project_id"],
        status=VivaSessionStatus(updated_data["status"]),
        mode=VivaSessionMode(updated_data["mode"]),
        current_turn=updated_data["current_turn"],
        base_questions_asked=updated_data["base_questions_asked"],
        followups_asked=updated_data["followups_asked"],
        current_difficulty=VivaDifficulty(updated_data["current_difficulty"]),
        target_categories=[VivaCategory(c) for c in updated_data["target_categories"]],
        evaluated_categories=evaluated_cats,
        started_at=updated_data["started_at"],
        updated_at=updated_data["updated_at"],
        completed_at=updated_data.get("completed_at"),
    )

    return evaluation, next_question, session_record


def get_session_state(db: Database, session_id: str) -> VivaSessionRecord:
    """Retrieves the current state record of a viva session."""
    session_data = db.get_viva_session(session_id)
    if not session_data:
        raise VivaSessionNotFoundError(f"Viva session {session_id} not found.")

    all_turns = db.get_viva_turns_for_session(session_id)
    evaluated_cats = list({VivaCategory(t["category"]) for t in all_turns})

    return VivaSessionRecord(
        session_id=session_id,
        project_id=session_data["project_id"],
        status=VivaSessionStatus(session_data["status"]),
        mode=VivaSessionMode(session_data["mode"]),
        current_turn=session_data["current_turn"],
        base_questions_asked=session_data["base_questions_asked"],
        followups_asked=session_data["followups_asked"],
        current_difficulty=VivaDifficulty(session_data["current_difficulty"]),
        target_categories=[VivaCategory(c) for c in session_data["target_categories"]],
        evaluated_categories=evaluated_cats,
        started_at=session_data["started_at"],
        updated_at=session_data["updated_at"],
        completed_at=session_data.get("completed_at"),
    )
