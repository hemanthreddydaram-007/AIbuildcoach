"""Explicit local command runner orchestrating execution and observation (Milestone 12.9)."""

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
)
from backend.session.service import SessionService
from backend.session.models import BuildCoachSession
from backend.context_engine.secrets import detect_and_redact
from backend.runtime.capture import CapturedOutput, DEFAULT_MAX_OUTPUT_BYTES
from backend.runtime.parser import parse_runtime_error, parse_test_results, ParsedTestSummary


class CommandRunResult:
    """Outcome of an explicitly executed developer command."""
    def __init__(
        self,
        command_list: List[str],
        exit_code: int,
        duration_ms: float,
        timed_out: bool,
        captured_output: CapturedOutput,
        observation_events: List[ObservationEvent],
        session: BuildCoachSession,
        test_summary: Optional[ParsedTestSummary] = None,
        runtime_error: Optional[Any] = None,
    ):
        self.command_list = command_list
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.timed_out = timed_out
        self.captured_output = captured_output
        self.observation_events = observation_events
        self.session = session
        self.test_summary = test_summary
        self.runtime_error = runtime_error

    def to_dict(self) -> Dict[str, Any]:
        """Serializes result into clean structured JSON format (never exposing raw secrets)."""
        # Redact raw command arguments if any contain sensitive keys
        clean_cmd_str, _, _ = detect_and_redact(" ".join(self.command_list))
        clean_cmd_list = [detect_and_redact(arg)[0] for arg in self.command_list]

        incident_id = None
        if self.session.active_incident:
            incident_id = self.session.active_incident.get("event_id") or self.session.active_incident.get("error_signature")

        return {
            "status": "success",
            "command": clean_cmd_list,
            "command_str": clean_cmd_str,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 2),
            "timed_out": self.timed_out,
            "observation_ids": [e.event_id for e in self.observation_events],
            "observation_count": len(self.observation_events),
            "incident_id": incident_id,
            "session_state": self.session.state.value,
            "test_summary": {
                "framework": self.test_summary.framework,
                "status": self.test_summary.status,
                "passed_count": self.test_summary.passed_count,
                "failed_count": self.test_summary.failed_count,
            } if self.test_summary else None,
            "runtime_error": {
                "error_kind": self.runtime_error.error_kind,
                "message": self.runtime_error.message,
                "file_path": self.runtime_error.file_path,
                "line_number": self.runtime_error.line_number,
                "error_signature": self.runtime_error.error_signature,
            } if self.runtime_error else None,
            "next_action": self.session.next_action.model_dump() if self.session.next_action else None,
            "output_metadata": self.captured_output.to_metadata(),
        }


