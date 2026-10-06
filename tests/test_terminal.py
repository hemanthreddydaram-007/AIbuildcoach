"""Comprehensive tests for Milestone 12.10: Transparent Terminal Integration.

Test coverage:
1. Terminal integration enable / disable / status lifecycle and state transitions.
2. Generating transparent PowerShell hook script without errors.
3. Disabled integration records zero events, returns recorded=False.
4. Enabled integration captures command execution strictly once.
5. Command lifecycle events: COMMAND_STARTED, COMMAND_FINISHED.
6. Test execution detection: TEST_STARTED, TEST_FINISHED, framework recognition.
7. Runtime failure detection: RUNTIME_ERROR event with error kind and snippet.
8. Secret redaction across command string, stdout, and stderr.
9. Interactive exclusions (vim, ssh, REPLs) bypass stream capture safely.
10. Project isolation: terminal commands do not pollute other projects.
11. Unified session state and guidance refresh upon terminal execution.
12. CLI commands: terminal enable, disable, status, and hook via main().
"""

import sys
import json
import pytest
from pathlib import Path
from backend.domain.models import Project, utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import ObservationEventType, ObservationSource
from backend.session.service import SessionService
from backend.session.models import SessionState
from backend.terminal.protocol import (
    ShellType,
    IntegrationStatus,
    TerminalCommandPayload,
)
from backend.terminal.session import TerminalSessionManager, INTERACTIVE_EXCLUSIONS
from backend.terminal.integration import TerminalIntegrationManager
from backend.cli.main import main as cli_main


