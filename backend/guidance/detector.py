"""Deterministic detector for evidence-backed knowledge and verification gaps (Milestone 12.7)."""

from typing import List, Dict, Any, Optional, Set
import hashlib
from backend.domain.models import utc_now_iso
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    FixStatus,
    TimelineExplanationPacket,
    IncidentExplanation,
)
from backend.guidance.models import (
    KnowledgeGap,
    GapCategory,
    ActionPriority,
)


def _compute_gap_id(project_id: str, category: str, seed: str) -> str:
    raw = f"{project_id}:{category}:{seed}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"gap_{category.lower()}_{h}"


class KnowledgeGapDetector:
    """Detects evidence-grounded knowledge and verification gaps strictly from verified observations and incident explanation packets.
    
    TRUST INVARIANT:
    Zero LLM calls for gap detection. Every detected gap MUST cite real, existing evidence IDs.
    Gaps without valid evidence IDs are never created.
    """

    def detect_gaps(
        self,
        project_id: str,
        events: List[ObservationEvent],
        packet: Optional[TimelineExplanationPacket] = None,
        explanation: Optional[IncidentExplanation] = None,
        comprehension_history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[KnowledgeGap]:
        """Detects all grounded knowledge and verification gaps across the current project timeline."""
        gaps: List[KnowledgeGap] = []
        incident_id = packet.incident.get("event_id") if (packet and packet.incident) else None

        # Sort events chronologically
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.created_at))
        valid_event_ids: Set[str] = {e.event_id for e in sorted_events}

        # -------------------------------------------------------------
        # 1. UNRESOLVED_ERROR Gap
        # Case A: Persistent runtime error or packet fix_status == PERSISTING
        # -------------------------------------------------------------
        if packet and packet.fix_status == FixStatus.PERSISTING:
            # Find the persistent error events
            err_sig = packet.incident.get("error_signature", "")
            err_events = [
                e.event_id for e in sorted_events
                if e.event_type == ObservationEventType.RUNTIME_ERROR
                and e.payload.get("error_signature") == err_sig
            ]
            if not err_events and packet.incident.get("event_id"):
                err_events = [packet.incident["event_id"]]
            
            # Evidence IDs must be real
            grounded_refs = [eid for eid in err_events if eid in valid_event_ids]
            if grounded_refs:
                gaps.append(
                    KnowledgeGap(
                        gap_id=_compute_gap_id(project_id, GapCategory.UNRESOLVED_ERROR.value, grounded_refs[0]),
                        project_id=project_id,
                        incident_id=incident_id,
                        category=GapCategory.UNRESOLVED_ERROR,
                        description=f"Runtime error '{packet.incident.get('error_type', 'Error')}' continues to recur after code modifications.",
                        reason="The failure is active and persisting; executing further changes without diagnosing the root failure risks compounding defects.",
                        priority=ActionPriority.CRITICAL,
                        evidence_ids=grounded_refs,
                        metadata={
                            "error_signature": err_sig,
                            "error_type": packet.incident.get("error_type"),
                            "location": packet.incident.get("location"),
                        },
                    )
                )

        # -------------------------------------------------------------
        # 2. TEST_COVERAGE / VERIFICATION Gap
        # Case B: Error recovered operationally (HTTP 200, clean restart) but no targeted automated test
        # -------------------------------------------------------------
        if packet and packet.fix_status == FixStatus.RECOVERED:
            # Verification events exist in packet, but no automated test pass
            verify_ids = [
                v.get("event_id") for v in packet.verification
                if v.get("event_id") in valid_event_ids
            ]
            incident_ref = packet.incident.get("event_id")
            if incident_ref and incident_ref in valid_event_ids:
                verify_ids.insert(0, incident_ref)

            if verify_ids:
                gaps.append(
                    KnowledgeGap(
                        gap_id=_compute_gap_id(project_id, GapCategory.TEST_COVERAGE.value, verify_ids[0]),
                        project_id=project_id,
                        incident_id=incident_id,
                        category=GapCategory.TEST_COVERAGE,
                        description="Error ceased operationally following changes, but has no passing automated test verification.",
                        reason="Operational disappearance without targeted test coverage means regression risk remains high on future edits.",
                        priority=ActionPriority.HIGH,
                        evidence_ids=verify_ids,
                        metadata={
                            "error_signature": packet.incident.get("error_signature"),
                            "verification_events_count": len(packet.verification),
                        },
                    )
                )

        # -------------------------------------------------------------
        # 3. VERIFICATION Gap (General UNKNOWN FixStatus)
        # Incident occurred, code changed, but no verification of any kind was observed
        # -------------------------------------------------------------
        if packet and packet.fix_status == FixStatus.UNKNOWN and packet.incident:
            inc_id = packet.incident.get("event_id")
            change_ids = [c.get("event_id") for c in packet.changes if c.get("event_id") in valid_event_ids]
            evidence_ids = []
            if inc_id and inc_id in valid_event_ids:
                evidence_ids.append(inc_id)
            evidence_ids.extend(change_ids)

            if evidence_ids:
                gaps.append(
                    KnowledgeGap(
                        gap_id=_compute_gap_id(project_id, GapCategory.VERIFICATION.value, evidence_ids[0]),
                        project_id=project_id,
                        incident_id=incident_id,
                        category=GapCategory.VERIFICATION,
                        description="No runtime verification, process execution, or test run was observed following the incident.",
                        reason="The system has unknown health status because neither operational execution nor test runs have taken place.",
                        priority=ActionPriority.HIGH,
                        evidence_ids=evidence_ids,
                        metadata={"status": "UNVERIFIED"},
                    )
                )

        # -------------------------------------------------------------
        # 4. CODE_CHANGE_REVIEW Gap
        # Case D: Significant code changes exist in the incident window or timeline without explicit review
        # -------------------------------------------------------------
        recent_changes = [
            e for e in sorted_events
            if e.event_type == ObservationEventType.GIT_CHANGE
            and e.payload.get("changed_files")
        ]
        if recent_changes:
            latest_change = recent_changes[-1]
            changed_files = latest_change.payload.get("changed_files", [])
            # Check if review has been completed for this change
            has_review = latest_change.payload.get("reviewed", False)
            if not has_review and latest_change.event_id in valid_event_ids:
                gaps.append(
                    KnowledgeGap(
                        gap_id=_compute_gap_id(project_id, GapCategory.CODE_CHANGE_REVIEW.value, latest_change.event_id),
                        project_id=project_id,
                        incident_id=incident_id,
                        category=GapCategory.CODE_CHANGE_REVIEW,
                        description=f"Unreviewed code modifications observed across {len(changed_files)} file(s): {', '.join(changed_files[:3])}.",
                        reason="Reviewing file diffs ensures understanding of structural changes made during AI-assisted troubleshooting.",
                        priority=ActionPriority.MEDIUM,
                        evidence_ids=[latest_change.event_id],
                        metadata={"changed_files": changed_files},
                    )
                )

        # -------------------------------------------------------------
        # 5. UNDERSTANDING Gap (M7 Can-I-Explain Integration)
        # Case C: Explanation identifies important mechanism or change, but user has not successfully explained it back
        # -------------------------------------------------------------
        if explanation and explanation.what_to_understand:
            # Check comprehension history for successful explain-back
            has_passed_understanding = False
            if comprehension_history:
                for record in comprehension_history:
                    if record.get("project_id") == project_id and record.get("overall_state") == "UNDERSTOOD":
                        has_passed_understanding = True
                        break

            if not has_passed_understanding and explanation.evidence_refs:
                valid_refs = [ref for ref in explanation.evidence_refs if ref in valid_event_ids]
                if valid_refs:
                    concept_preview = explanation.what_to_understand[0]
                    gaps.append(
                        KnowledgeGap(
                            gap_id=_compute_gap_id(project_id, GapCategory.UNDERSTANDING.value, valid_refs[0]),
                            project_id=project_id,
                            incident_id=incident_id,
                            category=GapCategory.UNDERSTANDING,
                            description=f"Architectural concept requires self-explanation: '{concept_preview[:120]}'.",
                            reason="Explaining purpose, mechanism, failure modes, and downstream impact solidifies mental models and avoids cargo-cult debugging.",
                            priority=ActionPriority.MEDIUM,
                            evidence_ids=valid_refs[:4],
                            metadata={
                                "concepts": explanation.what_to_understand,
                                "explanation_id": explanation.explanation_id,
                            },
                        )
                    )

        # -------------------------------------------------------------
        # 6. RUNTIME_BEHAVIOR / DEPENDENCY Gap
        # Check for dependency errors (ModuleNotFoundError, ImportError, PackageNotFoundError)
        # -------------------------------------------------------------
        if packet and packet.incident:
            err_type = str(packet.incident.get("error_type", ""))
            msg = str(packet.incident.get("message", ""))
            is_dep_error = any(kw in err_type or kw in msg for kw in ("ModuleNotFound", "ImportError", "Cannot find module", "PackageNotFound"))
            if is_dep_error and packet.fix_status != FixStatus.VERIFIED:
                inc_ref = packet.incident.get("event_id")
                if inc_ref and inc_ref in valid_event_ids:
                    gaps.append(
                        KnowledgeGap(
                            gap_id=_compute_gap_id(project_id, GapCategory.DEPENDENCY.value, inc_ref),
                            project_id=project_id,
                            incident_id=incident_id,
                            category=GapCategory.DEPENDENCY,
                            description=f"Unresolved package/module dependency boundary identified: {err_type}.",
                            reason="Missing or mismatched dependencies cause immediate startup crashes and break deployment predictability.",
                            priority=ActionPriority.HIGH if packet.fix_status == FixStatus.PERSISTING else ActionPriority.MEDIUM,
                            evidence_ids=[inc_ref],
                            metadata={"error_type": err_type, "message": msg},
                        )
                    )

        return gaps
