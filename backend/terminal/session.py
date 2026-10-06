"""Terminal session tracking and observation dispatch (Milestone 12.10)."""

import uuid
from typing import Optional, Dict, Any, List, Tuple
from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
)
from backend.session.service import SessionService
from backend.context_engine.secrets import detect_and_redact
from backend.runtime.capture import CapturedOutput, DEFAULT_MAX_OUTPUT_BYTES
from backend.runtime.parser import parse_runtime_error, parse_test_results
from backend.terminal.protocol import (
    TerminalCommandPayload,
    TerminalSessionRecord,
    IntegrationStatus,
)


# Commands considered interactive / REPL / full-screen TUI that should not be intercepted or corrupted
INTERACTIVE_EXCLUSIONS = {
    "vim", "vi", "nano", "emacs",
    "ssh", "telnet",
    "top", "htop", "btop",
    "python", "python3", "ipython", "node", "cmd", "powershell", "pwsh", "bash", "zsh", "sh",
}


class TerminalSessionManager:
    """Tracks terminal sessions and maps shell executions into the Build Coach observation engine."""

    def __init__(
        self,
        db: Database,
        observation_service: Optional[ObservationService] = None,
        session_service: Optional[SessionService] = None,
    ):
        self.db = db
        self.observation_service = observation_service or ObservationService(db)
        self.session_service = session_service or SessionService(db, self.observation_service)

    def get_or_create_session(
        self,
        project_id: str,
        session_id: Optional[str] = None,
        shell_type: str = "powershell",
    ) -> TerminalSessionRecord:
        """Retrieves or registers an active terminal session tied strictly to a project."""
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' is not registered in Build Coach.")

        sid = session_id or f"term_{uuid.uuid4().hex[:12]}"
        existing = self.db.get_terminal_session(sid)
        now = utc_now_iso()

        if existing:
            # Update last activity
            self.db.upsert_terminal_session(
                session_id=sid,
                project_id=project_id,
                shell_type=existing["shell_type"],
                started_at=existing["started_at"],
                last_activity_at=now,
                metadata=existing["metadata"],
            )
            return TerminalSessionRecord(
                session_id=sid,
                project_id=project_id,
                shell_type=existing["shell_type"],
                started_at=existing["started_at"],
                last_activity_at=now,
                metadata=existing["metadata"],
            )

        # Create new
        record = TerminalSessionRecord(
            session_id=sid,
            project_id=project_id,
            shell_type=shell_type,
            started_at=now,
            last_activity_at=now,
            metadata={"created_via": "shell_integration"},
        )
        self.db.upsert_terminal_session(
            session_id=record.session_id,
            project_id=record.project_id,
            shell_type=record.shell_type,
            started_at=record.started_at,
            last_activity_at=record.last_activity_at,
            metadata=record.metadata,
        )
        return record

    def record_terminal_command(
        self,
        project_id: str,
        payload: TerminalCommandPayload,
    ) -> Optional[Dict[str, Any]]:
        """Processes and persists terminal execution observations if integration is ENABLED.
        
        CRITICAL SAFETY INVARIANTS:
        1. If integration is DISABLED for this project, records NOTHING and returns None.
        2. Working directory or registered project root is enforced.
        3. Raw secrets are redacted across command string, stdout, stderr before persistence.
        4. Interactive commands (vim, ssh, REPLs) are marked safely without corrupting terminals.
        5. Session is refreshed immediately upon command completion.
        """
        # 1. Check if integration is enabled
        integ = self.db.get_terminal_integration(project_id)
        if not integ or integ.get("status") != IntegrationStatus.ENABLED.value:
            return None

        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' is not registered in Build Coach.")

        # 2. Get/refresh terminal session
        term_session = self.get_or_create_session(
            project_id=project_id,
            session_id=payload.terminal_session_id,
        )

        started_at = payload.started_at or utc_now_iso()
        finished_at = payload.finished_at or utc_now_iso()

        # 3. Clean and redact command string
        clean_cmd_str, was_cmd_redacted, _ = detect_and_redact(payload.command)
        cmd_tokens = clean_cmd_str.strip().split()
        cmd_name = cmd_tokens[0] if cmd_tokens else "command"

        # Check for interactive command exclusions
        if cmd_name.lower() in INTERACTIVE_EXCLUSIONS and len(cmd_tokens) == 1:
            # Interactive command started without arguments (e.g. entering python REPL or vim)
            # Record minimal event without capturing stream
            evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.COMMAND_STARTED,
                source=ObservationSource.TERMINAL,
                timestamp=started_at,
                payload={
                    "command_name": cmd_name,
                    "command_str": clean_cmd_str,
                    "interactive": True,
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "terminal_session_id": term_session.session_id,
                    "working_directory": payload.working_directory or project.root_path,
                },
            )
            return {
                "recorded": True,
                "interactive": True,
                "event_ids": [evt.event_id],
                "session_state": self.session_service.get_or_create_session(project_id).state.value,
            }

        # 4. Record COMMAND_STARTED event
        start_evt = self.observation_service.record_event(
            project_id=project_id,
            event_type=ObservationEventType.COMMAND_STARTED,
            source=ObservationSource.TERMINAL,
            timestamp=started_at,
            payload={
                "command_name": cmd_name,
                "command_str": clean_cmd_str,
            },
            provenance={
                "source": ObservationSource.TERMINAL,
                "terminal_session_id": term_session.session_id,
                "working_directory": payload.working_directory or project.root_path,
            },
        )

        # 5. Check if test command
        cmd_lower = clean_cmd_str.lower()
        is_test_run = any(k in cmd_lower for k in ("pytest", "npm test", "node --test", "jest", "vitest", "unittest"))
        test_start_evt = None
        if is_test_run:
            test_start_evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.TEST_STARTED,
                source=ObservationSource.PYTEST if "pytest" in cmd_lower else ObservationSource.TERMINAL,
                timestamp=started_at,
                payload={"command": clean_cmd_str},
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "terminal_session_id": term_session.session_id,
                },
            )

        # 6. Capture, truncate, and scrub output
        captured = CapturedOutput(payload.stdout, payload.stderr, max_bytes=DEFAULT_MAX_OUTPUT_BYTES)

        # 7. Parse test results and runtime errors
        test_summary = parse_test_results(clean_cmd_str, captured.stdout, captured.stderr, payload.exit_code)
        runtime_err = parse_runtime_error(captured.stderr) or parse_runtime_error(captured.stdout)

        # 8. Record COMMAND_FINISHED event
        cmd_status = "TIMEOUT" if payload.timed_out else ("SUCCESS" if payload.exit_code == 0 else "FAILED")
        finish_evt = self.observation_service.record_event(
            project_id=project_id,
            event_type=ObservationEventType.COMMAND_FINISHED,
            source=ObservationSource.TERMINAL,
            timestamp=finished_at,
            payload={
                "command": clean_cmd_str,
                "exit_code": payload.exit_code,
                "duration_ms": payload.duration_ms,
                "status": cmd_status,
                "stdout_snippet": captured.get_stdout_snippet(),
                "stderr_snippet": captured.get_stderr_snippet(),
                "output_truncated": captured.output_truncated,
            },
            provenance={
                "source": ObservationSource.TERMINAL,
                "terminal_session_id": term_session.session_id,
                "command": clean_cmd_str,
                "exit_code": payload.exit_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "output_metadata": captured.to_metadata(),
            },
        )

        recorded_evts = [start_evt]
        if test_start_evt:
            recorded_evts.append(test_start_evt)
        recorded_evts.append(finish_evt)

        # 9. Record TEST_FINISHED event if applicable
        if test_summary:
            test_evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.TEST_FINISHED,
                source=ObservationSource.PYTEST if test_summary.framework == "pytest" else ObservationSource.TERMINAL,
                timestamp=finished_at,
                payload={
                    "framework": test_summary.framework,
                    "status": test_summary.status,
                    "passed_count": test_summary.passed_count,
                    "failed_count": test_summary.failed_count,
                    "duration_ms": payload.duration_ms,
                    "failed_tests": test_summary.failed_tests,
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "terminal_session_id": term_session.session_id,
                    "command": clean_cmd_str,
                },
            )
            recorded_evts.append(test_evt)

        # 10. Record RUNTIME_ERROR if detected
        if runtime_err:
            err_evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.RUNTIME_ERROR,
                source=ObservationSource.TERMINAL,
                timestamp=finished_at,
                payload={
                    "error_kind": runtime_err.error_kind,
                    "message": runtime_err.message,
                    "file_path": runtime_err.file_path,
                    "line_number": runtime_err.line_number,
                    "function_name": runtime_err.function_name,
                    "error_signature": runtime_err.error_signature,
                    "raw_snippet": runtime_err.raw_snippet or captured.get_stderr_snippet(500),
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "terminal_session_id": term_session.session_id,
                    "command": clean_cmd_str,
                    "exit_code": payload.exit_code,
                },
            )
            recorded_evts.append(err_evt)

        # 11. Refresh session immediately
        refreshed_session = self.session_service.refresh_session(project_id)

        # 12. Determine compact notification text if an incident or test failure occurred
        notification = None
        if runtime_err:
            notification = f"Build Coach: Active runtime failure observed ({runtime_err.error_kind})."
        elif test_summary and test_summary.status == "FAILED":
            notification = f"Build Coach: Test suite failure ({test_summary.failed_count} tests failed)."
        elif test_summary and test_summary.status == "PASSED":
            notification = "Build Coach: Tests passed."

        return {
            "recorded": True,
            "observation_ids": [e.event_id for e in recorded_evts],
            "observation_count": len(recorded_evts),
            "session_state": refreshed_session.state.value,
            "next_action": refreshed_session.next_action.model_dump() if refreshed_session.next_action else None,
            "notification": notification,
        }