@pytest.fixture
def terminal_env(tmp_path: Path):
    """Sets up an isolated database and registered projects for terminal testing."""
    db_path = tmp_path / "state.db"
    db = Database(db_path)

    # Project A
    root_a = tmp_path / "proj_alpha"
    root_a.mkdir(parents=True, exist_ok=True)
    proj_a = Project(
        id="proj_alpha_123",
        name="Project Alpha",
        root_path=str(root_a),
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    db.upsert_project(proj_a)

    # Project B
    root_b = tmp_path / "proj_beta"
    root_b.mkdir(parents=True, exist_ok=True)
    proj_b = Project(
        id="proj_beta_456",
        name="Project Beta",
        root_path=str(root_b),
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    db.upsert_project(proj_b)

    obs_service = ObservationService(db)
    session_service = SessionService(db, obs_service)
    session_mgr = TerminalSessionManager(db, obs_service, session_service)
    integration_mgr = TerminalIntegrationManager(db, session_mgr)

    return {
        "db": db,
        "root_a": root_a,
        "root_b": root_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "obs_service": obs_service,
        "session_service": session_service,
        "session_mgr": session_mgr,
        "integration_mgr": integration_mgr,
    }


def test_terminal_integration_lifecycle(terminal_env):
    """Verifies enable, disable, and status transitions and script generation."""
    mgr = terminal_env["integration_mgr"]
    proj_id = terminal_env["proj_a"].id
    root_a = terminal_env["root_a"]

    # Initial status: DISABLED
    st = mgr.get_status(proj_id)
    assert st.status == IntegrationStatus.DISABLED.value
    assert st.hook_script_path is None

    # Enable for PowerShell
    en = mgr.enable(proj_id, shell_type="powershell")
    assert en.status == IntegrationStatus.ENABLED.value
    assert en.hook_script_path is not None
    assert Path(en.hook_script_path).exists()
    assert ".buildcoach" in en.hook_script_path

    # Verify script content
    script_text = Path(en.hook_script_path).read_text(encoding="utf-8")
    assert f"$env:BUILDCOACH_PROJECT_ID = \"{proj_id}\"" in script_text
    assert "Invoke-BuildCoachHook" in script_text
    assert "bc-run" in script_text

    # Check status matches
    st_active = mgr.get_status(proj_id)
    assert st_active.status == IntegrationStatus.ENABLED.value
    assert st_active.hook_script_path == en.hook_script_path

    # Disable
    dis = mgr.disable(proj_id)
    assert dis.status == IntegrationStatus.DISABLED.value

    # Check status is disabled
    st_disabled = mgr.get_status(proj_id)
    assert st_disabled.status == IntegrationStatus.DISABLED.value


def test_disabled_integration_records_zero_observations(terminal_env):
    """Verifies that terminal commands record nothing when integration is disabled."""
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_id = terminal_env["proj_a"].id

    payload = TerminalCommandPayload(
        command="pytest tests/",
        exit_code=0,
        stdout="5 passed in 0.2s",
        stderr="",
    )

    result = session_mgr.record_terminal_command(proj_id, payload)
    assert result is None

    evts = obs_service.get_timeline(proj_id)
    assert len(evts) == 0


def test_enabled_integration_records_command_lifecycle(terminal_env):
    """Verifies enabled integration records COMMAND_STARTED and COMMAND_FINISHED."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_id = terminal_env["proj_a"].id

    mgr.enable(proj_id)

    payload = TerminalCommandPayload(
        command="python build.py",
        exit_code=0,
        duration_ms=45.0,
        stdout="Build succeeded.",
        stderr="",
    )

    result = session_mgr.record_terminal_command(proj_id, payload)
    assert result is not None
    assert result["recorded"] is True
    assert result["observation_count"] == 2  # START + FINISH

    evts = obs_service.get_timeline(proj_id)
    types = [e.event_type for e in evts]
    assert ObservationEventType.COMMAND_STARTED in types
    assert ObservationEventType.COMMAND_FINISHED in types

    finish_evt = next(e for e in evts if e.event_type == ObservationEventType.COMMAND_FINISHED)
    assert finish_evt.payload["status"] == "SUCCESS"
    assert finish_evt.payload["stdout_snippet"] == "Build succeeded."


def test_test_suite_and_error_detection_in_terminal(terminal_env):
    """Verifies test suite results and runtime errors are parsed from terminal output."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_id = terminal_env["proj_a"].id

    mgr.enable(proj_id)

    payload = TerminalCommandPayload(
        command="pytest tests/test_core.py",
        exit_code=1,
        duration_ms=120.0,
        stdout="FAILED tests/test_core.py::test_fail\n1 failed in 0.12s",
        stderr="Traceback (most recent call last):\n  File \"core.py\", line 42, in run\n    raise ValueError('bad parameter')\nValueError: bad parameter",
    )

    result = session_mgr.record_terminal_command(proj_id, payload)
    assert result["recorded"] is True

    evts = obs_service.get_timeline(proj_id)
    types = [e.event_type for e in evts]
    assert ObservationEventType.TEST_STARTED in types
    assert ObservationEventType.COMMAND_FINISHED in types
    assert ObservationEventType.TEST_FINISHED in types
    assert ObservationEventType.RUNTIME_ERROR in types

    err_evt = next(e for e in evts if e.event_type == ObservationEventType.RUNTIME_ERROR)
    assert err_evt.payload["error_kind"] == "ValueError"
    assert err_evt.payload["file_path"] == "core.py"
    assert err_evt.payload["line_number"] == 42


def test_secret_redaction_in_terminal_command_and_output(terminal_env):
    """Verifies secrets are redacted before observation persistence."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_id = terminal_env["proj_a"].id

    mgr.enable(proj_id)

    raw_token = "ghp_" + "A" * 36
    api_key = "AIza" + "1" * 35

    payload = TerminalCommandPayload(
        command=f"curl -H 'Authorization: Bearer {raw_token}' https://api.github.com",
        exit_code=1,
        stdout=f"Response with API key {api_key}",
        stderr=f"Failed request token={raw_token}",
    )

    result = session_mgr.record_terminal_command(proj_id, payload)
    assert result["recorded"] is True

    evts = obs_service.get_timeline(proj_id)
    for evt in evts:
        evt_str = json.dumps(evt.model_dump())
        assert raw_token not in evt_str, "Raw GitHub token leaked into observation timeline!"
        assert api_key not in evt_str, "Raw API key leaked into observation timeline!"
        assert "[REDACTED" in evt_str


def test_interactive_command_exclusion(terminal_env):
    """Verifies interactive commands like vim or ssh do not attempt stream capture."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_id = terminal_env["proj_a"].id

    mgr.enable(proj_id)

    payload = TerminalCommandPayload(
        command="vim",
        exit_code=0,
    )

    result = session_mgr.record_terminal_command(proj_id, payload)
    assert result["recorded"] is True
    assert result["interactive"] is True
    assert len(result["event_ids"]) == 1

    evts = obs_service.get_timeline(proj_id)
    assert len(evts) == 1
    assert evts[0].event_type == ObservationEventType.COMMAND_STARTED
    assert evts[0].payload["interactive"] is True


def test_project_isolation(terminal_env):
    """Verifies terminal commands recorded for Project Alpha do not appear in Project Beta."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    obs_service = terminal_env["obs_service"]
    proj_a = terminal_env["proj_a"].id
    proj_b = terminal_env["proj_b"].id

    mgr.enable(proj_a)
    mgr.enable(proj_b)

    payload_a = TerminalCommandPayload(
        command="echo alpha",
        exit_code=0,
        stdout="alpha\n",
    )
    session_mgr.record_terminal_command(proj_a, payload_a)

    evts_a = obs_service.get_timeline(proj_a)
    evts_b = obs_service.get_timeline(proj_b)

    assert len(evts_a) >= 2
    assert len(evts_b) == 0


def test_session_state_and_guidance_refresh(terminal_env):
    """Verifies terminal test failure transitions session to ACTIVE_FAILURE and fixes retire guidance."""
    mgr = terminal_env["integration_mgr"]
    session_mgr = terminal_env["session_mgr"]
    session_service = terminal_env["session_service"]
    proj_id = terminal_env["proj_a"].id

    mgr.enable(proj_id)

    # 1. Run failing test command
    fail_payload = TerminalCommandPayload(
        command="pytest tests/test_login.py",
        exit_code=1,
        stdout="FAILED tests/test_login.py::test_auth\n1 failed in 0.1s",
        stderr="Traceback (most recent call last):\n  File \"auth.py\", line 10, in auth\n    raise ConnectionError('db down')\nConnectionError: db down",
    )
    session_mgr.record_terminal_command(proj_id, fail_payload)

    session = session_service.get_or_create_session(proj_id)
    assert session.state == SessionState.INVESTIGATING
    assert session.active_incident is not None

    # 2. Run passing test command
    pass_payload = TerminalCommandPayload(
        command="pytest tests/test_login.py",
        exit_code=0,
        stdout="1 passed in 0.05s",
        stderr="",
    )
    session_mgr.record_terminal_command(proj_id, pass_payload)

    session_fixed = session_service.refresh_session(proj_id)
    # The failing incident was followed by a passing test
    assert session_fixed.state in (SessionState.VERIFYING, SessionState.READY)
    assert session_fixed.verification_summary is not None
    assert session_fixed.verification_summary.targeted_test_observed is True


def test_cli_terminal_subcommands(terminal_env, capsys):
    """Verifies CLI terminal subcommands: enable, status, disable, hook."""
    from backend.cli.runner import resolve_workspace

    root_a = terminal_env["root_a"]
    ws_root, ws_db, ws_project = resolve_workspace(str(root_a))
    proj_id = ws_project.id

    # 1. CLI status (initially disabled)
    ret = cli_main(["--project-root", str(root_a), "terminal", "status", proj_id, "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    assert data["status"] == "DISABLED"

    # 2. CLI enable
    ret = cli_main(["--project-root", str(root_a), "terminal", "enable", proj_id, "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    assert data["status"] == "ENABLED"

    # 3. CLI hook single execution
    ret = cli_main(["--project-root", str(root_a), "terminal", "hook", proj_id, "--json", "--", sys.executable, "-c", "print('hello terminal')"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "{" in out
    data = json.loads(out[out.index("{"):])["data"]
    assert data["recorded"] is True
    assert data["result"]["exit_code"] == 0

    # 4. CLI disable
    ret = cli_main(["--project-root", str(root_a), "terminal", "disable", proj_id, "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    assert data["status"] == "DISABLED"

    # 5. CLI hook when disabled (runs command strictly once, records=False)
    ret = cli_main(["--project-root", str(root_a), "terminal", "hook", proj_id, "--json", "--", sys.executable, "-c", "print('disabled run')"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "{" in out
    data = json.loads(out[out.index("{"):])["data"]
    assert data["recorded"] is False
