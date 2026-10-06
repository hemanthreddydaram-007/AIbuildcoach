"""Coordinating service for Milestone 12.7 Knowledge Gap & Next Action Engine."""

import hashlib
import json
from typing import List, Dict, Any, Optional
from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    FixStatus,
    TimelineExplanationPacket,
    IncidentExplanation,
)
from backend.guidance.models import (
    KnowledgeGap,
    NextAction,
    GuidancePlan,
    GuidanceStatus,
    ActionStatus,
    ActionType,
)
from backend.guidance.detector import KnowledgeGapDetector
from backend.guidance.planner import NextActionPlanner
from backend.guidance.ranking import ActionRanker


def compute_state_fingerprint(events: List[ObservationEvent], incident_id: Optional[str] = None) -> str:
    """Computes a deterministic hash over recent observation event IDs and timestamps."""
    sorted_events = sorted(events, key=lambda e: (e.timestamp, e.created_at))
    summary_parts = [f"{e.event_id}:{e.timestamp}:{e.event_type}" for e in sorted_events[-20:]]
    if incident_id:
        summary_parts.append(f"incident:{incident_id}")
    raw = "|".join(summary_parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class GuidanceService:
    """Coordinates gap detection, action planning, completion verification, and guidance lifecycle.
    
    TRUST INVARIANTS:
    1. Zero autonomous execution: recommends actions for human developers to perform.
    2. Zero random outputs: deterministic for identical project states.
    3. Re-evaluation loop: recalculates guidance upon new observations. Satisfied completion conditions retire previous actions.
    4. Project boundary isolation: never mixes observations or gaps across distinct projects.
    """

    def __init__(self, db: Database, observation_service: Optional[ObservationService] = None):
        self.db = db
        self.observation_service = observation_service or ObservationService(db)
        self.detector = KnowledgeGapDetector()
        self.planner = NextActionPlanner()
        self.ranker = ActionRanker()

    def generate_plan(
        self,
        project_id: str,
        incident_id: Optional[str] = None,
        cached_plan: Optional[GuidancePlan] = None,
    ) -> GuidancePlan:
        """Generates or recalculates a deterministic GuidancePlan for the project."""
        # 1. Verify project exists
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found in registry: '{project_id}'")

        # 2. Retrieve chronological timeline
        timeline = self.observation_service.get_timeline(project_id)

        # 3. Compute current state fingerprint
        current_fingerprint = compute_state_fingerprint(timeline, incident_id=incident_id)

        # 4. Obtain incident explanation packet if any incident exists
        packet: Optional[TimelineExplanationPacket] = None
        try:
            packet = self.observation_service.get_incident_explanation_packet(project_id, incident_id=incident_id)
        except Exception:
            packet = None

        # 5. Retrieve comprehension history if available
        comprehension_history = self._get_comprehension_history(project_id)

        # 6. Detect evidence-backed gaps
        gaps = self.detector.detect_gaps(
            project_id=project_id,
            events=timeline,
            packet=packet,
            comprehension_history=comprehension_history,
        )

        # 7. Convert gaps into candidate actions
        candidate_actions = self.planner.plan_actions(
            project_id=project_id,
            gaps=gaps,
            incident_id=incident_id,
        )

        # 8. Check action completions against current observation timeline
        candidate_actions = self._evaluate_action_completions(candidate_actions, timeline, packet)

        # 9. Rank candidate actions deterministically
        top_action, secondary_actions = self.ranker.rank_actions(candidate_actions)

        plan_id = f"plan_{project_id}_{current_fingerprint[:8]}"
        return GuidancePlan(
            plan_id=plan_id,
            project_id=project_id,
            incident_id=incident_id,
            status=GuidanceStatus.CURRENT,
            state_fingerprint=current_fingerprint,
            top_next_action=top_action,
            secondary_actions=secondary_actions,
            gaps=gaps,
            actions=candidate_actions,
            generated_at=utc_now_iso(),
        )

    def check_plan_staleness(self, plan: GuidancePlan) -> bool:
        """Determines if new observations have arrived since the plan was generated."""
        timeline = self.observation_service.get_timeline(plan.project_id)
        current_fp = compute_state_fingerprint(timeline, incident_id=plan.incident_id)
        return current_fp != plan.state_fingerprint

    def _evaluate_action_completions(
        self,
        actions: List[NextAction],
        timeline: List[ObservationEvent],
        packet: Optional[TimelineExplanationPacket],
    ) -> List[NextAction]:
        """Evaluates whether observable state in timeline satisfies completion conditions."""
        sorted_events = sorted(timeline, key=lambda e: (e.timestamp, e.created_at))

        for action in actions:
            # 1. RUN_TEST: completed if post-incident passing test exists
            if action.action_type == ActionType.RUN_TEST:
                # If packet already indicates VERIFIED fix_status, test has passed
                if packet and packet.fix_status == FixStatus.VERIFIED:
                    action.status = ActionStatus.COMPLETED
                else:
                    # Check if any TEST_FINISHED with status PASSED exists after latest error
                    last_err = [e for e in sorted_events if e.event_type == ObservationEventType.RUNTIME_ERROR]
                    if last_err:
                        t_err = last_err[-1].timestamp
                        has_pass = any(
                            e.event_type == ObservationEventType.TEST_FINISHED
                            and e.timestamp > t_err
                            and e.payload.get("status") == "PASSED"
                            for e in sorted_events
                        )
                        if has_pass:
                            action.status = ActionStatus.COMPLETED

            # 2. CHECK_RUNTIME: completed if clean run observed
            elif action.action_type == ActionType.CHECK_RUNTIME:
                if packet and packet.fix_status in (FixStatus.VERIFIED, FixStatus.RECOVERED):
                    action.status = ActionStatus.COMPLETED
                else:
                    last_err = [e for e in sorted_events if e.event_type == ObservationEventType.RUNTIME_ERROR]
                    if last_err:
                        t_err = last_err[-1].timestamp
                        has_clean_run = any(
                            e.timestamp > t_err
                            and (
                                (e.event_type == ObservationEventType.COMMAND_FINISHED and e.payload.get("exit_code") == 0)
                                or (e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED")
                                or (e.event_type == ObservationEventType.HTTP_RESPONSE and 200 <= e.payload.get("status_code", 0) < 300)
                            )
                            for e in sorted_events
                        )
                        if has_clean_run:
                            action.status = ActionStatus.COMPLETED

            # 3. INVESTIGATE_ERROR: completed if error disappeared or was verified
            elif action.action_type == ActionType.INVESTIGATE_ERROR:
                if packet and packet.fix_status in (FixStatus.VERIFIED, FixStatus.RECOVERED):
                    action.status = ActionStatus.COMPLETED

            # 4. EXPLAIN_BACK: completed if comprehension run passed
            elif action.action_type == ActionType.EXPLAIN_BACK:
                comp_history = self._get_comprehension_history(action.project_id)
                if any(r.get("overall_state") == "UNDERSTOOD" for r in comp_history):
                    action.status = ActionStatus.COMPLETED

        return actions

    def _get_comprehension_history(self, project_id: str) -> List[Dict[str, Any]]:
        """Reads operational comprehension run records from DB (M7 table)."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT run_id, project_id, overall_state, gap_count, created_at FROM comprehension_runs WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                )
                rows = cursor.fetchall()
                return [
                    {
                        "run_id": r[0],
                        "project_id": r[1],
                        "overall_state": r[2],
                        "gap_count": r[3],
                        "created_at": r[4],
                    }
                    for r in rows
                ]
        except Exception:
            return []
