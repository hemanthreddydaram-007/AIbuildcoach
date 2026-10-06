"""Deterministic state determination for Build Coach sessions (Milestone 12.8)."""

from typing import List, Dict, Any, Optional
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    FixStatus,
    TimelineExplanationPacket,
)
from backend.guidance.models import (
    GuidancePlan,
    ActionType,
    ActionPriority,
    GapCategory,
)
from backend.session.models import SessionState


def compute_session_state(
    timeline: List[ObservationEvent],
    packet: Optional[TimelineExplanationPacket] = None,
    plan: Optional[GuidancePlan] = None,
    comprehension_history: Optional[List[Dict[str, Any]]] = None,
    has_uncommitted_changes: bool = False,
) -> SessionState:
    """Computes deterministic project session state strictly from verified observations and models.
    
    TRUST INVARIANT:
    State determination is 100% deterministic with ZERO LLM calls.
    
    PRECEDENCE:
    1. INSUFFICIENT EVIDENCE -> UNKNOWN
    2. ACTIVE CRITICAL ERROR / UNRESOLVED ERROR -> INVESTIGATING
    3. VERIFICATION REQUIRED -> VERIFYING
    4. UNDERSTANDING REQUIRED -> LEARNING
    5. HIGH-PRIORITY GUIDANCE -> ACTION_REQUIRED
    6. ALL IMPORTANT ACTIONS COMPLETE & VERIFIED -> STABLE
    7. CLEAN & IDLE -> READY
    """
    sorted_events = sorted(timeline, key=lambda e: (e.timestamp, e.created_at))

    # Identify execution / runtime / test observations
    runtime_or_test_events = [
        e for e in sorted_events
        if e.event_type in (
            ObservationEventType.RUNTIME_ERROR,
            ObservationEventType.TEST_FINISHED,
            ObservationEventType.COMMAND_FINISHED,
            ObservationEventType.PROCESS_STARTED,
            ObservationEventType.PROCESS_FINISHED,
            ObservationEventType.HTTP_RESPONSE,
        )
    ]

    change_events = [
        e for e in sorted_events
        if e.event_type == ObservationEventType.GIT_CHANGE
    ]

    # -------------------------------------------------------------------------
    # 1. INSUFFICIENT EVIDENCE check (UNKNOWN)
    # If there are zero events, or there are changes but ZERO runtime/test observations,
    # absence of evidence cannot be claimed as healthy.
    # -------------------------------------------------------------------------
    if not sorted_events:
        return SessionState.UNKNOWN

    if (change_events or has_uncommitted_changes) and not runtime_or_test_events:
        return SessionState.UNKNOWN

    # -------------------------------------------------------------------------
    # 2. ACTIVE CRITICAL ERROR / UNRESOLVED ERROR (INVESTIGATING)
    # Check if there is an active, persisting, or unrecovered runtime error.
    # -------------------------------------------------------------------------
    pkt_fix_status = (packet.fix_status.value if hasattr(packet.fix_status, "value") else str(packet.fix_status)) if packet else ""
    has_persisting_packet = (pkt_fix_status == "PERSISTING")
    has_investigate_action = plan and plan.top_next_action and plan.top_next_action.action_type == ActionType.INVESTIGATE_ERROR
    has_unresolved_gap = plan and any(g.category == GapCategory.UNRESOLVED_ERROR for g in plan.gaps)

    # Also inspect last runtime event directly if no packet
    last_runtime_error = None
    for e in reversed(sorted_events):
        if e.event_type == ObservationEventType.RUNTIME_ERROR:
            last_runtime_error = e
            break

    last_success = None
    for e in reversed(sorted_events):
        if (
            (e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED")
            or (e.event_type == ObservationEventType.HTTP_RESPONSE and 200 <= e.payload.get("status_code", 0) < 300)
            or (e.event_type == ObservationEventType.COMMAND_FINISHED and e.payload.get("exit_code") == 0)
        ):
            last_success = e
            break

    # An active error is one where runtime error occurred and was not superseded by a clean success
    is_active_unrecovered_error = False
    if last_runtime_error:
        if not last_success:
            is_active_unrecovered_error = True
        elif last_runtime_error.timestamp > last_success.timestamp:
            is_active_unrecovered_error = True

    if has_persisting_packet or has_investigate_action or has_unresolved_gap or is_active_unrecovered_error:
        return SessionState.INVESTIGATING

    # -------------------------------------------------------------------------
    # 3. VERIFICATION REQUIRED (VERIFYING)
    # Check if an error recovered operationally or code changed without targeted verification.
    # -------------------------------------------------------------------------
    is_recovered_without_test = (pkt_fix_status == "RECOVERED")
    is_verifying_action = plan and plan.top_next_action and plan.top_next_action.action_type in (
        ActionType.RUN_TEST,
        ActionType.CHECK_RUNTIME,
    )
    has_test_coverage_gap = plan and any(
        g.category in (GapCategory.TEST_COVERAGE, GapCategory.VERIFICATION)
        for g in plan.gaps
    )

    if is_recovered_without_test or is_verifying_action or has_test_coverage_gap:
        return SessionState.VERIFYING

    # -------------------------------------------------------------------------
    # 4. UNDERSTANDING REQUIRED (LEARNING)
    # Verification is satisfied/clean, but understanding gap is present.
    # -------------------------------------------------------------------------
    is_explain_action = plan and plan.top_next_action and plan.top_next_action.action_type == ActionType.EXPLAIN_BACK
    has_understanding_gap = plan and any(g.category == GapCategory.UNDERSTANDING for g in plan.gaps)

    # Check comprehension history: if recent changes exist and developer has not yet demonstrated UNDERSTOOD
    comp_not_understood = False
    if change_events or has_uncommitted_changes:
        if not comprehension_history:
            comp_not_understood = True
        elif comprehension_history[0].get("overall_state") != "UNDERSTOOD":
            comp_not_understood = True

    if is_explain_action or has_understanding_gap or comp_not_understood:
        return SessionState.LEARNING

    # -------------------------------------------------------------------------
    # 5. HIGH-PRIORITY GUIDANCE (ACTION_REQUIRED)
    # Other high-priority actions exist (e.g. diff review, file inspection, dependency).
    # -------------------------------------------------------------------------
    if plan and plan.top_next_action and plan.top_next_action.priority in (ActionPriority.CRITICAL, ActionPriority.HIGH):
        return SessionState.ACTION_REQUIRED

    # -------------------------------------------------------------------------
    # 6. ALL IMPORTANT ACTIONS COMPLETE & VERIFIED (STABLE)
    # Verification verified, tests passing, no unresolved errors or high gaps.
    # -------------------------------------------------------------------------
    has_passing_test = any(
        e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED"
        for e in sorted_events
    )
    is_verified_incident = (pkt_fix_status == "VERIFIED")

    if has_passing_test or is_verified_incident:
        return SessionState.STABLE

    # -------------------------------------------------------------------------
    # 7. CLEAN & IDLE (READY or STABLE)
    # If there are no pending gaps and runtime has executed cleanly
    # -------------------------------------------------------------------------
    if plan and not plan.gaps:
        return SessionState.STABLE

    return SessionState.READY
