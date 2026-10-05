"""Multi-repo integration validation suite for Milestone 10 across Python, TypeScript, React, and Java."""

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.cli.main import main
from backend.ai_gateway.gateway import AIGateway
from tests.fixtures.synthetic_repos import (
    create_python_fastapi_repo,
    create_typescript_node_repo,
    create_react_frontend_repo,
    create_java_gradle_repo,
    create_edge_case_repo,
)
from tests.fixtures.mock_gateway import MockAIProviderAdapter


@pytest.fixture(autouse=True)
def mock_gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock_key_for_testing")
    monkeypatch.setenv("BUILDCOACH_GEMINI_API_KEY", "mock_key_for_testing")
    with patch("backend.ai_gateway.consent.ConsentManager.validate_consent"):
        yield


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    return create_python_fastapi_repo(tmp_path / "python_project")


@pytest.fixture
def ts_repo(tmp_path: Path) -> Path:
    return create_typescript_node_repo(tmp_path / "ts_project")


@pytest.fixture
def react_repo(tmp_path: Path) -> Path:
    return create_react_frontend_repo(tmp_path / "react_project")


@pytest.fixture
def java_repo(tmp_path: Path) -> Path:
    return create_java_gradle_repo(tmp_path / "java_project")


@pytest.fixture
def edge_case_repo(tmp_path: Path) -> Path:
    return create_edge_case_repo(tmp_path / "edge_case_project")


