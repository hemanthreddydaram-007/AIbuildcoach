"""Compact human-readable summaries and subsystem extractors for Build Coach session (Milestone 12.8)."""

from typing import List, Dict, Any, Optional
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    FixStatus,
    TimelineExplanationPacket,
)
from backend.guidance.models import (
    GuidancePlan,
    GapCategory,
)
from backend.domain.models import (
    ConversationEvidenceResult,
    ClaimStatus,
)
from backend.session.models import (
    BuildCoachSession,
    SessionSummary,
    EvidenceSummary,
    VerificationSummary,
    UnderstandingSummary,
    GuidanceSummary,
)


def build_evidence_summary(evidence_result: Optional[ConversationEvidenceResult]) -> EvidenceSummary:
    """Builds aggregate evidence metrics from M11.1 / M12.4 conversation analysis."""
    if not evidence_result or not evidence_result.summary:
        return EvidenceSummary()

    summary_dict = evidence_result.summary
    status_counts = summary_dict.get("status_counts", {})
    return EvidenceSummary(
        total=summary_dict.get("total_claims", len(evidence_result.claims)),
        supported=status_counts.get(ClaimStatus.SUPPORTED, 0),
        partially_supported=status_counts.get(ClaimStatus.PARTIALLY_SUPPORTED, 0),
        unsupported=status_counts.get(ClaimStatus.UNSUPPORTED, 0),
        unknown=status_counts.get(ClaimStatus.UNKNOWN, 0),
    )


def build_verification_summary(
    packet: Optional[TimelineExplanationPacket],
    timeline: List[ObservationEvent],
) -> VerificationSummary:
    """Extracts deterministic verification status from M12.6 incident packet and observations."""
    has_passing_test = any(
        e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED"
        for e in timeline
    )

    if packet and packet.incident:
        status_val = packet.fix_status.value if hasattr(packet.fix_status, "value") else str(packet.fix_status)
        test_observed = (status_val == "VERIFIED") or has_passing_test
        details = (
            "Targeted test pass observed."
            if test_observed
            else ("Error recovered operationally without targeted test." if status_val == "RECOVERED" else "Incident remains unverified.")
        )
        return VerificationSummary(
            status=status_val,
            targeted_test_observed=test_observed,
            details=details,
        )

    if has_passing_test:
        return VerificationSummary(
            status="VERIFIED",
            targeted_test_observed=True,
            details="Automated test suite passing cleanly.",
        )

    return VerificationSummary(
        status="UNKNOWN",
        targeted_test_observed=False,
        details="No verification observations recorded.",
    )


def build_understanding_summary(
    comprehension_history: List[Dict[str, Any]],
    plan: Optional[GuidancePlan] = None,
    has_changes: bool = False,
) -> UnderstandingSummary:
    """Extracts pedagogical comprehension status from M7 history and M12.7 guidance."""
    has_understanding_gap = plan and any(g.category == GapCategory.UNDERSTANDING for g in plan.gaps)
    latest_run = comprehension_history[0] if comprehension_history else None
    latest_state = latest_run.get("overall_state") if latest_run else None

    # Required if understanding gap flagged or if recent changes exist without UNDERSTOOD evaluation
    required = bool(has_understanding_gap or (has_changes and latest_state != "UNDERSTOOD"))

    return UnderstandingSummary(
        required=required,
        latest_state=latest_state,
        gap_count=latest_run.get("gap_count", 0) if latest_run else (1 if has_understanding_gap else 0),
        action="EXPLAIN_BACK" if required else None,
    )


def build_guidance_summary(plan: Optional[GuidancePlan]) -> GuidanceSummary:
    """Extracts deterministic guidance metrics from M12.7 plan."""
    if not plan:
        return GuidanceSummary()

    return GuidanceSummary(
        status=plan.status.value,
        total_gaps=len(plan.gaps),
        total_actions=len(plan.actions),
        top_action_type=plan.top_next_action.action_type.value if plan.top_next_action else None,
        top_priority=plan.top_next_action.priority.value if plan.top_next_action else None,
    )


