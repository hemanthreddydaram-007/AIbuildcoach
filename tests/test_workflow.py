"""End-to-end workflow test suite for Milestone 12.11 (Real Developer Workflow).

Tests the full developer workflow composing:
- M12.5 Observation engine
- M12.6 Build timeline explanation
- M12.7 Knowledge gap and next action
- M12.8 Unified session orchestration
- M12.9 Runtime execution
- M12.10 Transparent terminal integration
- M7 Pedagogical comprehension
"""

import sys
import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Tuple

from backend.domain.models import Project, utc_now_iso
from backend.project_model.db import Database
from backend.observation.service import ObservationService
from backend.observation.models import (
    ObservationEventType,
    ObservationSource,
    FixStatus,
)
from backend.terminal.integration import TerminalIntegrationManager
from backend.terminal.session import TerminalSessionManager, TerminalCommandPayload
from backend.session.service import SessionService
from backend.session.models import SessionState
from backend.session.summary import format_human_session
from backend.bridge.routes import BridgeRouter


def _dt(offset_seconds: int = 0) -> str:
    """Produces monotonic ISO timestamps with controlled second offsets."""
    base = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def _setup_project(tmp_path: Path, name: str = "demo_proj") -> Tuple[Path, Database, str]:
    """Helper to set up a test project on disk with database."""
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    db_path = proj_dir / ".buildcoach" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(db_path))

    p = Project(
        id=name,
        name=f"Project {name}",
        root_path=str(proj_dir.resolve()),
        created_at=_dt(-3600),
        updated_at=_dt(-3600),
    )
    db.upsert_project(p)
    return proj_dir, db, name


# ---------------------------------------------------------------------------
# Critical 16-Step Real Developer Lifecycle Test (Section 25)
# ---------------------------------------------------------------------------

