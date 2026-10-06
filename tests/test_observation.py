"""Comprehensive test suite for Milestone 12.5: Runtime Failure & Change Observation."""

import io
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from backend.domain.models import Project
from backend.project_model.db import Database
from backend.project_model.migrations import (
    apply_migrations,
    get_current_schema_version,
    MIGRATIONS,
)
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
    CorrelationRelation,
)
from backend.observation.normalizer import (
    parse_stack_trace_and_error,
    compute_error_signature,
    sanitize_and_redact_payload,
)
from backend.observation.service import ObservationService
from backend.cli.main import main


@pytest.fixture
def test_project(tmp_path: Path):
    """Sets up a registered test project with Database."""
    proj_dir = tmp_path / "obs_project"
    proj_dir.mkdir()
    (proj_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")

    db_path = proj_dir / ".buildcoach" / "state.db"
    db = Database(db_path)
    project = Project(
        id="prj_obs_test",
        name="Observation Test Project",
        root_path=str(proj_dir.resolve()),
    )
    db.upsert_project(project)
    return {
        "proj_dir": proj_dir,
        "db": db,
        "project": project,
    }


def test_schema_migration_v11_applied(tmp_path: Path):
    """Verifies that migration v11 creates observation_events with index."""
    db_path = tmp_path / "test_mig_v11.db"
    db = Database(db_path)
    assert db.get_schema_version() == len(MIGRATIONS)

    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='observation_events'")
    assert cur.fetchone() is not None
    conn.close()


def test_event_validation_and_rejection_unknown_type(test_project):
    """Verifies that attempting to record an unknown event_type raises ValueError."""
    db = test_project["db"]
    project = test_project["project"]
    service = ObservationService(db)

    with pytest.raises(ValueError) as exc:
        service.record_event(
            project_id=project.id,
            event_type="UNAUTHORIZED_MONITORING",
            source=ObservationSource.TERMINAL,
            payload={},
            provenance={},
        )
    assert "Invalid event_type" in str(exc.value)


def test_event_validation_unknown_project(test_project):
    """Verifies that attempting to record for an unregistered project raises ValueError."""
    db = test_project["db"]
    service = ObservationService(db)

    with pytest.raises(ValueError) as exc:
        service.record_event(
            project_id="prj_ghost_unknown",
            event_type=ObservationEventType.RUNTIME_ERROR,
            source=ObservationSource.TERMINAL,
            payload={},
            provenance={},
        )
    assert "Project not found in registry" in str(exc.value)


def test_secret_redaction_before_event_persistence(test_project):
    """Verifies that secrets in event payload/provenance are thoroughly redacted."""
    db = test_project["db"]
    project = test_project["project"]
    service = ObservationService(db)

    raw_secret = "sk-proj-supersecretkey123456789012345"
    jwt_secret = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"

    event = service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.COMMAND_FINISHED,
        source=ObservationSource.TERMINAL,
        payload={
            "command": f"curl -H 'Authorization: {jwt_secret}'",
            "api_key": raw_secret,
            "nested": {"secret": raw_secret},
        },
        provenance={"token": raw_secret},
    )

    # Inspect stored event
    stored = db.get_observation_event(event.event_id)
    assert stored is not None
    assert raw_secret not in json.dumps(stored.payload)
    assert raw_secret not in json.dumps(stored.provenance)
    assert "[REDACTED]" in json.dumps(stored.payload)


def test_stack_trace_parsing_and_error_normalization():
    """Verifies deterministic parsing of Python stack traces without root cause invention."""
    trace = """
Traceback (most recent call last):
  File "backend/server.py", line 12, in start
    from backend.database import init_db
  File "backend/routes.py", line 42, in <module>
    import nonexistent_module
ModuleNotFoundError: No module named 'nonexistent_module'
"""
    parsed = parse_stack_trace_and_error(trace)
    assert parsed is not None
    assert parsed.error_kind == "ModuleNotFoundError"
    assert "nonexistent_module" in parsed.message
    assert parsed.file_path == "backend/routes.py"
    assert parsed.line_number == 42
    assert parsed.error_signature.startswith("errsig_")


def test_stable_error_signatures():
    """Verifies identical error signatures across different timestamps and memory addresses."""
    sig1 = compute_error_signature("ModuleNotFoundError", "No module named 'backend.database'", "routes.py")
    sig2 = compute_error_signature("ModuleNotFoundError", "No module named 'backend.database'", "routes.py")
    assert sig1 == sig2

    # Memory address variance is stripped
    msg_a = "Object at 0x7f9a12bc failed"
    msg_b = "Object at 0x7f9a99aa failed"
    sig_a = compute_error_signature("RuntimeError", msg_a)
    sig_b = compute_error_signature("RuntimeError", msg_b)
    assert sig_a == sig_b


def test_timeline_chronological_ordering(test_project):
    """Verifies that get_timeline returns events sorted timestamp ASC."""
    db = test_project["db"]
    project = test_project["project"]
    service = ObservationService(db)

    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.COMMAND_FINISHED,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T10:05:00Z",
        payload={"step": 3},
        provenance={},
    )
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp="2026-10-06T10:01:00Z",
        payload={"step": 1},
        provenance={},
    )
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T10:03:00Z",
        payload={"step": 2},
        provenance={},
    )

    tl = service.get_timeline(project.id)
    assert len(tl) == 3
    assert [e.payload["step"] for e in tl] == [1, 2, 3]


