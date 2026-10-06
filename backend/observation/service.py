"""Central observation service for recording, timeline retrieval, and correlation."""

from pathlib import Path
from typing import Optional, Dict, Any, List
from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.project_model.context_detector import ContextDetector
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
    VALID_EVENT_TYPES,
    ExplanationPacket,
    CorrelationLink,
)
from backend.observation.store import ObservationStore
from backend.observation.normalizer import (
    compute_deterministic_id,
    parse_stack_trace_and_error,
    sanitize_and_redact_payload,
)
from backend.observation.correlator import ObservationCorrelator


class ObservationService:
    """Manages project runtime observations, timelines, and change-failure-recovery correlations."""

    def __init__(self, db: Database):
        self.db = db
        self.store = ObservationStore(db)
        self.correlator = ObservationCorrelator()

    def record_event(
        self,
        project_id: str,
        event_type: str,
        source: str,
        payload: Dict[str, Any],
        provenance: Dict[str, Any],
        timestamp: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> ObservationEvent:
        """Validates, redacts, normalizes, and atomically persists an observation event."""
        # 1. Validate project exists in registry
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found in registry: '{project_id}'")

        # 2. Validate event_type
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type '{event_type}'. Valid types: {sorted(list(VALID_EVENT_TYPES))}")

        ts = timestamp or utc_now_iso()

        # 3. Redact secrets in payload and provenance
        clean_payload = sanitize_and_redact_payload(payload)
        clean_provenance = sanitize_and_redact_payload(provenance)

        # 4. Generate deterministic event_id if not given
        eid = event_id or compute_deterministic_id("evt", project_id, event_type, ts, clean_payload)

        event = ObservationEvent(
            event_id=eid,
            project_id=project_id,
            event_type=event_type,
            timestamp=ts,
            source=source,
            payload=clean_payload,
            provenance=clean_provenance,
            created_at=utc_now_iso(),
        )

        # 5. Persist event
        self.store.save_event(event)
        return event

    def observe_command(
        self,
        project_id: str,
        command: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> List[ObservationEvent]:
        """Observes an externally executed command result without executing shell code."""
        events: List[ObservationEvent] = []
        t_finish = finished_at or utc_now_iso()
        t_start = started_at or t_finish

        # 1. Record command finished
        cmd_evt = self.record_event(
            project_id=project_id,
            event_type=ObservationEventType.COMMAND_FINISHED,
            source=ObservationSource.TERMINAL,
            timestamp=t_finish,
            payload={
                "command": command,
                "exit_code": exit_code,
                "stdout_snippet": stdout[-1000:].strip() if stdout else "",
                "stderr_snippet": stderr[-1000:].strip() if stderr else "",
            },
            provenance={
                "source": ObservationSource.TERMINAL,
                "command": command,
                "exit_code": exit_code,
                "started_at": t_start,
                "finished_at": t_finish,
            },
        )
        events.append(cmd_evt)

        # 2. Check if stderr contains a runtime failure / stack trace
        parsed_err = parse_stack_trace_and_error(stderr) or parse_stack_trace_and_error(stdout)
        if parsed_err or exit_code != 0:
            err_kind = parsed_err.error_kind if parsed_err else "NonZeroExitCode"
            err_msg = parsed_err.message if parsed_err else f"Command exited with code {exit_code}"
            file_p = parsed_err.file_path if parsed_err else None
            line_no = parsed_err.line_number if parsed_err else None
            sig = parsed_err.error_signature if parsed_err else compute_deterministic_id("errsig", err_kind, err_msg)

            err_evt = self.record_event(
                project_id=project_id,
                event_type=ObservationEventType.RUNTIME_ERROR,
                source=ObservationSource.TERMINAL,
                timestamp=t_finish,
                payload={
                    "error_kind": err_kind,
                    "message": err_msg,
                    "file_path": file_p,
                    "line_number": line_no,
                    "error_signature": sig,
                    "raw_snippet": stderr[-500:].strip() if stderr else stdout[-500:].strip(),
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "command": command,
                    "exit_code": exit_code,
                },
            )
            events.append(err_evt)

        return events

    def observe_git_changes(
        self,
        project_id: str,
        timestamp: Optional[str] = None,
    ) -> Optional[ObservationEvent]:
        """Observes working-tree and Git changes using existing M3/M6 ContextDetector."""
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found: '{project_id}'")

        root = Path(project.root_path)
        detector = ContextDetector(root, project_id)
        changeset = detector.collect()

        changed_files = [fc.new_path or fc.old_path for fc in changeset.file_changes if fc.new_path or fc.old_path]
        if not changed_files and not changeset.git_state.is_dirty:
            return None

        ts = timestamp or utc_now_iso()
        return self.record_event(
            project_id=project_id,
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            timestamp=ts,
            payload={
                "changed_files": changed_files,
                "modified_count": changeset.git_state.modified_count,
                "untracked_count": changeset.git_state.untracked_count,
                "is_dirty": changeset.git_state.is_dirty,
                "summary": f"{len(changed_files)} files modified or untracked",
            },
            provenance={
                "source": ObservationSource.GIT,
                "head_commit": changeset.git_state.head_commit,
                "branch": changeset.git_state.current_branch,
            },
        )

    def observe_http_response(
        self,
        project_id: str,
        method: str,
        path: str,
        status_code: int,
        timestamp: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> ObservationEvent:
        """Observes an HTTP response event without recording sensitive headers."""
        ts = timestamp or utc_now_iso()
        return self.record_event(
            project_id=project_id,
            event_type=ObservationEventType.HTTP_RESPONSE,
            source=ObservationSource.HTTP,
            timestamp=ts,
            payload={
                "method": method.upper(),
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
            provenance={
                "source": ObservationSource.HTTP,
                "method": method.upper(),
                "path": path,
            },
        )

    def observe_test_result(
        self,
        project_id: str,
        test_name: str,
        status: str,  # PASSED, FAILED, SKIPPED
        duration_ms: Optional[float] = None,
        error_message: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> List[ObservationEvent]:
        """Observes a test execution outcome (e.g. pytest)."""
        events: List[ObservationEvent] = []
        ts = timestamp or utc_now_iso()

        test_evt = self.record_event(
            project_id=project_id,
            event_type=ObservationEventType.TEST_FINISHED,
            source=ObservationSource.PYTEST,
            timestamp=ts,
            payload={
                "test_name": test_name,
                "status": status.upper(),
                "duration_ms": duration_ms,
            },
            provenance={
                "source": ObservationSource.PYTEST,
                "test_name": test_name,
            },
        )
        events.append(test_evt)

        if status.upper() == "FAILED" and error_message:
            sig = compute_deterministic_id("errsig", "TestFailure", test_name, error_message.strip())
            err_evt = self.record_event(
                project_id=project_id,
                event_type=ObservationEventType.RUNTIME_ERROR,
                source=ObservationSource.PYTEST,
                timestamp=ts,
                payload={
                    "error_kind": "TestFailure",
                    "message": error_message.strip(),
                    "error_signature": sig,
                    "test_name": test_name,
                },
                provenance={
                    "source": ObservationSource.PYTEST,
                    "test_name": test_name,
                },
            )
            events.append(err_evt)

        return events

    def get_timeline(
        self,
        project_id: str,
        limit: int = 1000,
        event_types: Optional[List[str]] = None,
    ) -> List[ObservationEvent]:
        """Returns chronologically ordered observation events for a project."""
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found: '{project_id}'")
        return self.store.get_timeline(project_id, limit=limit, event_types=event_types)

    def get_explanation_packet(self, project_id: str) -> ExplanationPacket:
        """Builds an explanation packet summarizing problems, changes, and recoveries."""
        timeline = self.get_timeline(project_id)
        return self.correlator.build_explanation_packet(timeline, project_id)

    def get_incident_explanation_packet(
        self,
        project_id: str,
        incident_id: Optional[str] = None,
    ) -> Any:
        """Builds a focused incident explanation packet (explanation-v1) for M12.6."""
        timeline = self.get_timeline(project_id)
        return self.correlator.build_incident_explanation_packet(timeline, project_id, incident_id=incident_id)

    def explain_incident(
        self,
        project_id: str,
        incident_id: Optional[str] = None,
        has_consent: bool = False,
        explicit_api_key: Optional[str] = None,
        gateway: Optional[Any] = None,
    ) -> Any:
        """Produces user-facing IncidentExplanation for a specific or latest incident.
        
        If has_consent=False or gateway unavailable, returns deterministic fallback explanation.
        """
        from backend.observation.generator import TimelineExplanationGenerator
        from backend.ai_gateway.consent import ConsentManager

        packet = self.get_incident_explanation_packet(project_id, incident_id=incident_id)
        gen = TimelineExplanationGenerator(gateway=gateway)

        consent_token = None
        if has_consent:
            context_pkt = gen.convert_packet_to_context_packet(packet)
            consent_token = ConsentManager.grant_consent(context_pkt, provider="gemini", model="gemini-3.8-flash")

        return gen.explain(
            packet=packet,
            consent_token=consent_token,
            explicit_api_key=explicit_api_key,
        )