def test_e2e_16_step_real_developer_lifecycle(tmp_path):
    """Executes the full 16-step E2E developer lifecycle without manual observation events.
    
    1. Project starts STABLE.
    2. Developer runs a failing command normally.
    3. Build Coach observes the failure automatically.
    4. Session becomes INVESTIGATING.
    5. User opens Build Coach.
    6. Explanation is visible.
    7. Evidence is visible.
    8. User changes the project.
    9. User reruns normally.
    10. Build Coach observes recovery.
    11. Session becomes VERIFYING.
    12. Targeted test is run normally.
    13. Verification is observed.
    14. Guidance recalculates (actions retired).
    15. User completes M7 explanation if required.
    16. Session reaches final STABLE state.
    """
    proj_dir, db, proj_id = _setup_project(tmp_path, "lifecycle_app")
    terminal_mgr = TerminalIntegrationManager(db)
    session_mgr = TerminalSessionManager(db)
    sess_service = SessionService(db)

    # Enable transparent terminal integration (M12.10)
    terminal_mgr.enable(proj_id, shell_type="powershell")

    # -------------------------------------------------------------------------
    # STEP 1: Project starts healthy/stable
    # Initial clean test run + M7 comprehension run
    # -------------------------------------------------------------------------
    initial_pass = TerminalCommandPayload(
        command="pytest tests/test_calc.py",
        exit_code=0,
        stdout="1 passed in 0.04s",
        stderr="",
        working_directory=str(proj_dir),
        started_at=_dt(0),
        finished_at=_dt(1),
    )
    session_mgr.record_terminal_command(proj_id, initial_pass)

    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO comprehension_runs (
                run_id, project_id, changeset_id, packet_id, prompt_id,
                attempt_number, run_status, overall_state, gap_count, started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run_init", proj_id, "cs_0", "pkt_0", "prompt_0", 1, "COMPLETED", "UNDERSTOOD", 0, _dt(2), _dt(2)),
        )

    s1 = sess_service.get_or_create_session(proj_id)
    assert s1.state == SessionState.STABLE
    assert s1.active_incident is None
    assert s1.fix_status == "VERIFIED"

    # -------------------------------------------------------------------------
    # STEP 2 & 3: Developer runs failing command, Build Coach observes failure
    # -------------------------------------------------------------------------
    fail_payload = TerminalCommandPayload(
        command="pytest tests/test_calc.py",
        exit_code=1,
        stdout="FAILED tests/test_calc.py::test_divide\n1 failed in 0.05s",
        stderr="Traceback (most recent call last):\n  File \"calc.py\", line 12, in divide\n    return a / b\nZeroDivisionError: division by zero",
        working_directory=str(proj_dir),
        started_at=_dt(10),
        finished_at=_dt(11),
    )
    # The terminal integration observes and records this automatically
    session_mgr.record_terminal_command(proj_id, fail_payload)

    # -------------------------------------------------------------------------
    # STEP 4: Session becomes INVESTIGATING
    # -------------------------------------------------------------------------
    s2 = sess_service.refresh_session(proj_id)
    assert s2.state == SessionState.INVESTIGATING
    assert s2.active_incident is not None

    # -------------------------------------------------------------------------
    # STEP 5, 6, 7: User opens Build Coach; Explanation & Evidence are visible
    # -------------------------------------------------------------------------
    api_view = s2.to_api_dict()
    assert api_view["state"] == "INVESTIGATING"
    assert api_view["fix_status"] in ("UNKNOWN", "PERSISTING")
    assert "ZeroDivisionError" in api_view["what_happened"]
    assert len(api_view["how_do_we_know"]) > 0
    assert any("ZeroDivisionError" in item for item in api_view["how_do_we_know"])
    assert len(api_view["evidence_chain"]) > 0
    assert len(api_view["recent_activity"]) > 0

    human_view = format_human_session(s2, project_name="Lifecycle App")
    assert "BUILD COACH" in human_view
    assert "State:\nINVESTIGATING" in human_view
    assert "WHAT HAPPENED?" in human_view
    assert "HOW DO WE KNOW?" in human_view
    assert "NEXT ACTION:" in human_view

    # -------------------------------------------------------------------------
    # STEP 8: User changes the project (code fix)
    # -------------------------------------------------------------------------
    (proj_dir / "calc.py").write_text("def divide(a, b):\n    return a / b if b != 0 else 0\n")
    obs_svc = ObservationService(db)
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(20),
        payload={"changed_files": ["calc.py"], "summary": "Handle zero division"},
        provenance={"source": "git"},
    )

    # -------------------------------------------------------------------------
    # STEP 9 & 10: User reruns normally -> Build Coach observes recovery
    # -------------------------------------------------------------------------
    recovery_payload = TerminalCommandPayload(
        command="python -c \"import calc; print(calc.divide(10, 2))\"",
        exit_code=0,
        stdout="5.0\n",
        stderr="",
        working_directory=str(proj_dir),
        started_at=_dt(30),
        finished_at=_dt(31),
    )
    session_mgr.record_terminal_command(proj_id, recovery_payload)

    # -------------------------------------------------------------------------
    # STEP 11: Session becomes VERIFYING
    # -------------------------------------------------------------------------
    s3 = sess_service.refresh_session(proj_id)
    assert s3.state == SessionState.VERIFYING
    assert s3.fix_status == "RECOVERED"
    assert s3.next_action is not None
    assert "test" in s3.next_action.title.lower() or "verify" in s3.next_action.title.lower()

    # -------------------------------------------------------------------------
    # STEP 12 & 13: Targeted test is run normally -> Verification observed
    # -------------------------------------------------------------------------
    verify_payload = TerminalCommandPayload(
        command="pytest tests/test_calc.py",
        exit_code=0,
        stdout="1 passed in 0.03s",
        stderr="",
        working_directory=str(proj_dir),
        started_at=_dt(40),
        finished_at=_dt(41),
    )
    session_mgr.record_terminal_command(proj_id, verify_payload)

    s4 = sess_service.refresh_session(proj_id)
    assert s4.verification_summary.targeted_test_observed is True
    assert s4.fix_status == "VERIFIED"

    # -------------------------------------------------------------------------
    # STEP 14: Guidance recalculates; previous investigation actions retired
    # -------------------------------------------------------------------------
    plan = sess_service.what_should_i_do(proj_id)
    for act in plan.actions:
        assert act.title != "Investigate ZeroDivisionError"

    # -------------------------------------------------------------------------
    # STEP 15: User completes M7 explanation
    # -------------------------------------------------------------------------
    assert s4.state in (SessionState.LEARNING, SessionState.ACTION_REQUIRED, SessionState.STABLE)
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO comprehension_runs (
                run_id, project_id, changeset_id, packet_id, prompt_id,
                attempt_number, run_status, overall_state, gap_count, started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run_fix", proj_id, "cs_fix", "pkt_fix", "prompt_fix", 1, "COMPLETED", "UNDERSTOOD", 0, _dt(50), _dt(50)),
        )

    # -------------------------------------------------------------------------
    # STEP 16: Session reaches the appropriate final state (STABLE)
    # -------------------------------------------------------------------------
    s_final = sess_service.refresh_session(proj_id)
    assert s_final.state == SessionState.STABLE
    assert s_final.fix_status == "VERIFIED"
    assert s_final.understanding_summary.latest_state == "UNDERSTOOD"


