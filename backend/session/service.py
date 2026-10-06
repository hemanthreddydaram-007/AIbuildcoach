"""Unified Build Coach Session service orchestrating existing subsystems (Milestone 12.8)."""

from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    TimelineExplanationPacket,
    FixStatus,
)
from backend.guidance.service import GuidanceService
from backend.guidance.models import (
    GuidancePlan,
    GapCategory,
)
from backend.conversation.evidence_service import ConversationEvidenceService
from backend.domain.models import ClaimStatus
from backend.session.models import (
    BuildCoachSession,
    SessionState,
    SessionSummary,
    EvidenceSummary,
    VerificationSummary,
    UnderstandingSummary,
    GuidanceSummary,
)
from backend.session.state import compute_session_state
from backend.session.summary import (
    build_session_summary,
    build_verification_summary,
    build_understanding_summary,
    build_guidance_summary,
    build_evidence_summary,
    build_workflow_elements,
)


class SessionService:
    """Orchestrates existing M7, M11, M12.4, M12.5, M12.6, and M12.7 services into a single unified session.
    
    TRUST INVARIANTS:
    1. Orchestration only: zero duplicated business logic or re-implemented evaluators.
    2. Zero LLM calls for state determination or evidence aggregation.
    3. Strict project isolation: Project A never accesses Project B data.
    4. Deterministic and fast (< 250 ms) session retrieval reusing indexed database state.
    """

    def __init__(
        self,
        db: Database,
        observation_service: Optional[ObservationService] = None,
        guidance_service: Optional[GuidanceService] = None,
        evidence_service: Optional[ConversationEvidenceService] = None,
    ):
        self.db = db
        self.observation_service = observation_service or ObservationService(db)
        self.guidance_service = guidance_service or GuidanceService(db, self.observation_service)
        self.evidence_service = evidence_service or ConversationEvidenceService(db)

    def get_or_create_session(self, project_id: str) -> BuildCoachSession:
        """Retrieves or computes a unified, up-to-date BuildCoachSession for a registered project."""
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found in registry: '{project_id}'")

        # 1. Retrieve chronological timeline for this project (M12.5)
        timeline = self.observation_service.get_timeline(project_id)

        # 2. Retrieve focused incident packet if any incident exists (M12.6)
        packet: Optional[TimelineExplanationPacket] = None
        try:
            packet = self.observation_service.get_incident_explanation_packet(project_id)
        except Exception:
            packet = None

        # 3. Compute fresh guidance plan (M12.7)
        plan = self.guidance_service.generate_plan(project_id)

        # 4. Comprehension run history (M7)
        comprehension_history = self._get_comprehension_history(project_id)

        # 5. Check if project has uncommitted changes (M3/M6 ChangeSet cached in DB or timeline)
        has_uncommitted = self._has_uncommitted_changes(project_id, timeline)

        # 6. Aggregate conversation evidence for bound conversations (M11.1 / M12.4)
        evidence_summary = self._aggregate_evidence(project_id)

        # 7. Compute deterministic high-level session state
        state = compute_session_state(
            timeline=timeline,
            packet=packet,
            plan=plan,
            comprehension_history=comprehension_history,
            has_uncommitted_changes=has_uncommitted,
        )

        # 8. Build component summaries
        summary = build_session_summary(
            timeline=timeline,
            packet=packet,
            plan=plan,
            comprehension_history=comprehension_history,
        )
        verification_summary = build_verification_summary(packet, timeline)
        understanding_summary = build_understanding_summary(
            comprehension_history=comprehension_history,
            plan=plan,
            has_changes=(summary.recent_changes > 0 or has_uncommitted),
        )
        guidance_summary = build_guidance_summary(plan)

        # 8b. Deterministic explanation for incident (if any)
        explanation = None
        if packet and packet.incident:
            try:
                explanation = self.observation_service.explain_incident(
                    project_id=project_id,
                    incident_id=packet.incident.get("incident_id"),
                    has_consent=False,
                )
            except Exception:
                explanation = None

        workflow = build_workflow_elements(
            timeline=timeline,
            packet=packet,
            explanation=explanation,
            verification_summary=verification_summary,
            summary=summary,
        )

        # 9. Extract incident details
        active_incident = packet.incident if (packet and packet.incident) else None
        recent_incidents = [packet.incident] if (packet and packet.incident) else []

        # 10. Persist or update session identity
        session_id = f"session_{project_id}"
        now = utc_now_iso()

        existing = self.db.get_session_by_project_id(project_id)
        created_at = existing["created_at"] if existing else now

        session = BuildCoachSession(
            session_id=session_id,
            project_id=project_id,
            created_at=created_at,
            updated_at=now,
            state=state,
            summary=summary,
            active_incident=active_incident,
            recent_incidents=recent_incidents,
            evidence_summary=evidence_summary,
            verification_summary=verification_summary,
            understanding_summary=understanding_summary,
            guidance_summary=guidance_summary,
            next_action=plan.top_next_action,
            what_happened=workflow["what_happened"],
            fix_status=workflow["fix_status"],
            how_do_we_know=workflow["how_do_we_know"],
            evidence_chain=workflow["evidence_chain"],
            recent_activity=workflow["recent_activity"],
            incident_explanation=(
                explanation.model_dump()
                if explanation and hasattr(explanation, "model_dump")
                else (explanation if isinstance(explanation, dict) else None)
            ),
        )


        # Store in database
        self.db.upsert_session(
            session_id=session_id,
            project_id=project_id,
            state=state.value,
            created_at=created_at,
            updated_at=now,
            metadata=session.summary.model_dump(),
        )

        return session

    def refresh_session(self, project_id: str) -> BuildCoachSession:
        """Forces immediate re-evaluation and returns the refreshed session."""
        return self.get_or_create_session(project_id)

    # -------------------------------------------------------------------------
    # Unified Interaction Actions (Section 14)
    # -------------------------------------------------------------------------
    def what_happened(
        self,
        project_id: str,
        incident_id: Optional[str] = None,
        has_consent: bool = False,
        explicit_api_key: Optional[str] = None,
        gateway: Optional[Any] = None,
    ) -> Any:
        """Answers 'WHAT HAPPENED?' using M12.6 incident explanation subsystem."""
        return self.observation_service.explain_incident(
            project_id=project_id,
            incident_id=incident_id,
            has_consent=has_consent,
            explicit_api_key=explicit_api_key,
            gateway=gateway,
        )

    def what_should_i_do(
        self,
        project_id: str,
        incident_id: Optional[str] = None,
    ) -> GuidancePlan:
        """Answers 'WHAT SHOULD I DO?' using M12.7 Knowledge Gap & Next Action subsystem."""
        return self.guidance_service.generate_plan(
            project_id=project_id,
            incident_id=incident_id,
        )

    def do_i_understand(self, project_id: str) -> Dict[str, Any]:
        """Answers 'DO I UNDERSTAND?' using M7 comprehension evaluation history."""
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found: '{project_id}'")

        history = self._get_comprehension_history(project_id)
        plan = self.guidance_service.generate_plan(project_id)
        has_gap = any(g.category == GapCategory.UNDERSTANDING for g in plan.gaps)

        if history:
            latest = history[0]
            return {
                "project_id": project_id,
                "status": "EVALUATED",
                "overall_state": latest.get("overall_state"),
                "gap_count": latest.get("gap_count", 0),
                "created_at": latest.get("created_at"),
                "requires_explanation": has_gap or (latest.get("overall_state") != "UNDERSTOOD"),
                "action": "EXPLAIN_BACK" if (has_gap or latest.get("overall_state") != "UNDERSTOOD") else None,
            }

        return {
            "project_id": project_id,
            "status": "PENDING" if has_gap else "NOT_EVALUATED",
            "overall_state": "NONE",
            "gap_count": 1 if has_gap else 0,
            "requires_explanation": has_gap,
            "action": "EXPLAIN_BACK" if has_gap else None,
        }

    # -------------------------------------------------------------------------
    # Internal Aggregators
    # -------------------------------------------------------------------------
    def _aggregate_evidence(self, project_id: str) -> EvidenceSummary:
        """Aggregates verified claims and evidence links across all bound conversations."""
        convs = self.db.list_conversations(project_id=project_id)
        if not convs:
            return EvidenceSummary()

        total = 0
        supported = 0
        partially_supported = 0
        unsupported = 0
        unknown = 0

        for conv in convs:
            try:
                res = self.evidence_service.analyze_conversation(
                    conversation_id=conv.conversation_id,
                    project_id=project_id,
                )
                counts = res.summary.get("status_counts", {})
                total += res.summary.get("total_claims", len(res.claims))
                supported += counts.get(ClaimStatus.SUPPORTED, 0)
                partially_supported += counts.get(ClaimStatus.PARTIALLY_SUPPORTED, 0)
                unsupported += counts.get(ClaimStatus.UNSUPPORTED, 0)
                unknown += counts.get(ClaimStatus.UNKNOWN, 0)
            except Exception:
                continue

        return EvidenceSummary(
            total=total,
            supported=supported,
            partially_supported=partially_supported,
            unsupported=unsupported,
            unknown=unknown,
        )

    def _get_comprehension_history(self, project_id: str) -> List[Dict[str, Any]]:
        """Queries M7 comprehension runs for this project."""
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

    def _has_uncommitted_changes(self, project_id: str, timeline: List[ObservationEvent]) -> bool:
        """Determines if the project has uncommitted or recent changes without costly rescans."""
        # 1. Check timeline for recent GIT_CHANGE events
        if any(e.event_type == ObservationEventType.GIT_CHANGE for e in timeline):
            return True

        # 2. Check DB cached change sets
        cs = self.db.get_latest_change_set(project_id)
        if cs and cs.file_changes:
            return True

        return False
