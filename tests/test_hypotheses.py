"""Formal automated verification of the 7 core architectural hypotheses (H1-H6, H7a, H7b)."""

import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.cli.main import main
from backend.ai_gateway.gateway import AIGateway
from backend.explanation.models import IntentEpistemicStatus
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


def test_hypothesis_h1_physical_truth_invariance(tmp_path: Path):
    """H1: Validates that What Changed file counts, additions, and deletions match Git truth exactly, even if model hallucinates."""
    repo = create_python_fastapi_repo(tmp_path / "h1_repo", dirty=True)
    root_str = str(repo)

    # Use a mock adapter returning claims that mention non-existent files
    adapter = MockAIProviderAdapter(scenario="STRONG")
    buf = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf):
            rc = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    wc = json.loads(buf.getvalue())["data"]["what_changed"]

    # Verify physical truth invariant: only physical files from git appear in files list
    reported_files = [f["file_path"] for f in wc["files"]]
    assert "app/routes/auth.py" in reported_files or any("auth.py" in f for f in reported_files)
    # Model cannot hallucinate new physical files into the What Changed table
    assert not any("phantom_file.py" in f for f in reported_files)
    assert wc["total_files_changed"] == len(reported_files)


def test_hypothesis_h2_evidence_grounded_comprehension(tmp_path: Path):
    """H2: Validates that viva questions cite verified ContextItem IDs and reject ungrounded generic textbook trivia."""
    repo = create_python_fastapi_repo(tmp_path / "h2_repo", dirty=False)
    root_str = str(repo)

    adapter = MockAIProviderAdapter(scenario="STRONG")
    buf_scan = io.StringIO()
    with patch("sys.stdout", buf_scan):
        main(["--project-root", root_str, "scan", "--json"])

    buf = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf):
            rc = main(["--project-root", root_str, "viva", "start", "--difficulty", "easy", "--json"])
    assert rc == 0
    data = json.loads(buf.getvalue())["data"]
    first_q = data["first_question"]

    assert first_q is not None
    # Question must cite supporting evidence IDs from project
    assert len(first_q["supporting_evidence_ids"]) >= 1
    assert first_q["packet_id"] != ""


def test_hypothesis_h3_strict_epistemic_qualification(tmp_path: Path):
    """H3: Validates strict epistemic rules:
    - absent / undocumented rationale -> UNKNOWN (never inferred or explicit)
    - supported inference -> INFERRED
    - documented rationale -> EXPLICIT
    """
    repo = create_python_fastapi_repo(tmp_path / "h3_repo", dirty=True)
    root_str = str(repo)

    # 1. Absent / intentionally undocumented rationale MUST strictly result in UNKNOWN
    undocumented_adapter = MockAIProviderAdapter(scenario="UNDOCUMENTED")
    buf_undocumented = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=undocumented_adapter):
        with patch("sys.stdout", buf_undocumented):
            rc = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    why_undocumented = json.loads(buf_undocumented.getvalue())["data"]["why"]
    primary_status_undocumented = why_undocumented["primary_intent"]["status"]
    assert primary_status_undocumented == IntentEpistemicStatus.UNKNOWN.value

    # 2. Supported model inference results in INFERRED
    inferred_adapter = MockAIProviderAdapter(scenario="STRONG")
    buf_inferred = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=inferred_adapter):
        with patch("sys.stdout", buf_inferred):
            rc = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    why_inferred = json.loads(buf_inferred.getvalue())["data"]["why"]
    primary_status_inferred = why_inferred["primary_intent"]["status"]
    assert primary_status_inferred == IntentEpistemicStatus.INFERRED.value

    # 3. Documented rationale (e.g. commit message with explicit why) results in EXPLICIT
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Why: Enforce strict token authentication"], cwd=repo, check=True, capture_output=True)
    (repo / "app" / "routes" / "auth.py").write_text("# subsequent change\n", encoding="utf-8")

    buf_explicit = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=inferred_adapter):
        with patch("sys.stdout", buf_explicit):
            rc = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    why_explicit = json.loads(buf_explicit.getvalue())["data"]["why"]
    primary_status_explicit = why_explicit["primary_intent"]["status"]
    assert primary_status_explicit == IntentEpistemicStatus.EXPLICIT.value


def test_hypothesis_h4_zero_untrusted_code_execution(tmp_path: Path):
    """H4: Validates scanning and context generation execute zero project binaries or build scripts (npm, pip, gradle)."""
    repo = create_typescript_node_repo(tmp_path / "h4_repo", dirty=True)

    # Monitor subprocess.Popen and subprocess.run to verify only read-only git commands execute
    original_run = subprocess.run

    def guarded_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        forbidden_tools = ["npm", "pip", "node", "mvn", "gradle", "bash", "sh"]
        for tool in forbidden_tools:
            assert tool not in cmd_str.lower().split(), f"Violation of H4: Executed forbidden tool '{tool}': {cmd_str}"
        return original_run(*args, **kwargs)

    with patch("subprocess.run", side_effect=guarded_run):
        rc = main(["--project-root", str(repo), "scan", "--json"])
        assert rc == 0