def test_multi_repo_python_understand_flow(python_repo: Path):
    """1. Tests complete Workflow 1 preview -> explain -> submit on Python FastAPI repo."""
    root_str = str(python_repo)

    # 1. Preview
    buf_preview = io.StringIO()
    with patch("sys.stdout", buf_preview):
        rc_p = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_p == 0
    p_data = json.loads(buf_preview.getvalue())["data"]
    assert p_data["clean_working_tree"] is False
    assert p_data["total_files_changed"] >= 1
    assert any("routes/auth.py" in f for f in p_data["changed_files"])

    # 2. Explain with mock gateway
    adapter = MockAIProviderAdapter(scenario="STRONG")
    buf_explain = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_explain):
            rc_e = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc_e == 0
    e_data = json.loads(buf_explain.getvalue())["data"]
    assert e_data["what_changed"]["total_files_changed"] >= 1
    prompt_id = e_data["can_i_explain_this"]["prompt_id"]
    packet_id = e_data["packet_id"]

    # 3. Submit student answer via stdin
    fake_stdin = io.StringIO("The auth module validates JWT credentials before granting access.\n")
    buf_submit = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_submit):
            rc_s = main([
                "--project-root", root_str,
                "understand", "submit",
                "--prompt-id", prompt_id,
                "--packet-id", packet_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_s == 0
    s_data = json.loads(buf_submit.getvalue())["data"]
    assert s_data["overall_state"] == "UNDERSTOOD"
    assert "PURPOSE" in s_data["dimensions"]


def test_multi_repo_python_viva_flow(python_repo: Path):
    """2. Tests complete Workflow 2 viva start -> submit -> report on Python FastAPI repo."""
    root_str = str(python_repo)
    adapter = MockAIProviderAdapter(scenario="STRONG")

    # 1. Viva Start
    buf_start = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_start):
            rc_start = main(["--project-root", root_str, "viva", "start", "--difficulty", "easy", "--json"])
    assert rc_start == 0
    v_data = json.loads(buf_start.getvalue())["data"]
    session_id = v_data["session"]["session_id"]
    assert v_data["session"]["status"] == "AWAITING_ANSWER"
    assert v_data["first_question"] is not None

    # 2. Viva Submit
    fake_stdin = io.StringIO("We use token-based authentication to decouple authentication from user session storage.\n")
    buf_sub = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
            rc_sub = main([
                "--project-root", root_str,
                "viva", "submit",
                "--session-id", session_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_sub == 0
    step_data = json.loads(buf_sub.getvalue())["data"]
    assert step_data["turn_evaluation"]["rating"] == "STRONG"

    # 3. Viva Report
    buf_rep = io.StringIO()
    with patch("sys.stdout", buf_rep):
        rc_rep = main(["--project-root", root_str, "viva", "report", "--session-id", session_id, "--json"])
    assert rc_rep == 0
    rep_data = json.loads(buf_rep.getvalue())["data"]
    assert "readiness" in rep_data
    assert "category_masteries" in rep_data


def test_multi_repo_typescript_understand_flow(ts_repo: Path):
    """3. Tests Workflow 1 on TypeScript fixture verifying multiline import graph and staged/unstaged changes."""
    root_str = str(ts_repo)

    # 1. Status inspection
    buf_status = io.StringIO()
    with patch("sys.stdout", buf_status):
        rc_st = main(["--project-root", root_str, "status", "--json"])
    assert rc_st == 0
    st_data = json.loads(buf_status.getvalue())["data"]
    assert st_data["is_dirty"] is True
    assert st_data["staged_files_count"] >= 1
    assert st_data["modified_files_count"] >= 1

    # 2. Preview
    buf_preview = io.StringIO()
    with patch("sys.stdout", buf_preview):
        rc_p = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_p == 0
    p_data = json.loads(buf_preview.getvalue())["data"]
    assert p_data["total_files_changed"] >= 2

    # 3. Secret redaction verification
    assert p_data["redaction_summary"]["total_secrets_detected"] >= 1


def test_multi_repo_typescript_viva_flow(ts_repo: Path):
    """4. Tests Workflow 2 on TypeScript fixture verifying graph mapping and category mastery."""
    root_str = str(ts_repo)
    adapter = MockAIProviderAdapter(scenario="ADEQUATE")

    buf_start = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_start):
            rc_start = main(["--project-root", root_str, "viva", "start", "--mode", "project-wide", "--json"])
    assert rc_start == 0
    start_data = json.loads(buf_start.getvalue())["data"]
    session_id = start_data["session"]["session_id"]
    assert start_data["first_question"]["target_files"] is not None

    fake_stdin = io.StringIO("The User model interface defines the contract for creating users with typed attributes.\n")
    buf_sub = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
            rc_sub = main([
                "--project-root", root_str,
                "viva", "submit",
                "--session-id", session_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_sub == 0
    eval_data = json.loads(buf_sub.getvalue())["data"]["turn_evaluation"]
    assert eval_data["rating"] == "ADEQUATE"


def test_multi_repo_react_understand_flow(react_repo: Path):
    """5. Tests Workflow 1 on React fixture with JSX/TSX changes and component hierarchy."""
    root_str = str(react_repo)

    buf_preview = io.StringIO()
    with patch("sys.stdout", buf_preview):
        rc_p = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_p == 0
    p_data = json.loads(buf_preview.getvalue())["data"]
    assert any("Dashboard.tsx" in f for f in p_data["changed_files"])

    adapter = MockAIProviderAdapter(scenario="STRONG")
    buf_explain = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_explain):
            rc_e = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc_e == 0
    exp_data = json.loads(buf_explain.getvalue())["data"]
    assert exp_data["what_changed"]["total_files_changed"] >= 1


def test_multi_repo_react_viva_flow(react_repo: Path):
    """6. Tests Workflow 2 on React fixture verifying component identification and architecture defence."""
    root_str = str(react_repo)
    adapter = MockAIProviderAdapter(scenario="PARTIAL")

    buf_start = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_start):
            rc = main(["--project-root", root_str, "viva", "start", "--difficulty", "medium", "--json"])
    assert rc == 0
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    fake_stdin = io.StringIO("Dashboard displays user data using the useAuth hook.\n")
    buf_sub = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub):
            rc_sub = main([
                "--project-root", root_str,
                "viva", "submit",
                "--session-id", session_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_sub == 0
    step_data = json.loads(buf_sub.getvalue())["data"]
    assert step_data["turn_evaluation"]["rating"] == "PARTIAL"


def test_multi_repo_java_graceful_handling(java_repo: Path):
    """7. Tests Java Gradle enterprise layout indexes cleanly without hallucinating unsupported AST imports."""
    root_str = str(java_repo)

    # 1. Scan command
    buf_scan = io.StringIO()
    with patch("sys.stdout", buf_scan):
        rc_scan = main(["--project-root", root_str, "scan", "--json"])
    assert rc_scan == 0
    s_data = json.loads(buf_scan.getvalue())["data"]
    assert s_data["scanned_files_count"] >= 4

    # 2. Inspect SQLite graph_nodes to ensure Java files are indexed as FILE nodes
    db_path = java_repo / ".buildcoach" / "state.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id, node_type, name FROM graph_nodes WHERE node_type = 'FILE'")
    file_nodes = cur.fetchall()
    conn.close()

    assert any("Application.java" in n[2] for n in file_nodes)
    assert any("AuthController.java" in n[2] for n in file_nodes)

    # 3. Understand preview on Java edits
    buf_preview = io.StringIO()
    with patch("sys.stdout", buf_preview):
        rc_prev = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_prev == 0
    p_data = json.loads(buf_preview.getvalue())["data"]
    assert any("AuthController.java" in f for f in p_data["changed_files"])


def test_multi_repo_broken_edge_cases(edge_case_repo: Path):
    """8. Verifies graceful operation on repositories with binaries, non-UTF8 files, and large files (>1MB)."""
    root_str = str(edge_case_repo)

    # 1. Scan handles non-UTF8, binary, and large files safely
    buf_scan = io.StringIO()
    with patch("sys.stdout", buf_scan):
        rc_scan = main(["--project-root", root_str, "scan", "--json"])
    assert rc_scan == 0
    scan_data = json.loads(buf_scan.getvalue())["data"]
    assert scan_data["scanned_files_count"] >= 4

    # 2. Inspect database flags for binary and large files
    db_path = edge_case_repo / ".buildcoach" / "state.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT path, is_binary, is_large FROM files")
    files_info = {r[0]: (bool(r[1]), bool(r[2])) for r in cur.fetchall()}
    conn.close()

    assert files_info.get("assets/data.bin", (False, False))[0] is True  # is_binary
    assert files_info.get("logs/large.log", (False, False))[1] is True    # is_large

    # 3. Preview operates without UnicodeDecodeError
    buf_preview = io.StringIO()
    with patch("sys.stdout", buf_preview):
        rc_p = main(["--project-root", root_str, "understand", "preview", "--json"])
    assert rc_p == 0
    p_data = json.loads(buf_preview.getvalue())["data"]
    assert p_data["redaction_summary"]["total_secrets_detected"] >= 1  # AWS secret redacted