def build_session_summary(
    timeline: List[ObservationEvent],
    packet: Optional[TimelineExplanationPacket] = None,
    plan: Optional[GuidancePlan] = None,
    comprehension_history: Optional[List[Dict[str, Any]]] = None,
) -> SessionSummary:
    """Builds compact high-level metrics and grounded unknown factors for the session."""
    recent_changes_count = 0
    for e in timeline:
        if e.event_type == ObservationEventType.GIT_CHANGE:
            changed_files = e.payload.get("changed_files", [])
            recent_changes_count += len(changed_files) if changed_files else 1

    pkt_fix_status = (packet.fix_status.value if hasattr(packet.fix_status, "value") else str(packet.fix_status)) if packet else ""
    active_incidents = 1 if (packet and pkt_fix_status == "PERSISTING") else 0
    recovered_incidents = 1 if (packet and pkt_fix_status in ("RECOVERED", "VERIFIED")) else 0

    unknowns: List[str] = []
    if packet and pkt_fix_status == "RECOVERED":
        unknowns.append("The evidence does not prove the code change was the sole cause of recovery.")
        unknowns.append("Targeted test verification for the recovery has not been recorded.")
    elif packet and pkt_fix_status == "UNKNOWN":
        unknowns.append("No runtime or test verification observed following the incident.")

    if not comprehension_history and recent_changes_count > 0:
        unknowns.append("Developer conceptual comprehension of recent code changes has not been verified.")

    has_exec = any(
        e.event_type in (
            ObservationEventType.RUNTIME_ERROR,
            ObservationEventType.TEST_FINISHED,
            ObservationEventType.COMMAND_FINISHED,
            ObservationEventType.HTTP_RESPONSE,
        )
        for e in timeline
    )
    if recent_changes_count > 0 and not has_exec:
        unknowns.append("No runtime execution or test results have been observed for recent changes.")

    return SessionSummary(
        recent_changes=recent_changes_count,
        active_incidents=active_incidents,
        recovered_incidents=recovered_incidents,
        total_observations=len(timeline),
        unknowns=unknowns,
    )


def format_human_session(session: BuildCoachSession, project_name: str = "") -> str:
    """Formats BuildCoachSession into a clean, human-readable terminal dashboard view."""
    lines = [
        "BUILD COACH",
        "------------------------",
        "",
        f"Project: {project_name or session.project_id}",
        "",
        "State:",
        session.state.value,
        "",
    ]

    # Latest incident
    if session.active_incident:
        inc_title = session.active_incident.get("error_type") or session.active_incident.get("message") or "Active Error"
        lines.extend(["Latest incident:", inc_title, ""])
    elif session.verification_summary and session.verification_summary.status != "UNKNOWN":
        lines.extend(["Latest incident:", session.verification_summary.status.capitalize(), ""])

    # Verification
    ver_status = "Pending"
    if session.verification_summary:
        if session.verification_summary.targeted_test_observed:
            ver_status = "Verified"
        elif session.verification_summary.status == "RECOVERED":
            ver_status = "Pending"
        elif session.verification_summary.status == "PERSISTING":
            ver_status = "Failing"
    lines.extend(["Verification:", ver_status, ""])

    # Understanding
    und_status = "Pending"
    if session.understanding_summary:
        if session.understanding_summary.latest_state == "UNDERSTOOD":
            und_status = "Understood"
        elif not session.understanding_summary.required:
            und_status = "Not required"
    lines.extend(["Understanding:", und_status, ""])

    # Next Action
    if session.next_action:
        lines.extend([
            f"NEXT ACTION:",
            session.next_action.title,
            "",
            "WHY:",
            session.next_action.description,
        ])
    else:
        lines.extend([
            "NEXT ACTION:",
            "No immediate action required.",
        ])

    return "\n".join(lines)