def test_icecream_change_failure_recovery_correlation(test_project):
    """Tests the canonical scenario:
    1. Git change: backend/routes.py
    2. Command: backend start
    3. Error: ModuleNotFoundError
    4. Git change: backend/routes.py
    5. Command: backend restart (success)
    6. HTTP: GET /api/icecreams -> 200
    
    Verifies:
    - CHANGE, ERROR, CHANGE, RECOVERY detected
    - Zero claims of unproven root cause causality
    - Epistemic unknowns explicitly declared
    """
    db = test_project["db"]
    project = test_project["project"]
    service = ObservationService(db)

    # 1. 10:01 - Git Change on backend/routes.py
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp="2026-10-06T10:01:00Z",
        payload={"changed_files": ["backend/routes.py"], "summary": "Modified routes"},
        provenance={"source": "GIT"},
    )

    # 2 & 3. 10:02 - Command run & Error: ModuleNotFoundError backend.database
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.COMMAND_FINISHED,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T10:02:00Z",
        payload={"command": "python -m backend.main", "exit_code": 1},
        provenance={"source": "TERMINAL"},
    )
    err_sig = compute_error_signature("ModuleNotFoundError", "No module named 'backend.database'", "backend/routes.py")
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T10:02:05Z",
        payload={
            "error_kind": "ModuleNotFoundError",
            "message": "No module named 'backend.database'",
            "file_path": "backend/routes.py",
            "line_number": 15,
            "error_signature": err_sig,
        },
        provenance={"source": "TERMINAL"},
    )

    # 4. 10:03 - Second Git Change on backend/routes.py
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp="2026-10-06T10:03:00Z",
        payload={"changed_files": ["backend/routes.py"], "summary": "Fixed import in routes"},
        provenance={"source": "GIT"},
    )

    # 5. 10:04 - Successful process / restart
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.COMMAND_FINISHED,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T10:04:00Z",
        payload={"command": "python -m backend.main", "exit_code": 0},
        provenance={"source": "TERMINAL"},
    )

    # 6. 10:05 - HTTP 200 response
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.HTTP_RESPONSE,
        source=ObservationSource.HTTP,
        timestamp="2026-10-06T10:05:00Z",
        payload={"method": "GET", "path": "/api/icecreams", "status_code": 200},
        provenance={"source": "HTTP"},
    )

    # Correlate & Explanation
    packet = service.get_explanation_packet(project.id)
    assert packet.problem["error_kind"] == "ModuleNotFoundError"
    assert len(packet.changes) == 2
    assert packet.recovery["status"] == "RECOVERED"
    assert packet.recovery["relation"] == CorrelationRelation.ERROR_DISAPPEARED_AFTER_CHANGE

    # Verify Epistemic unknowns are explicitly present
    assert any("sole cause" in u for u in packet.unknowns)
    assert any("causal proof" in u for u in packet.unknowns)


def test_icecream_persistent_error_no_recovery_claimed(test_project):
    """Verifies that if the error recurs after a change, NO recovery is claimed."""
    db = test_project["db"]
    project = test_project["project"]
    service = ObservationService(db)

    err_sig = compute_error_signature("SyntaxError", "invalid syntax", "app.py")

    # 1. Error
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T11:00:00Z",
        payload={"error_kind": "SyntaxError", "message": "invalid syntax", "file_path": "app.py", "error_signature": err_sig},
        provenance={},
    )

    # 2. Change
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp="2026-10-06T11:01:00Z",
        payload={"changed_files": ["app.py"]},
        provenance={},
    )

    # 3. Same Error recurs
    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp="2026-10-06T11:02:00Z",
        payload={"error_kind": "SyntaxError", "message": "invalid syntax", "file_path": "app.py", "error_signature": err_sig},
        provenance={},
    )

    packet = service.get_explanation_packet(project.id)
    assert packet.recovery["status"] == "UNRESOLVED"
    assert "disappear" in packet.recovery["description"]


def test_cli_observation_timeline_json(test_project):
    """Verifies 'ai-build-coach observation timeline <project_id> --json' CLI output."""
    db = test_project["db"]
    project = test_project["project"]
    proj_dir = test_project["proj_dir"]
    service = ObservationService(db)

    service.record_event(
        project_id=project.id,
        event_type=ObservationEventType.COMMAND_FINISHED,
        source=ObservationSource.TERMINAL,
        payload={"command": "ls"},
        provenance={},
    )

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "observation", "timeline",
            project.id,
            "--json",
        ])

    assert rc == 0
    raw = buf.getvalue().strip()
    data = json.loads(raw)
    assert data["status"] == "success"
    assert data["command"] == "observation"
    assert data["action"] == "timeline"
    assert data["data"]["total_events"] == 1


def test_cli_observation_explanation_json(test_project):
    """Verifies 'ai-build-coach observation explanation <project_id> --json' CLI output."""
    db = test_project["db"]
    project = test_project["project"]
    proj_dir = test_project["proj_dir"]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "observation", "explanation",
            project.id,
            "--json",
        ])

    assert rc == 0
    data = json.loads(buf.getvalue().strip())
    assert data["status"] == "success"
    assert "explanation" in data["data"]
    assert "unknowns" in data["data"]["explanation"]