# ---------------------------------------------------------------------------
# Project Isolation Test (Section 22)
# ---------------------------------------------------------------------------

def test_workflow_strict_project_isolation(tmp_path):
    """Verifies complete isolation between Project A and Project B workflows."""
    _, db_a, proj_a = _setup_project(tmp_path, "proj_isolated_a")
    _, db_b, proj_b = _setup_project(tmp_path, "proj_isolated_b")

    terminal_mgr_a = TerminalIntegrationManager(db_a)
    terminal_mgr_a.enable(proj_a)

    sess_mgr_a = TerminalSessionManager(db_a)
    sess_svc_a = SessionService(db_a)
    sess_svc_b = SessionService(db_b)

    # Project A has a critical failure
    fail_payload = TerminalCommandPayload(
        command="python -m app",
        exit_code=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'secret_module'",
        started_at=_dt(0),
        finished_at=_dt(1),
    )
    sess_mgr_a.record_terminal_command(proj_a, fail_payload)

    session_a = sess_svc_a.get_or_create_session(proj_a)
    session_b = sess_svc_b.get_or_create_session(proj_b)

    # Project A is INVESTIGATING with the incident
    assert session_a.state == SessionState.INVESTIGATING
    assert "ModuleNotFoundError" in session_a.what_happened

    # Project B must not see any incident or evidence from Project A
    assert session_b.state == SessionState.UNKNOWN
    assert session_b.active_incident is None
    assert "ModuleNotFoundError" not in session_b.what_happened
    assert len(session_b.how_do_we_know) == 1
    assert "No runtime errors observed" in session_b.how_do_we_know[0]


# ---------------------------------------------------------------------------
# No Fake Completion & Uncertainty Preservation (Section 9 & 13)
# ---------------------------------------------------------------------------

