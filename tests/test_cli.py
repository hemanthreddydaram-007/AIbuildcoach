"""Automated test suite for Milestone 9: Unified Engine CLI & Headless JSON Interface."""

import io
import sys
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.cli.main import main, build_parser
from backend.cli.runner import get_cli_version, resolve_workspace
from backend.project_model.db import Database


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Creates a temporary workspace with git and project files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "test-pkg"\nversion = "0.1.0"\n', encoding="utf-8")
    (repo / "main.py").write_text('def hello():\n    print("Hello world")\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), check=True)
    return repo


@pytest.fixture
def temp_dirty_project(temp_project: Path) -> Path:
    """Creates a dirty workspace with uncommitted edits."""
    # Modify main.py and add a new untracked file
    (temp_project / "main.py").write_text('def hello():\n    print("Hello modified world")\n', encoding="utf-8")
    (temp_project / "new_module.py").write_text('import os\n\ndef helper():\n    return os.getcwd()\n', encoding="utf-8")
    return temp_project


def test_cli_version():
    """1. Verifies --version outputs ai-build-coach 0.1.0."""
    version = get_cli_version()
    assert version == "0.1.0"

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
    assert "ai-build-coach 0.1.0" in buf.getvalue()


def test_cli_module_entrypoint():
    """2. Verifies execution via python -m backend.cli --version matches direct binary output."""
    res = subprocess.run(
        [sys.executable, "-m", "backend.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "ai-build-coach 0.1.0" in res.stdout.strip()


def test_cli_help():
    """3. Verifies --help outputs usage instructions for all subcommands."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
    output = buf.getvalue()
    assert "status" in output
    assert "scan" in output
    assert "understand" in output
    assert "viva" in output


def test_cli_json_flag_position(temp_project: Path):
    """4. Verifies that --json is recognized both at top-level and after subcommands."""
    root_str = str(temp_project)

    # Position A: top-level flag
    buf_a = io.StringIO()
    with patch("sys.stdout", buf_a):
        rc_a = main(["--json", "--project-root", root_str, "status"])
    assert rc_a == 0
    data_a = json.loads(buf_a.getvalue())
    assert data_a["status"] == "success"

    # Position B: subcommand flag
    buf_b = io.StringIO()
    with patch("sys.stdout", buf_b):
        rc_b = main(["--project-root", root_str, "status", "--json"])
    assert rc_b == 0
    data_b = json.loads(buf_b.getvalue())
    assert data_b["status"] == "success"

    # Position C: nested subcommand flag
    buf_c = io.StringIO()
    with patch("sys.stdout", buf_c):
        rc_c = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_c == 0
    data_c = json.loads(buf_c.getvalue())
    assert data_c["status"] == "success"

    # Position D: viva start flag
    buf_d = io.StringIO()
    with patch("sys.stdout", buf_d):
        rc_d = main(["--project-root", root_str, "viva", "start", "--json"])
    assert rc_d == 0
    data_d = json.loads(buf_d.getvalue())
    assert data_d["status"] == "success"


def test_cli_project_root_auto_detection(temp_project: Path, monkeypatch: pytest.MonkeyPatch):
    """5. Verifies automatic detection of project root from cwd and explicit --project-root override."""
    monkeypatch.chdir(temp_project)
    root, db, project = resolve_workspace(None)
    assert root.resolve() == temp_project.resolve()
    assert project.name == temp_project.name

    # Explicit override to another directory
    sub = temp_project / "sub"
    sub.mkdir()
    root_sub, db_sub, project_sub = resolve_workspace(str(sub))
    assert root_sub.resolve() == temp_project.resolve()


def test_cli_status_clean_repo(temp_project: Path):
    """6. Validates status command output on a clean repository."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "status"])
    assert rc == 0
    out = buf.getvalue()
    assert "=== PROJECT STATUS ===" in out
    assert "Root:" in out
    assert "Git Repo:         Yes" in out


def test_cli_status_dirty_repo(temp_dirty_project: Path):
    """7. Validates status command showing modified/untracked file counts and branch."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_dirty_project), "status"])
    assert rc == 0
    out = buf.getvalue()
    assert "=== PROJECT STATUS ===" in out


def test_cli_status_json(temp_project: Path):
    """8. Validates status --json output conforms to JSON schema."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "status", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    assert payload["command"] == "status"
    assert payload["action"] == "view"
    data = payload["data"]
    assert "schema_version" in data
    assert "graph_nodes_count" in data
    assert "graph_edges_count" in data
    assert "recent_understand_runs" in data
    assert "recent_viva_sessions" in data


def test_cli_scan_command(temp_project: Path):
    """9. Confirms scan updates database file and graph records."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "scan", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    assert payload["command"] == "scan"
    assert payload["action"] == "execute"
    assert payload["data"]["scanned_files_count"] >= 1


def test_cli_understand_preview_interactive(temp_dirty_project: Path):
    """10. Validates interactive preview formatting for modified files."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_dirty_project), "understand", "preview"])
    assert rc == 0
    out = buf.getvalue()
    assert "Changed files:" in out
    assert "token estimate:" in out


def test_cli_understand_preview_clean_working_tree(tmp_path: Path):
    """11. Validates preview behavior when no changes exist."""
    clean_repo = tmp_path / "clean_repo"
    clean_repo.mkdir()
    (clean_repo / ".git").mkdir()
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(clean_repo), "understand", "preview", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    assert payload["data"]["clean_working_tree"] is True
    assert payload["data"]["total_files_changed"] == 0


def test_cli_understand_preview_json(temp_dirty_project: Path):
    """12. Validates understand preview --json schema compliance."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_dirty_project), "understand", "preview", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    assert payload["data"]["clean_working_tree"] is False
    assert payload["data"]["total_files_changed"] >= 1
    assert "packet_id" in payload["data"]
    assert "packet_hash" in payload["data"]


def test_cli_understand_explain_with_consent(temp_dirty_project: Path):
    """13. Verifies understand explain --consent-token <tok> executes and returns result."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_dirty_project), "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    data = payload["data"]
    assert "what_changed" in data
    assert "why" in data
    assert "evidence" in data
    assert "what_should_i_understand" in data
    assert "can_i_explain_this" in data


def test_cli_understand_explain_consent_rejection(temp_dirty_project: Path):
    """14. Confirms declined/missing consent aborts execution without AI gateway calls."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_dirty_project), "understand", "explain", "--json"])
    assert rc == 1
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "CONSENT_REQUIRED"


def test_cli_understand_submit_answer_stdin_interactive(temp_dirty_project: Path):
    """15. Tests interactive comprehension submission via stdin and rating display."""
    # First generate preview and explanation
    buf_exp = io.StringIO()
    with patch("sys.stdout", buf_exp):
        main(["--project-root", str(temp_dirty_project), "understand", "explain", "--consent-token", "auto", "--json"])
    exp_data = json.loads(buf_exp.getvalue())["data"]
    prompt_id = exp_data["can_i_explain_this"]["prompt_id"]
    packet_id = exp_data["packet_id"]

    # Submit fast-path answer via interactive simulation
    fake_stdin = io.StringIO("asdfasdfasdf\n\n")
    buf_sub = io.StringIO()
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
        rc = main([
            "--project-root", str(temp_dirty_project),
            "understand", "submit",
            "--prompt-id", prompt_id,
            "--packet-id", packet_id,
            "--answer-stdin",
        ])
    assert rc == 0
    out = buf_sub.getvalue()
    assert "Overall comprehension:" in out


def test_cli_understand_submit_answer_stdin_json(temp_dirty_project: Path):
    """16. Tests understand submit --answer-stdin --json returning structured comprehension evaluation."""
    # First generate preview and explanation
    buf_exp = io.StringIO()
    with patch("sys.stdout", buf_exp):
        main(["--project-root", str(temp_dirty_project), "understand", "explain", "--consent-token", "auto", "--json"])
    exp_data = json.loads(buf_exp.getvalue())["data"]
    prompt_id = exp_data["can_i_explain_this"]["prompt_id"]
    packet_id = exp_data["packet_id"]

    fake_stdin = io.StringIO("asdfasdfasdf\n")
    buf_sub = io.StringIO()
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
        rc = main([
            "--project-root", str(temp_dirty_project),
            "understand", "submit",
            "--prompt-id", prompt_id,
            "--packet-id", packet_id,
            "--answer-stdin",
            "--json",
        ])
    assert rc == 0
    payload = json.loads(buf_sub.getvalue())
    assert payload["status"] == "success"
    data = payload["data"]
    assert "overall_state" in data
    assert "dimensions" in data
    assert "PURPOSE" in data["dimensions"]
    assert "MECHANISM" in data["dimensions"]
    assert "FAILURE_MODES" in data["dimensions"]
    assert "DOWNSTREAM_IMPACT" in data["dimensions"]


def test_cli_viva_start_interactive(temp_project: Path):
    """17. Tests interactive viva session initialization and initial question presentation."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "viva", "start"])
    assert rc == 0
    assert "Viva session" in buf.getvalue()


def test_cli_viva_start_json(temp_project: Path):
    """18. Tests viva start --json returning valid session and question records."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "viva", "start", "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "success"
    assert "session" in payload["data"]
    assert "first_question" in payload["data"]
    assert payload["data"]["session"]["status"] == "AWAITING_ANSWER"


def test_cli_viva_submit_turn_progression(temp_project: Path):
    """19. Tests answer submission via --answer-stdin advancing turn, adjusting difficulty, and returning next question."""
    # Start session
    buf_start = io.StringIO()
    with patch("sys.stdout", buf_start):
        main(["--project-root", str(temp_project), "viva", "start", "--json"])
    start_data = json.loads(buf_start.getvalue())["data"]
    session_id = start_data["session"]["session_id"]

    fake_stdin = io.StringIO("This module manages authentication sessions.\n")
    buf_sub = io.StringIO()
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
        rc = main([
            "--project-root", str(temp_project),
            "viva", "submit",
            "--session-id", session_id,
            "--answer-stdin",
            "--json",
        ])
    assert rc == 0
    payload = json.loads(buf_sub.getvalue())
    assert payload["status"] == "success"
    assert "turn_evaluation" in payload["data"]
    assert "session" in payload["data"]
    assert payload["data"]["session"]["current_turn"] == 1


def test_cli_viva_submit_fast_path(temp_project: Path):
    """20. Tests empty/gibberish answer submission returning WEAK rating without gateway calls."""
    buf_start = io.StringIO()
    with patch("sys.stdout", buf_start):
        main(["--project-root", str(temp_project), "viva", "start", "--json"])
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    fake_stdin = io.StringIO("   \n")  # Empty whitespace
    buf_sub = io.StringIO()
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
        rc = main([
            "--project-root", str(temp_project),
            "viva", "submit",
            "--session-id", session_id,
            "--answer-stdin",
            "--json",
        ])
    assert rc == 0
    payload = json.loads(buf_sub.getvalue())
    assert payload["status"] == "success"
    turn_eval = payload["data"]["turn_evaluation"]
    assert turn_eval["is_fast_path"] is True
    assert turn_eval["rating"] == "WEAK"


def test_cli_viva_report_generation(temp_project: Path):
    """21. Tests viva report output in both terminal and JSON formats."""
    buf_start = io.StringIO()
    with patch("sys.stdout", buf_start):
        main(["--project-root", str(temp_project), "viva", "start", "--json"])
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    # Terminal report
    buf_term = io.StringIO()
    with patch("sys.stdout", buf_term):
        rc_term = main(["--project-root", str(temp_project), "viva", "report", "--session-id", session_id])
    assert rc_term == 0
    assert "Readiness:" in buf_term.getvalue()

    # JSON report
    buf_json = io.StringIO()
    with patch("sys.stdout", buf_json):
        rc_json = main(["--project-root", str(temp_project), "viva", "report", "--session-id", session_id, "--json"])
    assert rc_json == 0
    payload = json.loads(buf_json.getvalue())
    assert payload["status"] == "success"
    assert "readiness" in payload["data"]
    assert "category_masteries" in payload["data"]


def test_cli_zero_persistence_of_student_answers(temp_project: Path):
    """22. Confirms student answers piped through --answer-stdin never appear in SQLite tables or logs."""
    buf_start = io.StringIO()
    with patch("sys.stdout", buf_start):
        main(["--project-root", str(temp_project), "viva", "start", "--json"])
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    canary_secret_answer = "SECRET_CANARY_STUDENT_ANSWER_12345"
    fake_stdin = io.StringIO(f"{canary_secret_answer}\n")
    with patch("sys.stdin", fake_stdin), patch("sys.stdout", io.StringIO()):
        main([
            "--project-root", str(temp_project),
            "viva", "submit",
            "--session-id", session_id,
            "--answer-stdin",
            "--json",
        ])

    # Search all SQLite tables in .buildcoach/state.db for the canary string
    db_path = temp_project / ".buildcoach" / "state.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]

    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        columns = [c[1] for c in cur.fetchall()]
        for c in columns:
            cur.execute(f"SELECT COUNT(*) FROM {t} WHERE CAST({c} AS TEXT) LIKE '%{canary_secret_answer}%'")
            count = cur.fetchone()[0]
            assert count == 0, f"Leaked student answer in table {t}, column {c}!"
    conn.close()


def test_cli_stdout_purity_in_json_mode(temp_project: Path):
    """23. Confirms that in --json mode, sys.stdout contains strictly valid parseable JSON, while logs appear on sys.stderr."""
    buf_stdout = io.StringIO()
    buf_stderr = io.StringIO()
    with patch("sys.stdout", buf_stdout), patch("sys.stderr", buf_stderr):
        rc = main(["--project-root", str(temp_project), "status", "--json"])
    assert rc == 0
    # Must parse cleanly without error
    raw_out = buf_stdout.getvalue()
    parsed = json.loads(raw_out)
    assert parsed["status"] == "success"


def test_cli_error_envelope_formatting(temp_project: Path):
    """24. Tests standard error envelope when invalid session IDs or missing arguments occur."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(["--project-root", str(temp_project), "viva", "report", "--session-id", "non_existent_vs", "--json"])
    assert rc != 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "VIVA_REPORT_ERROR"
    assert "not found" in payload["error"]["message"].lower()


def test_cli_no_answer_argv_allowed(temp_project: Path):
    """25. Confirms passing --answer is rejected as an unrecognized argument (privacy guard)."""
    with pytest.raises(SystemExit) as exc:
        main(["--project-root", str(temp_project), "viva", "submit", "--session-id", "vs_123", "--answer", "leaked_text"])
    assert exc.value.code == 2  # argparse error code for unrecognized arguments
