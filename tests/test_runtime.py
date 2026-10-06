"""Comprehensive tests for Milestone 12.9: Real Developer Activity Capture."""

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
from backend.runtime.capture import CapturedOutput
from backend.runtime.parser import parse_runtime_error, parse_test_results
from backend.runtime.runner import RuntimeRunner, format_human_command_run
from backend.cli.main import main as cli_main


@pytest.fixture
def test_env(tmp_path: Path):
    """Sets up an isolated database and registered projects for testing."""
    db_path = tmp_path / "state.db"
    db = Database(db_path)

    # Project A
    root_a = tmp_path / "project_a"
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
    root_b = tmp_path / "project_b"
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
    runner = RuntimeRunner(db, obs_service, session_service)

    return {
        "db": db,
        "root_a": root_a,
        "root_b": root_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "obs_service": obs_service,
        "session_service": session_service,
        "runner": runner,
    }


# ==============================================================================
# Critical Test A: Command succeeds
# Expected: COMMAND_STARTED, COMMAND_FINISHED, SUCCESS
# ==============================================================================
def test_critical_test_a_command_succeeds(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]

    # Run a clean python echo command
    res = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "-c", "print('hello success')"],
        passthrough_output=False,
    )

    assert res.exit_code == 0
    assert not res.timed_out
    assert "hello success" in res.captured_output.stdout

    # Check recorded events
    evt_types = [e.event_type for e in res.observation_events]
    assert ObservationEventType.COMMAND_STARTED in evt_types
    assert ObservationEventType.COMMAND_FINISHED in evt_types

    finish_evt = next(e for e in res.observation_events if e.event_type == ObservationEventType.COMMAND_FINISHED)
    assert finish_evt.payload["status"] == "SUCCESS"
    assert finish_evt.payload["exit_code"] == 0

    # Ensure to_dict() serialization works
    d = res.to_dict()
    assert d["status"] == "success"
    assert d["exit_code"] == 0


# ==============================================================================
# Critical Test B: Command exits non-zero + runtime error
# Expected: COMMAND_FINISHED, RUNTIME_ERROR, session = INVESTIGATING
# ==============================================================================
def test_critical_test_b_command_fails_with_runtime_error(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]

    # Run command that raises a known runtime exception
    res = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "-c", "raise KeyError('missing_cache_key')"],
        passthrough_output=False,
    )

    assert res.exit_code != 0
    evt_types = [e.event_type for e in res.observation_events]
    assert ObservationEventType.COMMAND_FINISHED in evt_types
    assert ObservationEventType.RUNTIME_ERROR in evt_types

    err_evt = next(e for e in res.observation_events if e.event_type == ObservationEventType.RUNTIME_ERROR)
    assert err_evt.payload["error_kind"] == "KeyError"
    assert "missing_cache_key" in err_evt.payload["message"]

    # Session must transition to INVESTIGATING
    assert res.session.state == SessionState.INVESTIGATING


# ==============================================================================
# Critical Test C: Same command + successful verification
# Expected: Previous error no longer active, session recalculates
# ==============================================================================
def test_critical_test_c_error_recalculated_after_successful_verification(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]
    root_a: Path = test_env["root_a"]

    # 1. Create failing script
    script_file = root_a / "app.py"
    script_file.write_text("raise ValueError('broken config')\n", encoding="utf-8")

    res_fail = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "app.py"],
        passthrough_output=False,
    )
    assert res_fail.exit_code != 0
    assert res_fail.session.state == SessionState.INVESTIGATING

    # 2. Fix the script
    script_file.write_text("print('config loaded cleanly')\n", encoding="utf-8")

    # 3. Rerun the same command
    res_pass = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "app.py"],
        passthrough_output=False,
    )
    assert res_pass.exit_code == 0

    # 4. Session recalculates away from INVESTIGATING
    assert res_pass.session.state != SessionState.INVESTIGATING


# ==============================================================================
# Critical Test D: Secret appears in stderr
# Expected: stored observation contains [REDACTED], never original secret
# ==============================================================================
def test_critical_test_d_secret_redaction_before_persistence(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]
    obs_service: ObservationService = test_env["obs_service"]

    secret_key = "sk-live12345678901234567890abcdef"
    script = f"import sys; sys.stderr.write('Failed with api_key={secret_key}\\n'); sys.exit(1)"

    res = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "-c", script],
        passthrough_output=False,
    )

    # 1. Raw secret must NOT be in captured output or to_dict()
    assert secret_key not in res.captured_output.stderr
    assert "[REDACTED]" in res.captured_output.stderr
    assert secret_key not in json.dumps(res.to_dict())

    # 2. Raw secret must NOT be in any database observation events
    timeline = obs_service.get_timeline(proj_a.id)
    for evt in timeline:
        payload_str = json.dumps(evt.payload)
        prov_str = json.dumps(evt.provenance)
        assert secret_key not in payload_str
        assert secret_key not in prov_str


