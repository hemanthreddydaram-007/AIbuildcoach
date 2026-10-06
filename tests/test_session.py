"""Comprehensive test suite for Milestone 12.8 (Unified Build Coach Session)."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from backend.domain.models import Project
from backend.project_model.db import Database
from backend.observation.models import (
    ObservationEventType,
    ObservationSource,
)
from backend.observation.service import ObservationService
from backend.guidance.service import GuidanceService
from backend.guidance.models import ActionType
from backend.session.models import (
    BuildCoachSession,
    SessionState,
)
from backend.session.service import SessionService
from backend.session.summary import format_human_session
from backend.bridge.routes import BridgeRouter
from backend.cli.runner import run_session_show


def _dt(offset_seconds: int = 0) -> str:
    base = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


@pytest.fixture
def clean_db(tmp_path: Path):
    db_file = tmp_path / "test_session.db"
    return Database(str(db_file))


def _setup_project(db: Database, project_id: str = "proj_session_test") -> str:
    p = Project(
        id=project_id,
        name=f"Project {project_id}",
        root_path=f"/path/to/{project_id}",
        created_at=_dt(-3600),
        updated_at=_dt(-3600),
    )
    db.upsert_project(p)
    return project_id


# ---------------------------------------------------------------------------
# 1. Required State Tests (Section 27)
# ---------------------------------------------------------------------------

def test_state_1_active_runtime_error_investigating(clean_db):
    """Test 1: active runtime error -> Expected: INVESTIGATING."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # Record active runtime error without recovery
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
            "error_signature": "sig_err_zero",
        },
        provenance={"source": "cli"},
    )

    sess_svc = SessionService(clean_db, obs_svc)
    session = sess_svc.get_or_create_session(proj_id)

    assert session.state == SessionState.INVESTIGATING
    assert session.active_incident is not None
    assert session.next_action is not None
    assert session.next_action.action_type in (ActionType.INVESTIGATE_ERROR, ActionType.CHECK_RUNTIME)


def test_persistent_error_investigate_error_action(clean_db):
    """Verifies that persisting error after an attempted fix yields ActionType.INVESTIGATE_ERROR."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # 1. Error
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
            "error_signature": "sig_err_zero",
        },
        provenance={"source": "cli"},
    )
    # 2. Change
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(10),
        payload={"changed_files": ["math.py"], "summary": "attempted fix"},
        provenance={"source": "git"},
    )
    # 3. Same error recurring
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(20),
        payload={
            "error_kind": "ZeroDivisionError",
            "message": "division by zero",
            "file_path": "math.py",
            "line_number": 12,
            "error_signature": "sig_err_zero",
        },
        provenance={"source": "cli"},
    )

    sess_svc = SessionService(clean_db, obs_svc)
    session = sess_svc.get_or_create_session(proj_id)

    assert session.state == SessionState.INVESTIGATING
    assert session.verification_summary.status == "PERSISTING"
    assert session.next_action.action_type == ActionType.INVESTIGATE_ERROR


def test_state_2_error_recovered_test_missing_verifying(clean_db):
    """Test 2: error recovered + test missing -> Expected: VERIFYING."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # 1. Error
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
            "error_signature": "sig_err_zero",
        },
        provenance={"source": "cli"},
    )
    # 2. Change
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(10),
        payload={"changed_files": ["math.py"], "summary": "fix division"},
        provenance={"source": "git"},
    )
    # 3. Clean operational restart / HTTP 200 (error vanished operationally, but no tests)
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.HTTP_RESPONSE,
        source=ObservationSource.HTTP,
        timestamp=_dt(20),
        payload={"method": "GET", "path": "/health", "status_code": 200},
        provenance={"source": "http"},
    )

    sess_svc = SessionService(clean_db, obs_svc)
    session = sess_svc.get_or_create_session(proj_id)

    assert session.state == SessionState.VERIFYING
    assert session.verification_summary.status == "RECOVERED"
    assert session.verification_summary.targeted_test_observed is False
    assert session.next_action is not None
    assert session.next_action.action_type == ActionType.RUN_TEST


