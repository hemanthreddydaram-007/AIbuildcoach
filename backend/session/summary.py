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


def _get_event_field(e: Any, field_name: str, default: Any = None) -> Any:
    """Safely extracts field from either a dict or an ObservationEvent object."""
    if isinstance(e, dict):
        return e.get(field_name, default)
    return getattr(e, field_name, default)


def format_event_label(e: Any) -> str:
    """Formats an ObservationEvent or dict into a concise human-readable activity line (M12.11)."""
    et = _get_event_field(e, "event_type")
    et_str = et.value if hasattr(et, "value") else str(et or "")
    p = _get_event_field(e, "payload") or {}

    if et_str == "GIT_CHANGE":
        files = p.get("changed_files", [])
        return f"File changed ({', '.join(files[:2])}{'...' if len(files) > 2 else ''})" if files else "File changed"
    elif et_str == "RUNTIME_ERROR":
        err = p.get("error_kind") or p.get("message") or "Runtime error"
        return f"Runtime error: {err}"
    elif et_str == "TEST_FINISHED":
        st = p.get("status", "FINISHED")
        tname = p.get("test_name", "test")
        return f"Test {st.lower()} ({tname})"
    elif et_str == "COMMAND_FINISHED":
        cmd = p.get("command", "command")
        code = p.get("exit_code", 0)
        return f"Command exit {code} ({cmd})"
    elif et_str == "PROCESS_STARTED":
        return f"Process started: {p.get('process_name', 'process')}"
    elif et_str == "PROCESS_FINISHED":
        return f"Process finished: {p.get('process_name', 'process')}"
    elif et_str == "HTTP_RESPONSE":
        return f"HTTP {p.get('status_code', '')} {p.get('endpoint', '')}"
    return et_str or "Unknown event"


