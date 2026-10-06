"""Deterministic correlation of change, failure, and recovery events."""

from typing import List, Dict, Any, Optional
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    CorrelationRelation,
    CorrelationLink,
    ExplanationPacket,
    FixStatus,
    TimelineExplanationPacket,
)
from backend.observation.normalizer import compute_deterministic_id


class ObservationCorrelator:
    """Discovers deterministic temporal and structural associations across observation events.
    
    CRITICAL TRUST INVARIANT:
    Temporal proximity is NEVER inferred as causal proof.
    Associations describe:
      - 'precedes' / 'temporally_followed_by'
      - 'affects_same_file'
      - 'same_error_signature'
      - 'error_disappeared_after_change'
    """

    def correlate(self, events: List[ObservationEvent], project_id: str) -> List[CorrelationLink]:
        """Discovers deterministic associations among chronologically ordered events."""
        links: List[CorrelationLink] = []
        # Ensure chronological ordering
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.created_at))

        error_events = [e for e in sorted_events if e.event_type == ObservationEventType.RUNTIME_ERROR]
        change_events = [e for e in sorted_events if e.event_type == ObservationEventType.GIT_CHANGE]

        # 1. Check AFFECTS_SAME_FILE between changes and runtime errors
        for change in change_events:
            changed_files = set(change.payload.get("changed_files", []))
            for err in error_events:
                err_file = err.payload.get("file_path")
                if err_file and err_file in changed_files:
                    link_id = compute_deterministic_id("link", change.event_id, err.event_id, CorrelationRelation.AFFECTS_SAME_FILE)
                    links.append(
                        CorrelationLink(
                            link_id=link_id,
                            source_event_id=change.event_id,
                            target_event_id=err.event_id,
                            relation_type=CorrelationRelation.AFFECTS_SAME_FILE,
                            description=f"Change to '{err_file}' shares target path with subsequent error location.",
                            details={"file_path": err_file},
                        )
                    )

        # 2. Check SAME_ERROR across multiple occurrences
        for i, err1 in enumerate(error_events):
            sig1 = err1.payload.get("error_signature")
            if not sig1:
                continue
            for err2 in error_events[i + 1:]:
                sig2 = err2.payload.get("error_signature")
                if sig1 == sig2:
                    link_id = compute_deterministic_id("link", err1.event_id, err2.event_id, CorrelationRelation.SAME_ERROR)
                    links.append(
                        CorrelationLink(
                            link_id=link_id,
                            source_event_id=err1.event_id,
                            target_event_id=err2.event_id,
                            relation_type=CorrelationRelation.SAME_ERROR,
                            description=f"Identical error signature '{sig1}' recurred.",
                            details={"error_signature": sig1},
                        )
                    )

        # 3. Detect ERROR_DISAPPEARED_AFTER_CHANGE (Recovery candidate)
        for err in error_events:
            err_ts = err.timestamp
            sig = err.payload.get("error_signature")
            err_file = err.payload.get("file_path")

            # Look for subsequent change event
            intervening_changes = [
                c for c in change_events
                if c.timestamp >= err_ts and c.event_id != err.event_id
            ]
            if not intervening_changes:
                continue

            last_change = intervening_changes[-1]
            last_change_ts = last_change.timestamp

            # Look for post-change execution/test/command/http events
            post_change_events = [
                e for e in sorted_events
                if e.timestamp >= last_change_ts
                and e.event_id not in (err.event_id, last_change.event_id)
            ]

            # Check if identical error recurred after last_change
            subsequent_same_errors = [
                e for e in post_change_events
                if e.event_type == ObservationEventType.RUNTIME_ERROR
                and e.payload.get("error_signature") == sig
            ]

            if not subsequent_same_errors:
                # Check if there is positive verification (e.g. process finished 0, command 0, test passed, or HTTP 2xx)
                verifications = [
                    e for e in post_change_events
                    if (
                        (e.event_type == ObservationEventType.COMMAND_FINISHED and e.payload.get("exit_code") == 0)
                        or (e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED")
                        or (e.event_type == ObservationEventType.HTTP_RESPONSE and 200 <= e.payload.get("status_code", 0) < 300)
                        or (e.event_type == ObservationEventType.PROCESS_STARTED)
                    )
                ]

                if verifications:
                    link_id = compute_deterministic_id("link", err.event_id, last_change.event_id, CorrelationRelation.ERROR_DISAPPEARED_AFTER_CHANGE)
                    links.append(
                        CorrelationLink(
                            link_id=link_id,
                            source_event_id=err.event_id,
                            target_event_id=last_change.event_id,
                            relation_type=CorrelationRelation.ERROR_DISAPPEARED_AFTER_CHANGE,
                            description=f"Error '{err.payload.get('error_kind')}' did not reappear after change to '{err_file or 'project'}'.",
                            details={
                                "error_signature": sig,
                                "verified_by_event_id": verifications[0].event_id,
                            },
                        )
                    )

        return links

    def build_explanation_packet(
        self,
        events: List[ObservationEvent],
        project_id: str,
    ) -> ExplanationPacket:
        """Constructs a deterministic explanation packet detailing observed problems, changes, and recoveries."""
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.created_at))
        links = self.correlate(sorted_events, project_id)

        error_events = [e for e in sorted_events if e.event_type == ObservationEventType.RUNTIME_ERROR]
        change_events = [e for e in sorted_events if e.event_type == ObservationEventType.GIT_CHANGE]

        problem: Dict[str, Any] = {}
        if error_events:
            first_err = error_events[0]
            problem = {
                "error_kind": first_err.payload.get("error_kind"),
                "message": first_err.payload.get("message"),
                "file_path": first_err.payload.get("file_path"),
                "line_number": first_err.payload.get("line_number"),
                "error_signature": first_err.payload.get("error_signature"),
                "event_id": first_err.event_id,
            }

        changes_list = []
        for c in change_events:
            changes_list.append({
                "event_id": c.event_id,
                "timestamp": c.timestamp,
                "changed_files": c.payload.get("changed_files", []),
                "summary": c.payload.get("summary", ""),
            })

        recovery: Dict[str, Any] = {}
        recovery_links = [l for l in links if l.relation_type == CorrelationRelation.ERROR_DISAPPEARED_AFTER_CHANGE]
        if recovery_links:
            rec_link = recovery_links[-1]
            recovery = {
                "status": "RECOVERED",
                "relation": CorrelationRelation.ERROR_DISAPPEARED_AFTER_CHANGE,
                "description": rec_link.description,
                "correlated_change_id": rec_link.target_event_id,
                "verified_by_event_id": rec_link.details.get("verified_by_event_id"),
            }
        elif error_events:
            recovery = {
                "status": "UNRESOLVED",
                "description": "Error was observed and has not been proven to disappear following a change.",
            }

        # Epistemic unknowns: always state clear limitations
        unknowns = [
            "Build Coach cannot prove that the code change was the sole cause of error resolution.",
            "Temporal correlation does not establish causal proof.",
        ]

        evidence_list = []
        for l in links:
            evidence_list.append(l.model_dump())

        return ExplanationPacket(
            project_id=project_id,
            problem=problem,
            changes=changes_list,
            observations=[e.model_dump() for e in sorted_events],
            recovery=recovery,
            evidence=evidence_list,
            unknowns=unknowns,
        )

    def build_incident_explanation_packet(
        self,
        events: List[ObservationEvent],
        project_id: str,
        incident_id: Optional[str] = None,
    ) -> TimelineExplanationPacket:
        """Constructs a focused incident explanation packet (explanation-v1) for M12.6.
        
        Relevance filtering:
        - Selects target incident (by incident_id / event_id / error_signature, or latest RUNTIME_ERROR).
        - Isolates relevant window: preceding relevant changes, error event(s), subsequent changes,
          and subsequent verification events (restarts, tests, HTTP responses).
        - Ignores unrelated events unless structurally or temporally correlated.
        - Computes deterministic fix_status (VERIFIED, RECOVERED, PERSISTING, UNKNOWN).
        - Computes deterministic confidence (HIGH, MEDIUM, LOW).
        """
        from backend.observation.models import FixStatus, TimelineExplanationPacket

        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.created_at))
        links = self.correlate(sorted_events, project_id)

        error_events = [e for e in sorted_events if e.event_type == ObservationEventType.RUNTIME_ERROR]
        change_events = [e for e in sorted_events if e.event_type == ObservationEventType.GIT_CHANGE]

        target_error: Optional[ObservationEvent] = None
        if incident_id:
            for err in error_events:
                if (
                    err.event_id == incident_id
                    or err.payload.get("error_signature") == incident_id
                    or err.payload.get("error_kind") == incident_id
                ):
                    target_error = err
                    break
            # If not matching an error event specifically, check if incident_id matches any event
            if target_error is None:
                for ev in sorted_events:
                    if ev.event_id == incident_id:
                        if ev.event_type == ObservationEventType.RUNTIME_ERROR:
                            target_error = ev
                        break

        if target_error is None and error_events:
            target_error = error_events[-1]

        # If no error events exist in the timeline at all
        if target_error is None:
            pkt_id = compute_deterministic_id("pkt", project_id, "no_incident", utc_now_iso())
            return TimelineExplanationPacket(
                packet_version="explanation-v1",
                packet_id=pkt_id,
                project_id=project_id,
                incident={},
                timeline=[e.model_dump() for e in sorted_events],
                changes=[c.model_dump() for c in change_events],
                correlations=[l.model_dump() for l in links],
                verification=[],
                fix_status=FixStatus.UNKNOWN,
                confidence="LOW",
                unknowns=[
                    "No runtime error incident was identified in the project timeline.",
                    "Build Coach cannot evaluate a fix status without an observed problem.",
                ],
            )

        err_sig = target_error.payload.get("error_signature", "")
        err_file = target_error.payload.get("file_path")
        err_ts = target_error.timestamp

        # 1. Focused Changes Filtering:
        # Include changes touching the same file, or immediately preceding the error if no file match
        relevant_changes = []
        prior_changes = [c for c in change_events if c.timestamp <= err_ts]
        subsequent_changes = [c for c in change_events if c.timestamp > err_ts]

        # Prior: prefer changes affecting the same file; if none, include the immediately preceding change
        prior_same_file = [c for c in prior_changes if err_file and err_file in c.payload.get("changed_files", [])]
        if prior_same_file:
            relevant_changes.extend(prior_same_file)
        elif prior_changes:
            relevant_changes.append(prior_changes[-1])

        # Subsequent: prefer changes affecting the same file; if none, include subsequent changes up to recovery
        subsequent_same_file = [c for c in subsequent_changes if err_file and err_file in c.payload.get("changed_files", [])]
        if subsequent_same_file:
            relevant_changes.extend(subsequent_same_file)
        elif subsequent_changes:
            relevant_changes.extend(subsequent_changes[:2])

        # Ensure no duplicates while keeping chronological order
        seen_c_ids = set()
        unique_changes = []
        for c in relevant_changes:
            if c.event_id not in seen_c_ids:
                seen_c_ids.add(c.event_id)
                unique_changes.append(c)

        # 2. Check for subsequent changes after error
        post_error_changes = [c for c in unique_changes if c.timestamp >= err_ts and c.event_id != target_error.event_id]

        # 3. Check for recurrence of same error after post-error change
        recurring_errors = []
        if post_error_changes:
            last_change = post_error_changes[-1]
            recurring_errors = [
                e for e in sorted_events
                if e.timestamp >= last_change.timestamp
                and e.event_type == ObservationEventType.RUNTIME_ERROR
                and e.payload.get("error_signature") == err_sig
                and e.event_id != target_error.event_id
            ]

        # 4. Filter Post-change verification events
        verification_events = []
        if post_error_changes:
            last_change = post_error_changes[-1]
            verification_events = [
                e for e in sorted_events
                if e.timestamp >= last_change.timestamp
                and e.event_id not in (target_error.event_id, last_change.event_id)
                and (
                    (e.event_type == ObservationEventType.COMMAND_FINISHED and e.payload.get("exit_code") == 0)
                    or (e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED")
                    or (e.event_type == ObservationEventType.HTTP_RESPONSE and 200 <= e.payload.get("status_code", 0) < 300)
                    or (e.event_type == ObservationEventType.PROCESS_STARTED)
                )
            ]

        # 5. Determine FixStatus and Confidence
        targeted_tests_passed = any(
            e.event_type == ObservationEventType.TEST_FINISHED and e.payload.get("status") == "PASSED"
            for e in verification_events
        )

        has_recovery_evidence = len(verification_events) > 0 and len(recurring_errors) == 0

        if len(recurring_errors) > 0:
            fix_status = FixStatus.PERSISTING
            confidence = "HIGH"
        elif post_error_changes and targeted_tests_passed and len(recurring_errors) == 0:
            fix_status = FixStatus.VERIFIED
            confidence = "HIGH"
        elif post_error_changes and has_recovery_evidence:
            fix_status = FixStatus.RECOVERED
            confidence = "MEDIUM"
        elif not post_error_changes or not has_recovery_evidence:
            fix_status = FixStatus.UNKNOWN
            confidence = "LOW"
        else:
            fix_status = FixStatus.UNKNOWN
            confidence = "LOW"

        # 6. Focused Timeline Assembly
        # Include target error, relevant changes, post-change verifications, recurring errors
        focused_event_ids = {target_error.event_id}
        for c in unique_changes:
            focused_event_ids.add(c.event_id)
        for v in verification_events:
            focused_event_ids.add(v.event_id)
        for r in recurring_errors:
            focused_event_ids.add(r.event_id)

        # Include commands or process starts that triggered the error or verifications
        for e in sorted_events:
            if e.event_id in focused_event_ids:
                continue
            if e.event_type in (ObservationEventType.COMMAND_FINISHED, ObservationEventType.PROCESS_STARTED):
                # Only include if occurring within 60s of the error or verification
                try:
                    from datetime import datetime
                    e_dt = datetime.fromisoformat(e.timestamp.replace("Z", "+00:00"))
                    err_dt = datetime.fromisoformat(err_ts.replace("Z", "+00:00"))
                    if abs((e_dt - err_dt).total_seconds()) <= 60:
                        focused_event_ids.add(e.event_id)
                except Exception:
                    pass

        focused_timeline = [e for e in sorted_events if e.event_id in focused_event_ids]

        # Relevant correlations
        relevant_correlations = [
            l for l in links
            if l.source_event_id in focused_event_ids or l.target_event_id in focused_event_ids
        ]

        incident_info = {
            "event_id": target_error.event_id,
            "error_signature": err_sig,
            "error_type": target_error.payload.get("error_kind", "RuntimeError"),
            "message": target_error.payload.get("message", ""),
            "location": {
                "file_path": err_file,
                "line_number": target_error.payload.get("line_number"),
                "function_name": target_error.payload.get("function_name"),
            },
            "timestamp": target_error.timestamp,
        }

        unknowns = [
            "Build Coach cannot prove the code change was the sole cause of error disappearance.",
            "Temporal correlation does not establish causal proof.",
        ]
        if fix_status == FixStatus.UNKNOWN:
            unknowns.append("No post-change recovery or verification run was observed for this error.")
        elif fix_status == FixStatus.RECOVERED and not targeted_tests_passed:
            unknowns.append("No targeted automated test suite confirmed the fix; status is based on operational observation.")

        pkt_id = compute_deterministic_id("pkt", project_id, target_error.event_id, fix_status)

        return TimelineExplanationPacket(
            packet_version="explanation-v1",
            packet_id=pkt_id,
            project_id=project_id,
            incident=incident_info,
            timeline=[e.model_dump() for e in focused_timeline],
            changes=[c.model_dump() for c in unique_changes],
            correlations=[l.model_dump() for l in relevant_correlations],
            verification=[v.model_dump() for v in verification_events],
            fix_status=fix_status,
            confidence=confidence,
            unknowns=unknowns,
        )