def test_state_3_verification_complete_understanding_gap_learning(clean_db):
    """Test 3: verification complete + understanding gap -> Expected: LEARNING."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # 1. Code change
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(0),
        payload={"changed_files": ["math.py"], "summary": "refactor"},
        provenance={"source": "git"},
    )
    # 2. Targeted test passed
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.TEST_FINISHED,
        source=ObservationSource.PYTEST,
        timestamp=_dt(10),
        payload={"status": "PASSED", "test_name": "tests/test_math.py"},
        provenance={"source": "pytest"},
    )

    # 3. Insert understanding run that is NOT UNDERSTOOD (e.g. MISUNDERSTOOD or PARTIALLY_UNDERSTOOD)
    with clean_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO comprehension_runs (
                run_id, project_id, changeset_id, packet_id, prompt_id,
                attempt_number, run_status, overall_state, gap_count, started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_1",
                proj_id,
                "cs_1",
                "pkt_1",
                "prmpt_1",
                1,
                "COMPLETED",
                "MISUNDERSTOOD",
                2,
                _dt(20),
                _dt(20),
            ),
        )

    sess_svc = SessionService(clean_db, obs_svc)
    session = sess_svc.get_or_create_session(proj_id)

    assert session.state == SessionState.LEARNING
    assert session.understanding_summary.required is True
    assert session.understanding_summary.latest_state == "MISUNDERSTOOD"
    assert session.understanding_summary.action == "EXPLAIN_BACK"


def test_state_4_all_important_actions_complete_stable(clean_db):
    """Test 4: all important actions complete -> Expected: STABLE."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # 1. Code change
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(0),
        payload={"changed_files": ["math.py"], "summary": "feat"},
        provenance={"source": "git"},
    )
    # 2. Passing automated test suite
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.TEST_FINISHED,
        source=ObservationSource.PYTEST,
        timestamp=_dt(10),
        payload={"status": "PASSED", "test_name": "tests/test_math.py"},
        provenance={"source": "pytest"},
    )
    # 3. M7 Comprehension evaluated as UNDERSTOOD
    with clean_db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO comprehension_runs (
                run_id, project_id, changeset_id, packet_id, prompt_id,
                attempt_number, run_status, overall_state, gap_count, started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_clean",
                proj_id,
                "cs_clean",
                "pkt_clean",
                "prmpt_clean",
                1,
                "COMPLETED",
                "UNDERSTOOD",
                0,
                _dt(20),
                _dt(20),
            ),
        )

    sess_svc = SessionService(clean_db, obs_svc)
    session = sess_svc.get_or_create_session(proj_id)

    assert session.state == SessionState.STABLE
    assert session.verification_summary.targeted_test_observed is True
    assert session.understanding_summary.latest_state == "UNDERSTOOD"


def test_state_5_insufficient_evidence_unknown(clean_db):
    """Test 5: insufficient evidence -> Expected: UNKNOWN."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)

    # Case A: Zero observations whatsoever
    sess_svc = SessionService(clean_db, obs_svc)
    sess_empty = sess_svc.get_or_create_session(proj_id)
    assert sess_empty.state == SessionState.UNKNOWN

    # Case B: Code changed, but zero runtime/test observations recorded
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(0),
        payload={"changed_files": ["math.py"], "summary": "untested code changes"},
        provenance={"source": "git"},
    )
    sess_untested = sess_svc.get_or_create_session(proj_id)
    assert sess_untested.state == SessionState.UNKNOWN
    assert any("No runtime execution" in u for u in sess_untested.summary.unknowns)


# ---------------------------------------------------------------------------
# 2. Project Isolation & Security
# ---------------------------------------------------------------------------

def test_strict_project_isolation(clean_db):
    """Verifies Project A session never leaks Project B data or observations."""
    proj_a = _setup_project(clean_db, "proj_isolate_a")
    proj_b = _setup_project(clean_db, "proj_isolate_b")
    obs_svc = ObservationService(clean_db)

    # Record error only for Project A
    obs_svc.record_event(
        project_id=proj_a,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={"error_kind": "RuntimeCrash", "message": "Crash in A", "file_path": "a.py", "line_number": 1},
        provenance={"source": "cli"},
    )

    sess_svc = SessionService(clean_db, obs_svc)
    session_a = sess_svc.get_or_create_session(proj_a)
    session_b = sess_svc.get_or_create_session(proj_b)

    assert session_a.state == SessionState.INVESTIGATING
    assert session_a.active_incident is not None

    # Project B must remain completely unaffected (empty / UNKNOWN)
    assert session_b.state == SessionState.UNKNOWN
    assert session_b.active_incident is None
    assert session_b.summary.total_observations == 0


# ---------------------------------------------------------------------------
# 3. Dynamic Refresh & Stale-State Prevention
# ---------------------------------------------------------------------------

def test_session_refresh_retires_completed_actions(clean_db):
    """Verifies that new observations retire prior actions upon session refresh."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)
    sess_svc = SessionService(clean_db, obs_svc)

    # 1. Error -> Investigating
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={"error_kind": "ZeroDivisionError", "message": "div 0", "file_path": "app.py", "line_number": 10},
        provenance={"source": "cli"},
    )
    s1 = sess_svc.get_or_create_session(proj_id)
    assert s1.state == SessionState.INVESTIGATING
    assert s1.next_action.action_type in (ActionType.INVESTIGATE_ERROR, ActionType.CHECK_RUNTIME)

    # 2. Fix + Test pass -> Refreshed session retires investigation action
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(10),
        payload={"changed_files": ["app.py"], "summary": "fix"},
        provenance={"source": "git"},
    )
    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.TEST_FINISHED,
        source=ObservationSource.PYTEST,
        timestamp=_dt(20),
        payload={"status": "PASSED", "test_name": "test_app"},
        provenance={"source": "pytest"},
    )

    s2 = sess_svc.refresh_session(proj_id)
    assert s2.state != SessionState.INVESTIGATING
    assert s2.verification_summary.targeted_test_observed is True