class RuntimeRunner:
    """Executes developer commands strictly in local project root and registers observations."""

    DEFAULT_TIMEOUT_SECONDS = 120.0  # 2 minute default finite limit

    def __init__(
        self,
        db: Database,
        observation_service: Optional[ObservationService] = None,
        session_service: Optional[SessionService] = None,
    ):
        self.db = db
        self.observation_service = observation_service or ObservationService(db)
        self.session_service = session_service or SessionService(db, self.observation_service)

    def run_command(
        self,
        project_id: str,
        command_list: List[str],
        timeout_seconds: Optional[float] = None,
        passthrough_output: bool = True,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandRunResult:
        """Executes a command synchronously in the validated project root.
        
        CRITICAL SAFETY BOUNDARIES:
        1. Explicitly user-triggered only (never automatic or from LLM/conversation text).
        2. Execution cwd is strictly the registered project's authoritative root_path.
        3. All stdout/stderr is bounded and redacted before DB persistence or event logging.
        4. Timeouts produce structured PROCESS_FINISHED status=TIMEOUT.
        5. Session is refreshed immediately upon completion.
        """
        if not command_list:
            raise ValueError("No command specified to run.")

        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' is not registered in Build Coach.")

        working_dir = Path(project.root_path).resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            raise FileNotFoundError(f"Project root directory does not exist: '{working_dir}'")

        timeout = timeout_seconds if timeout_seconds is not None else self.DEFAULT_TIMEOUT_SECONDS

        # Command name and sanitized command string
        cmd_name = Path(command_list[0]).name
        clean_cmd_str, _, _ = detect_and_redact(" ".join(command_list))

        started_at = utc_now_iso()
        start_time = time.perf_counter()

        # 1. Record COMMAND_STARTED event
        cmd_start_evt = self.observation_service.record_event(
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
                "command_name": cmd_name,
                "working_directory": str(working_dir),
            },
        )

        # 2. Check if this is a test command
        cmd_lower = " ".join(command_list).lower()
        is_test_run = any(k in cmd_lower for k in ("pytest", "npm test", "node --test", "jest", "vitest", "unittest"))
        test_start_evt = None
        if is_test_run:
            test_start_evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.TEST_STARTED,
                source=ObservationSource.PYTEST if "pytest" in cmd_lower else ObservationSource.TERMINAL,
                timestamp=started_at,
                payload={
                    "command": clean_cmd_str,
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "command_name": cmd_name,
                },
            )

        # 3. Execute process
        # On Windows, shell=True can be needed for builtins or npm.cmd, but list of args works safely
        # if using shell=False or finding executable. If command is string or list:
        raw_stdout = ""
        raw_stderr = ""
        exit_code = 0
        timed_out = False

        try:
            # We run subprocess capturing pipe
            # Use shell=True on Windows if command is a batch file or npm/yarn
            use_shell = os.name == "nt"
            proc = subprocess.Popen(
                command_list,
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=use_shell,
                errors="replace",
            )
            stdout_data, stderr_data = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
            raw_stdout = stdout_data or ""
            raw_stderr = stderr_data or ""

        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_data, stderr_data = proc.communicate()
            exit_code = -1
            timed_out = True
            raw_stdout = stdout_data or ""
            raw_stderr = (stderr_data or "") + f"\n[Process timed out after {timeout} seconds]"

        except Exception as exc:
            exit_code = 127
            raw_stderr = f"Execution failed: {exc}"

        end_time = time.perf_counter()
        finished_at = utc_now_iso()
        duration_ms = (end_time - start_time) * 1000.0

        # Passthrough raw output to console if requested so developer sees command output
        if passthrough_output:
            if raw_stdout:
                sys.stdout.write(raw_stdout)
                sys.stdout.flush()
            if raw_stderr:
                sys.stderr.write(raw_stderr)
                sys.stderr.flush()

        # 4. Capture, truncate, and redact output
        captured = CapturedOutput(raw_stdout, raw_stderr, max_bytes=max_output_bytes)

        recorded_events: List[ObservationEvent] = [cmd_start_evt]
        if test_start_evt:
            recorded_events.append(test_start_evt)

        # 5. Parse test results and runtime errors
        test_summary = parse_test_results(clean_cmd_str, captured.stdout, captured.stderr, exit_code)
        runtime_err = parse_runtime_error(captured.stderr) or parse_runtime_error(captured.stdout)

        # 6. Record COMMAND_FINISHED event
        cmd_status = "TIMEOUT" if timed_out else ("SUCCESS" if exit_code == 0 else "FAILED")
        cmd_finish_evt = self.observation_service.record_event(
            project_id=project_id,
            event_type=ObservationEventType.COMMAND_FINISHED,
            source=ObservationSource.TERMINAL,
            timestamp=finished_at,
            payload={
                "command": clean_cmd_str,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "status": cmd_status,
                "stdout_snippet": captured.get_stdout_snippet(),
                "stderr_snippet": captured.get_stderr_snippet(),
                "output_truncated": captured.output_truncated,
            },
            provenance={
                "source": ObservationSource.TERMINAL,
                "command": clean_cmd_str,
                "exit_code": exit_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "output_metadata": captured.to_metadata(),
            },
        )
        recorded_events.append(cmd_finish_evt)

        # 7. Record TEST_FINISHED event if applicable
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
                    "duration_ms": duration_ms,
                    "failed_tests": test_summary.failed_tests,
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "command": clean_cmd_str,
                },
            )
            recorded_events.append(test_evt)

        # 8. Record RUNTIME_ERROR event if error detected or non-zero exit with failure
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
                    "command": clean_cmd_str,
                    "exit_code": exit_code,
                },
            )
            recorded_events.append(err_evt)
        elif exit_code != 0 and not is_test_run:
            # Generic non-zero error when not caught by regex
            from backend.observation.normalizer import compute_deterministic_id
            err_sig = compute_deterministic_id("errsig", "NonZeroExitCode", str(exit_code))
            err_evt = self.observation_service.record_event(
                project_id=project_id,
                event_type=ObservationEventType.RUNTIME_ERROR,
                source=ObservationSource.TERMINAL,
                timestamp=finished_at,
                payload={
                    "error_kind": "NonZeroExitCode",
                    "message": f"Command exited with non-zero exit code {exit_code}",
                    "error_signature": err_sig,
                    "raw_snippet": captured.get_stderr_snippet(500) or captured.get_stdout_snippet(500),
                },
                provenance={
                    "source": ObservationSource.TERMINAL,
                    "command": clean_cmd_str,
                    "exit_code": exit_code,
                },
            )
            recorded_events.append(err_evt)

        # 9. Refresh unified session immediately
        refreshed_session = self.session_service.refresh_session(project_id)

        return CommandRunResult(
            command_list=command_list,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            captured_output=captured,
            observation_events=recorded_events,
            session=refreshed_session,
            test_summary=test_summary,
            runtime_error=runtime_err,
        )


def format_human_command_run(result: CommandRunResult, project_name: str) -> str:
    """Renders human-readable summary augmenting the terminal output."""
    clean_cmd = " ".join(result.command_list)
    clean_cmd, _, _ = detect_and_redact(clean_cmd)

    duration_sec = result.duration_ms / 1000.0
    status_str = "TIMEOUT" if result.timed_out else ("SUCCESS" if result.exit_code == 0 else "FAILED")

    lines = [
        "",
        "BUILD COACH RUNNER",
        "------------------------",
        "",
        "Project:",
        project_name,
        "",
        "Command:",
        clean_cmd,
        "",
        "Result:",
        status_str,
        "",
        "Duration:",
        f"{duration_sec:.2f}s",
    ]

    if result.runtime_error:
        lines.extend([
            "",
            "Incident:",
            f"{result.runtime_error.error_kind}: {result.runtime_error.message}",
        ])
    elif result.test_summary and result.test_summary.status == "FAILED":
        lines.extend([
            "",
            "Incident:",
            f"Test Suite Failure ({result.test_summary.failed_count} failed)",
        ])

    lines.extend([
        "",
        "Build Coach State:",
        result.session.state.value,
    ])

    if result.session.next_action:
        act = result.session.next_action
        lines.extend([
            "",
            "Next action:",
            f"{act.action_type.value}: {act.title}",
        ])
    else:
        lines.extend([
            "",
            "Next action:",
            "No immediate action required.",
        ])

    lines.append("")
    return "\n".join(lines)