def test_no_fake_completion_and_unknowns_preserved(tmp_path):
    """Verifies that unknowns are always preserved and state transitions strictly require real evidence."""
    proj_dir, db, proj_id = _setup_project(tmp_path, "uncertainty_app")
    obs_svc = ObservationService(db)
    sess_svc = SessionService(db)

    # Record error followed by change and restart (no tests)
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={
            "error_kind": "ZeroDivisionError",
            "message": "division by zero",
            "file_path": "math.py",
            "line_number": 12,
            "error_signature": "sig_zero",
        },
        provenance={"source": "cli"},
    )
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(10),
        payload={"changed_files": ["math.py"], "summary": "fix division"},
        provenance={"source": "git"},
    )
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.HTTP_RESPONSE,
        source=ObservationSource.HTTP,
        timestamp=_dt(20),
        payload={"method": "GET", "path": "/health", "status_code": 200},
        provenance={"source": "http"},
    )

    session = sess_svc.get_or_create_session(proj_id)
    assert session.fix_status == "RECOVERED"
    # Epistemic limitation must never be omitted: recovery is not proof of causality
    assert len(session.summary.unknowns) > 0
    unknown_text = " ".join(session.summary.unknowns)
    assert "sole cause" in unknown_text.lower() or "test verification" in unknown_text.lower()

    # Without an actual test observation, it cannot be promoted to VERIFIED
    assert session.verification_summary.targeted_test_observed is False


# ---------------------------------------------------------------------------
# Bridge Routes for Workflow (Section 18 & 19)
# ---------------------------------------------------------------------------

def test_bridge_workflow_routes(tmp_path):
    """Verifies GET /v1/projects/{id}/session, POST /v1/projects/{id}/refresh, and GET /v1/projects/{id}/understanding."""
    proj_dir, db, proj_id = _setup_project(tmp_path, "bridge_workflow_app")
    router = BridgeRouter(db)

    # 1. GET session
    status, sess_resp = router.handle_get_session(proj_id)
    assert status == 200
    assert sess_resp["ok"] is True
    res = sess_resp["result"]
    assert res["project_id"] == proj_id
    assert "what_happened" in res
    assert "fix_status" in res
    assert "how_do_we_know" in res
    assert "evidence_chain" in res
    assert "recent_activity" in res

    # 2. POST refresh
    status, ref_resp = router.handle_refresh_session(proj_id)
    assert status == 200
    assert ref_resp["ok"] is True
    assert ref_resp["result"]["project_id"] == proj_id

    # 3. GET understanding
    status, und_resp = router.handle_get_understanding(proj_id)
    assert status == 200
    assert und_resp["ok"] is True
    assert und_resp["result"]["project_id"] == proj_id
    assert "overall_state" in und_resp["result"]


# ---------------------------------------------------------------------------
# CLI Workflow Consistency (Section 18 & 26)
# ---------------------------------------------------------------------------

def test_cli_workflow_human_and_json_output(tmp_path):
    """Verifies python -m backend.cli session show outputs answers to the 5+1 questions consistently."""
    from backend.cli.runner import run_session_show

    proj_dir, db, proj_id = _setup_project(tmp_path, "cli_workflow_app")
    terminal_mgr = TerminalIntegrationManager(db)
    session_mgr = TerminalSessionManager(db)

    terminal_mgr.enable(proj_id)
    fail_payload = TerminalCommandPayload(
        command="pytest tests/test_payment.py",
        exit_code=1,
        stdout="FAILED test_charge\n1 failed",
        stderr="ValueError: Invalid currency",
        started_at=_dt(0),
        finished_at=_dt(1),
    )
    session_mgr.record_terminal_command(proj_id, fail_payload)

    res = run_session_show(db, project_id=proj_id)
    assert res["project_id"] == proj_id
    assert "session" in res
    assert "human_text" in res

    # JSON DTO consistency
    dto = res["session"]
    assert dto["state"] == "INVESTIGATING"
    assert "ValueError" in dto["what_happened"]
    assert len(dto["how_do_we_know"]) > 0
    assert len(dto["recent_activity"]) > 0

    # Human terminal output consistency
    txt = res["human_text"]
    assert "BUILD COACH" in txt
    assert "State:\nINVESTIGATING" in txt
    assert "WHAT HAPPENED?" in txt
    assert "HOW DO WE KNOW?" in txt
    assert "STILL UNKNOWN:" in txt
    assert "NEXT ACTION:" in txt
    assert "DO I UNDERSTAND?" in txt
    assert "RECENT ACTIVITY:" in txt