# ---------------------------------------------------------------------------
# 4. JSON DTO Contract & Subsystem Summaries
# ---------------------------------------------------------------------------

def test_session_api_contract_and_formatting(clean_db):
    """Verifies session.to_api_dict() and format_human_session conform to spec."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)
    sess_svc = SessionService(clean_db, obs_svc)

    obs_svc.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={"error_kind": "IndexError", "message": "index out of range", "file_path": "list.py", "line_number": 5},
        provenance={"source": "cli"},
    )

    session = sess_svc.get_or_create_session(proj_id)
    dto = session.to_api_dict()

    assert dto["session_id"] == f"session_{proj_id}"
    assert dto["project_id"] == proj_id
    assert dto["state"] == "INVESTIGATING"
    assert "summary" in dto
    assert "verification" in dto
    assert "understanding" in dto
    assert "next_action" in dto

    human_text = format_human_session(session, project_name="My Project")
    assert "BUILD COACH" in human_text
    assert "State:\nINVESTIGATING" in human_text
    assert "NEXT ACTION:" in human_text


# ---------------------------------------------------------------------------
# 5. Unified Actions & Bridge Route (Section 14 & 19)
# ---------------------------------------------------------------------------

def test_unified_actions_and_bridge_route(clean_db):
    """Verifies what_happened, what_should_i_do, do_i_understand and GET /v1/projects/{id}/session."""
    proj_id = _setup_project(clean_db)
    obs_svc = ObservationService(clean_db)
    sess_svc = SessionService(clean_db, obs_svc)

    # 1. Test unified action interfaces
    guidance_plan = sess_svc.what_should_i_do(proj_id)
    assert guidance_plan.project_id == proj_id

    und = sess_svc.do_i_understand(proj_id)
    assert und["project_id"] == proj_id

    # 2. Bridge router handler
    router = BridgeRouter(clean_db)
    status_code, response_dict = router.handle_get_session(proj_id)
    assert status_code == 200
    assert response_dict["ok"] is True
    assert response_dict["message_type"] == "session_result"
    assert response_dict["result"]["project_id"] == proj_id

    # 3. Bridge router 404 for unknown project
    status_code_404, resp_404 = router.handle_get_session("unknown_prj_xyz")
    assert status_code_404 == 400 or status_code_404 == 404


# ---------------------------------------------------------------------------
# 6. CLI runner integration
# ---------------------------------------------------------------------------

def test_cli_runner_session_show(clean_db):
    """Verifies run_session_show CLI function."""
    proj_id = _setup_project(clean_db)
    result = run_session_show(clean_db, proj_id)
    assert result["project_id"] == proj_id
    assert "session" in result
    assert "human_text" in result
    assert "BUILD COACH" in result["human_text"]