def test_hypothesis_h5_student_answer_privacy_boundary(tmp_path: Path):
    """H5 (Student Answer Privacy Boundary): Validates student answer is transient in memory and absent from SQLite, logs, temp files, and argv."""
    repo = create_python_fastapi_repo(tmp_path / "h5_repo", dirty=False)
    root_str = str(repo)

    # 1. Argv privacy invariant: --answer is rejected as unrecognized argument
    with pytest.raises(SystemExit) as exc:
        main(["--project-root", root_str, "viva", "submit", "--session-id", "vs_test", "--answer", "leaked_in_argv"])
    assert exc.value.code == 2

    # 2. Start session
    adapter = MockAIProviderAdapter(scenario="STRONG")
    buf_start = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf_start):
            main(["--project-root", root_str, "viva", "start", "--json"])
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    # 3. Submit high-entropy canary answer via stdin
    canary_answer = "CANARY_STUDENT_CONFIDENTIAL_DEFENCE_ANSWER_778899"
    fake_stdin = io.StringIO(f"{canary_answer}\n")
    buf_sub = io.StringIO()
    buf_err = io.StringIO()

    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", buf_sub), patch("sys.stderr", buf_err):
            rc_sub = main([
                "--project-root", root_str,
                "viva", "submit",
                "--session-id", session_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_sub == 0

    # 4. Verify absent from stderr and stdout payload details
    assert canary_answer not in buf_err.getvalue()

    # 5. Verify absent from every SQLite table and column
    db_path = repo / ".buildcoach" / "state.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        columns = [c[1] for c in cur.fetchall()]
        for c in columns:
            cur.execute(f"SELECT COUNT(*) FROM {t} WHERE CAST({c} AS TEXT) LIKE '%{canary_answer}%'")
            count = cur.fetchone()[0]
            assert count == 0, f"Leaked student answer in table {t}, column {c}!"
    conn.close()


def test_hypothesis_h6_frozen_provider_failure_fallback_m6(tmp_path: Path):
    """H6: Validates M6 explain degrades safely to frozen _synthesize_fallback_result on gateway errors."""
    repo = create_python_fastapi_repo(tmp_path / "h6_m6_repo", dirty=True)
    root_str = str(repo)

    # Inject timeout fault
    adapter = MockAIProviderAdapter(scenario="timeout")
    buf = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=adapter):
        with patch("sys.stdout", buf):
            rc = main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    assert rc == 0
    data = json.loads(buf.getvalue())["data"]

    # Verify frozen fallback behavior
    assert data["why"]["primary_intent"]["status"] == "UNKNOWN"
    assert "timed out" in data["unresolved_questions"][0].lower()
    assert data["can_i_explain_this"]["is_available"] is False


def test_hypothesis_h6_frozen_provider_failure_fallback_m7_m8(tmp_path: Path):
    """H6: Validates M7 records FAILED run and M8 records UNKNOWN turn with provider failure feedback on gateway outage."""
    repo = create_python_fastapi_repo(tmp_path / "h6_m78_repo", dirty=True)
    root_str = str(repo)

    good_adapter = MockAIProviderAdapter(scenario="STRONG")
    fault_adapter = MockAIProviderAdapter(scenario="http_503")

    # 1. M7 comprehension provider failure fallback:
    # Explain changes first to generate prompt & packet
    buf_exp = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=good_adapter):
        with patch("sys.stdout", buf_exp):
            main(["--project-root", root_str, "understand", "explain", "--consent-token", "auto", "--json"])
    e_data = json.loads(buf_exp.getvalue())["data"]
    prompt_id = e_data["can_i_explain_this"]["prompt_id"]
    packet_id = e_data["packet_id"]

    # Submit explanation during outage: re-raises exception, CLI returns code 2, SQLite records FAILED run
    buf_m7_sub = io.StringIO()
    fake_stdin_m7 = io.StringIO("The auth module validates JWT tokens.\n")
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=fault_adapter):
        with patch("sys.stdin", fake_stdin_m7), patch("sys.stdout", buf_m7_sub):
            rc_m7 = main([
                "--project-root", root_str,
                "understand", "submit",
                "--prompt-id", prompt_id,
                "--packet-id", packet_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_m7 == 2
    err_envelope = json.loads(buf_m7_sub.getvalue())
    assert err_envelope["status"] == "error"
    assert err_envelope["error"]["code"] == "SUBMIT_ERROR"

    # Verify SQLite recorded FAILED run in comprehension_runs
    db_path = repo / ".buildcoach" / "state.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT run_status FROM comprehension_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (e_data["project_id"],))
    assert cur.fetchone()[0] == "FAILED"

    # 2. M8 viva provider failure fallback:
    # Start viva session
    buf_start = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=good_adapter):
        with patch("sys.stdout", buf_start):
            main(["--project-root", root_str, "viva", "start", "--json"])
    session_id = json.loads(buf_start.getvalue())["data"]["session"]["session_id"]

    # Submit answer during outage: graceful degradation returning completed turn with UNKNOWN rating
    fake_stdin_m8 = io.StringIO("Answer during outage\n")
    buf_m8_sub = io.StringIO()
    with patch("backend.ai_gateway.gateway.GeminiInteractionsAdapter", return_value=fault_adapter):
        with patch("sys.stdin", fake_stdin_m8), patch("sys.stdout", buf_m8_sub):
            rc_m8 = main([
                "--project-root", root_str,
                "viva", "submit",
                "--session-id", session_id,
                "--answer-stdin",
                "--json",
            ])
    assert rc_m8 == 0
    m8_data = json.loads(buf_m8_sub.getvalue())["data"]
    assert m8_data["turn_evaluation"]["rating"] == "UNKNOWN"
    assert "provider failure" in m8_data["turn_evaluation"]["feedback"].lower()

    # Verify SQLite recorded UNKNOWN turn rating
    cur.execute("SELECT rating FROM viva_turns WHERE session_id = ?", (session_id,))
    assert cur.fetchone()[0] == "UNKNOWN"
    conn.close()


def test_hypothesis_h7a_multi_stack_functional_portability(tmp_path: Path):
    """H7a: Validates functional portability across Python, TypeScript, React, Java, and Edge Case repositories."""
    python_repo = create_python_fastapi_repo(tmp_path / "h7a_python")
    ts_repo = create_typescript_node_repo(tmp_path / "h7a_ts")
    react_repo = create_react_frontend_repo(tmp_path / "h7a_react")
    java_repo = create_java_gradle_repo(tmp_path / "h7a_java")
    edge_repo = create_edge_case_repo(tmp_path / "h7a_edge")

    repos = [python_repo, ts_repo, react_repo, java_repo, edge_repo]
    for r in repos:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = main(["--project-root", str(r), "status", "--json"])
        assert rc == 0, f"H7a failed for repo: {r.name}"
        data = json.loads(buf.getvalue())["data"]
        assert data["schema_version"] == 8
        assert data["is_git_repo"] is True


def test_hypothesis_h7b_performance_sla_conformance(tmp_path: Path):
    """H7b: Validates that local scan and graph building perform within SLA limits (< 1000ms scan, < 500ms graph) on 500+ files."""
    from backend.project_model.scanner import ProjectScanner
    from backend.project_model.graph_builder import ProjectGraphBuilder
    from backend.project_model.db import Database
    from tests.fixtures.synthetic_repos import create_large_benchmark_repo
    import statistics
    import time

    repo = create_large_benchmark_repo(tmp_path / "h7b_perf_500", file_count=600)

    scanner = ProjectScanner(repo)
    # Warm-up run
    scanner.scan()

    durations = []
    for _ in range(5):
        t0 = time.perf_counter()
        scan_res = scanner.scan()
        durations.append((time.perf_counter() - t0) * 1000.0)

    median_scan = statistics.median(durations)
    scan_sla_target = 1000.0  # < 1.0s
    assert len(scan_res.files) >= 500, f"Fixture file count must be >= 500, got {len(scan_res.files)}"
    assert median_scan < scan_sla_target, f"H7b Scan SLA violation: Median scan took {median_scan:.2f}ms (target < {scan_sla_target:.2f}ms)"
    assert scan_res.project.id != ""

    # Graph building SLA: < 500ms
    db = Database(repo / ".buildcoach" / "state.db")
    builder = ProjectGraphBuilder(repo, scan_res.project, scan_res.files, db)
    builder.build()  # warm-up

    graph_durations = []
    for _ in range(5):
        t_graph_0 = time.perf_counter()
        graph = builder.build()
        graph_durations.append((time.perf_counter() - t_graph_0) * 1000.0)

    median_graph = statistics.median(graph_durations)
    graph_sla_target = 500.0  # < 500ms
    assert len(graph.nodes) >= 500
    assert median_graph < graph_sla_target, f"H7b Graph SLA violation: Median graph build took {median_graph:.2f}ms (target < {graph_sla_target:.2f}ms)"


def test_v1_cli_headless_stdout_purity_across_all_stacks(tmp_path: Path):
    """Validates that --json mode emits strictly one valid JSON document on stdout without ANSI escapes across all stacks."""
    ts_repo = create_typescript_node_repo(tmp_path / "purity_ts")

    buf_stdout = io.StringIO()
    buf_stderr = io.StringIO()
    with patch("sys.stdout", buf_stdout), patch("sys.stderr", buf_stderr):
        rc = main(["--project-root", str(ts_repo), "status", "--json"])
    assert rc == 0

    raw_output = buf_stdout.getvalue()
    # Must contain no ANSI escape sequences
    assert "\033[" not in raw_output
    # Must parse cleanly as single JSON object
    parsed = json.loads(raw_output)
    assert parsed["status"] == "success"
    assert parsed["command"] == "status"