def build_workflow_elements(
    timeline: List[ObservationEvent],
    packet: Optional[TimelineExplanationPacket],
    explanation: Optional[Any],
    verification_summary: VerificationSummary,
    summary: SessionSummary,
) -> Dict[str, Any]:
    """Builds deterministic M12.11 workflow view fields answering the 5+1 core questions."""
    # 1. WHAT HAPPENED?
    if explanation and getattr(explanation, "summary", None):
        what_happened = explanation.summary
    elif packet and packet.incident:
        err_kind = packet.incident.get("error_type") or packet.incident.get("message") or "RuntimeError"
        loc = packet.incident.get("location")
        loc_str = ""
        if isinstance(loc, dict) and loc.get("file_path"):
            loc_str = f" in {loc.get('file_path')}"
            if loc.get("line_number"):
                loc_str += f":{loc.get('line_number')}"
        msg = packet.incident.get("message", "")
        what_happened = f"A runtime error ({err_kind}) occurred{loc_str}{': ' + msg if msg and msg != err_kind else ''}."
    elif any(e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED" for e in timeline):
        what_happened = "All automated tests passing cleanly. No active incidents detected."
    elif len(timeline) > 0:
        what_happened = "No active runtime incidents detected for recent activity."
    else:
        what_happened = "Project initialized. Awaiting initial commands or observations."

    # 2. IS IT FIXED? (FixStatus)
    if packet and packet.fix_status:
        fix_status = packet.fix_status.value if hasattr(packet.fix_status, "value") else str(packet.fix_status)
    elif verification_summary.targeted_test_observed:
        fix_status = "VERIFIED"
    elif verification_summary.status in ("RECOVERED", "PERSISTING", "UNKNOWN"):
        fix_status = verification_summary.status
    else:
        fix_status = "UNKNOWN"

    # 3. HOW DO WE KNOW?
    how_do_we_know: List[str] = []
    if explanation and getattr(explanation, "observed_sequence", None):
        how_do_we_know = [f"✓ {item.statement}" for item in explanation.observed_sequence]
    elif packet and packet.timeline:
        items = []
        for evt in packet.timeline:
            et = _get_event_field(evt, "event_type")
            et_str = et.value if hasattr(et, "value") else str(et or "")
            p = _get_event_field(evt, "payload") or {}
            if et_str == "RUNTIME_ERROR":
                items.append(f"✓ Error observed: {p.get('error_kind') or p.get('message') or 'Runtime error'}")
            elif et_str == "GIT_CHANGE":
                ch = p.get("changed_files", [])
                items.append(f"✓ Code change observed: {', '.join(ch[:2]) if ch else 'files modified'}")
            elif et_str == "PROCESS_STARTED":
                items.append(f"✓ Process started: {p.get('process_name', 'process')}")
            elif et_str == "PROCESS_FINISHED":
                items.append(f"✓ Process finished: {p.get('process_name', 'process')}")
            elif et_str == "TEST_FINISHED":
                st = p.get("status", "FINISHED")
                items.append(f"✓ Test {st.lower()} ({p.get('test_name', 'test')})")
            elif et_str == "COMMAND_FINISHED":
                items.append(f"✓ Command finished ({p.get('command', '')})")
        how_do_we_know = items if items else ["✓ No incident events recorded."]
    else:
        items = []
        for evt in reversed(timeline[-5:]):
            et = _get_event_field(evt, "event_type")
            et_str = et.value if hasattr(et, "value") else str(et or "")
            p = _get_event_field(evt, "payload") or {}
            if et_str == "TEST_FINISHED" and p.get("status") == "PASSED":
                items.append(f"✓ Automated test passed ({p.get('test_name', 'test')})")
            elif et_str == "RUNTIME_ERROR":
                items.append(f"✓ Error observed: {p.get('error_kind') or p.get('message')}")
            elif et_str == "GIT_CHANGE":
                items.append("✓ Code change observed")
        how_do_we_know = items if items else ["✓ No runtime errors observed in session."]

    # 4. Evidence chain
    evts = packet.timeline if (packet and packet.timeline) else timeline[-10:]
    evidence_chain = []
    for e in evts:
        eid = _get_event_field(e, "event_id", "")
        ts = _get_event_field(e, "timestamp", "")
        et = _get_event_field(e, "event_type")
        et_str = et.value if hasattr(et, "value") else str(et or "")
        src = _get_event_field(e, "source")
        src_str = src.value if hasattr(src, "value") else str(src or "")
        evidence_chain.append({
            "event_id": eid,
            "timestamp": ts,
            "event_type": et_str,
            "source": src_str,
            "summary": format_event_label(e),
        })

    # 5. Recent activity (Section 14: compact chronological timeline)
    recent_activity = []
    for e in timeline[-8:]:
        eid = _get_event_field(e, "event_id", "")
        ts = _get_event_field(e, "timestamp", "")
        et = _get_event_field(e, "event_type")
        et_str = et.value if hasattr(et, "value") else str(et or "")
        src = _get_event_field(e, "source")
        src_str = src.value if hasattr(src, "value") else str(src or "")
        recent_activity.append({
            "event_id": eid,
            "timestamp": ts,
            "time_short": ts[11:16] if len(ts) >= 16 else ts,
            "event_type": et_str,
            "source": src_str,
            "summary": format_event_label(e),
        })

    return {
        "what_happened": what_happened,
        "fix_status": fix_status,
        "how_do_we_know": how_do_we_know,
        "evidence_chain": evidence_chain,
        "recent_activity": recent_activity,
    }



def format_human_session(session: BuildCoachSession, project_name: str = "") -> str:
    """Formats BuildCoachSession into a clean, human-readable terminal dashboard view (M12.11)."""
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
    inc_title = "None"
    if session.active_incident:
        inc_title = session.active_incident.get("error_type") or session.active_incident.get("message") or "Active Error"
    elif session.verification_summary and session.verification_summary.status != "UNKNOWN":
        inc_title = session.verification_summary.status.capitalize()
    lines.extend(["Latest incident:", inc_title, ""])

    # Status / FixStatus (Question 2: Is it fixed?)
    fix_status_label = session.fix_status
    if session.fix_status == "RECOVERED":
        fix_status_label = "RECOVERED (Previous error no longer observed; verification pending)"
    elif session.fix_status == "VERIFIED":
        fix_status_label = "VERIFIED (Targeted verification observed passing cleanly)"
    elif session.fix_status == "PERSISTING":
        fix_status_label = "PERSISTING (Failure continues to be observed)"
    elif session.fix_status == "UNKNOWN":
        fix_status_label = "UNKNOWN (Insufficient observation data)"
    lines.extend(["STATUS (IS IT FIXED?):", fix_status_label, ""])

    # What Happened?
    lines.extend(["WHAT HAPPENED?", session.what_happened or "No active incidents detected.", ""])

    # How Do We Know?
    lines.append("HOW DO WE KNOW?")
    if session.how_do_we_know:
        for item in session.how_do_we_know:
            lines.append(item if item.startswith("✓") else f"✓ {item}")
    else:
        lines.append("✓ No runtime failures observed.")
    lines.append("")

    # Still Unknown
    lines.append("STILL UNKNOWN:")
    if session.summary.unknowns:
        for u in session.summary.unknowns:
            lines.append(f"- {u}")
    else:
        lines.append("- No critical unknown factors identified.")
    lines.append("")

    # Next Action
    if session.next_action:
        lines.extend([
            "NEXT ACTION:",
            session.next_action.title,
            "",
            "WHY:",
            session.next_action.description,
        ])
        if session.next_action.priority:
            lines.extend(["", f"Priority: {session.next_action.priority.value}"])
    else:
        lines.extend([
            "NEXT ACTION:",
            "No immediate action required.",
        ])
    lines.append("")

    # Understanding / Do I Understand?
    und_status = "Pending"
    if session.understanding_summary:
        if session.understanding_summary.latest_state == "UNDERSTOOD":
            und_status = "Understood (Strong)"
        elif session.understanding_summary.latest_state in ("PARTIALLY_UNDERSTOOD", "MISUNDERSTOOD"):
            und_status = f"Review needed ({session.understanding_summary.latest_state})"
        elif not session.understanding_summary.required:
            und_status = "Not required"
    lines.extend(["DO I UNDERSTAND?", und_status, ""])

    # Recent Activity
    if session.recent_activity:
        lines.append("RECENT ACTIVITY:")
        for act in session.recent_activity:
            t_str = act.get("time_short") or act.get("timestamp", "")[-8:]
            lines.append(f"{t_str}  {act.get('summary', '')}")
        lines.append("")

    return "\n".join(lines).strip()