# ==============================================================================
# Test E: Timeout handling
# Expected: PROCESS_FINISHED / COMMAND_FINISHED with status TIMEOUT, exit_code -1
# ==============================================================================
def test_command_timeout_handling(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]

    # Sleep longer than timeout
    res = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=0.5,
        passthrough_output=False,
    )

    assert res.timed_out is True
    finish_evt = next(e for e in res.observation_events if e.event_type == ObservationEventType.COMMAND_FINISHED)
    assert finish_evt.payload["status"] == "TIMEOUT"


# ==============================================================================
# Test F: Output bounding and truncation
# ==============================================================================
def test_output_bounding_and_truncation():
    large_output = "Line " * 20000  # ~100 KB
    captured = CapturedOutput(raw_stdout=large_output, raw_stderr="short error", max_bytes=1000)

    assert captured.output_truncated is True
    assert captured.stdout_truncated is True
    assert not captured.stderr_truncated
    assert len(captured.stdout) <= 1500
    assert "[... OUTPUT TRUNCATED BY BUILD COACH" in captured.stdout


# ==============================================================================
# Test G: Strict project isolation and working directory enforcement
# ==============================================================================
def test_project_isolation_and_working_dir(test_env):
    runner: RuntimeRunner = test_env["runner"]
    proj_a: Project = test_env["proj_a"]
    proj_b: Project = test_env["proj_b"]
    root_a: Path = test_env["root_a"]
    obs_service: ObservationService = test_env["obs_service"]

    # Command prints current working directory
    res_a = runner.run_command(
        project_id=proj_a.id,
        command_list=[sys.executable, "-c", "import os; print(os.getcwd())"],
        passthrough_output=False,
    )
    assert Path(res_a.captured_output.stdout.strip()).resolve() == root_a.resolve()

    # Verify Project B timeline contains 0 events
    timeline_b = obs_service.get_timeline(proj_b.id)
    assert len(timeline_b) == 0

    # Non-existent project rejected
    with pytest.raises(ValueError, match="not registered"):
        runner.run_command("nonexistent_id", ["echo", "test"])


# ==============================================================================
# Test H: Test result normalization (pytest, node, jest)
# ==============================================================================
def test_test_result_normalization():
    # Pytest pass
    py_pass_out = "=== 15 passed in 0.45s ==="
    res = parse_test_results("pytest tests/", py_pass_out, "", 0)
    assert res is not None
    assert res.framework == "pytest"
    assert res.status == "PASSED"
    assert res.passed_count == 15
    assert res.failed_count == 0

    # Pytest fail
    py_fail_out = "FAILED tests/test_foo.py::test_bar\n=== 2 failed, 10 passed in 1.20s ==="
    res_fail = parse_test_results("pytest tests/", py_fail_out, "", 1)
    assert res_fail is not None
    assert res_fail.status == "FAILED"
    assert res_fail.failed_count == 2
    assert res_fail.passed_count == 10
    assert "tests/test_foo.py::test_bar" in res_fail.failed_tests

    # Node tap summary
    tap_out = "# pass 64\n# fail 0"
    res_node = parse_test_results("npm test", tap_out, "", 0)
    assert res_node is not None
    assert res_node.framework == "node-test"
    assert res_node.status == "PASSED"


# ==============================================================================
# Test I: CLI execution mode (--json and human text)
# ==============================================================================
# ==============================================================================
# Test I: CLI execution mode (--json and human text)
# ==============================================================================
def test_cli_runner_json_and_human(test_env, capsys):
    from backend.cli.runner import run_execute, resolve_workspace

    # Resolve actual workspace project for root_a
    root_a = test_env["root_a"]
    ws_root, ws_db, ws_project = resolve_workspace(str(root_a))

    # 1. Test CLI with --json using workspace registered project
    code = cli_main([
        "--project-root", str(root_a),
        "run", ws_project.id, "--json", "--",
        sys.executable, "-c", "print('cli_json_test')"
    ])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "success"
    assert data["data"]["exit_code"] == 0

    # 2. Test CLI without -- (mandatory separator check)
    code_err = cli_main([
        "--project-root", str(root_a),
        "run", ws_project.id,
    ])
    assert code_err != 0

    # 3. Test run_execute helper directly
    res = run_execute(
        db=ws_db,
        project_id=ws_project.id,
        command_list=[sys.executable, "-c", "print('direct_run')"],
        passthrough_output=False,
    )
    assert res["result"]["exit_code"] == 0
    assert "BUILD COACH RUNNER" in res["human_text"]
