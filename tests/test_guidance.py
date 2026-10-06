"""Comprehensive test suite for Milestone 12.7: Knowledge Gap & Next Action Engine."""

from datetime import datetime, timezone, timedelta
import pytest
from pathlib import Path

from backend.domain.models import Project
from backend.project_model.db import Database
from backend.project_model.migrations import apply_migrations
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
    FixStatus,
    TimelineExplanationPacket,
    IncidentExplanation,
    StatementWithEvidence,
)
from backend.observation.service import ObservationService
from backend.guidance.models import (
    KnowledgeGap,
    NextAction,
    GuidancePlan,
    GuidanceStatus,
    GapCategory,
    ActionType,
    ActionPriority,
    ActionStatus,
)
from backend.guidance.detector import KnowledgeGapDetector
from backend.guidance.planner import NextActionPlanner
from backend.guidance.ranking import ActionRanker
from backend.guidance.service import GuidanceService


def _dt(offset_seconds: int = 0) -> str:
    base = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


@pytest.fixture
def clean_db(tmp_path):
    db_file = tmp_path / "test_guidance.db"
    db = Database(str(db_file))
    return db


def _setup_project(db: Database, project_id: str = "proj_test") -> str:
    p = Project(
        id=project_id,
        name="Test Project",
        root_path=f"/path/to/{project_id}",
        created_at=_dt(-3600),
        updated_at=_dt(-3600),
    )
    db.upsert_project(p)
    return project_id


# ---------------------------------------------------------------------------
# 1. Critical Scenario 1: Persistent Error -> UNRESOLVED_ERROR / INVESTIGATE_ERROR
# ---------------------------------------------------------------------------
def test_scenario_1_persistent_error_investigation(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # Error -> Change -> Same error recurring
    e_err1 = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={
            "error_kind": "ZeroDivisionError",
            "message": "division by zero",
            "file_path": "math_ops.py",
            "line_number": 42,
            "error_signature": "sig_zero_div",
        },
        provenance={"source": "cli"},
    )
    e_chg = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(10),
        payload={"changed_files": ["math_ops.py"], "summary": "attempted fix"},
        provenance={"source": "git"},
    )
    e_err2 = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(20),
        payload={
            "error_kind": "ZeroDivisionError",
            "message": "division by zero",
            "file_path": "math_ops.py",
            "line_number": 42,
            "error_signature": "sig_zero_div",
        },
        provenance={"source": "cli"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    plan = guide_service.generate_plan(proj_id, incident_id=e_err1.event_id)

    assert plan.status == GuidanceStatus.CURRENT
    assert plan.top_next_action is not None
    assert plan.top_next_action.action_type == ActionType.INVESTIGATE_ERROR
    assert plan.top_next_action.priority == ActionPriority.CRITICAL
    assert e_err1.event_id in plan.top_next_action.evidence_ids

    # Gaps check
    unresolved_gaps = [g for g in plan.gaps if g.category == GapCategory.UNRESOLVED_ERROR]
    assert len(unresolved_gaps) == 1
    assert unresolved_gaps[0].priority == ActionPriority.CRITICAL


# ---------------------------------------------------------------------------
# 2. Critical Scenario 2: Error Recovered Without Test -> TEST_COVERAGE / RUN_TEST
# ---------------------------------------------------------------------------
def test_scenario_2_recovered_without_targeted_test(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # Error -> Change -> Clean restart / HTTP 200 (operational recovery without test)
    e_err = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={
            "error_kind": "KeyError",
            "message": "'token'",
            "file_path": "auth.py",
            "error_signature": "sig_auth_key",
        },
        provenance={"source": "cli"},
    )
    e_chg = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(5),
        payload={"changed_files": ["auth.py"], "summary": "check token in dict"},
        provenance={"source": "git"},
    )
    e_http = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.HTTP_RESPONSE,
        source=ObservationSource.HTTP,
        timestamp=_dt(10),
        payload={"status_code": 200, "url": "http://127.0.0.1:8000/auth"},
        provenance={"source": "http"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    plan = guide_service.generate_plan(proj_id, incident_id=e_err.event_id)

    assert plan.top_next_action is not None
    assert plan.top_next_action.action_type == ActionType.RUN_TEST
    assert plan.top_next_action.priority == ActionPriority.HIGH
    assert any(g.category == GapCategory.TEST_COVERAGE for g in plan.gaps)


# ---------------------------------------------------------------------------
# 3. Critical Scenario 3: Test Passes -> RUN_TEST Retired
# ---------------------------------------------------------------------------
def test_scenario_3_test_passed_retires_run_test_action(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # Error -> Change -> Test passes!
    e_err = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={
            "error_kind": "KeyError",
            "message": "'token'",
            "file_path": "auth.py",
            "error_signature": "sig_auth_key",
        },
        provenance={"source": "cli"},
    )
    e_chg = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(5),
        payload={"changed_files": ["auth.py"]},
        provenance={"source": "git"},
    )
    e_test = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.TEST_FINISHED,
        source=ObservationSource.PYTEST,
        timestamp=_dt(10),
        payload={"status": "PASSED", "test_name": "test_auth_token"},
        provenance={"source": "pytest"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    plan = guide_service.generate_plan(proj_id, incident_id=e_err.event_id)

    # RUN_TEST must NOT be top action and not active
    if plan.top_next_action:
        assert plan.top_next_action.action_type != ActionType.RUN_TEST
    assert not any(g.category == GapCategory.TEST_COVERAGE for g in plan.gaps)


# ---------------------------------------------------------------------------
# 4. Critical Scenario 4: Architectural Change -> UNDERSTANDING / EXPLAIN_BACK
# ---------------------------------------------------------------------------
def test_scenario_4_understanding_gap_suggests_explain_back(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # Event in timeline
    e_chg = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(0),
        payload={"changed_files": ["backend/cache.py"], "summary": "Add Redis caching layer"},
        provenance={"source": "git"},
    )

    # Mock an IncidentExplanation with concepts to understand
    explanation = IncidentExplanation(
        explanation_id="exp_cache_1",
        packet_id="pkt_cache_1",
        project_id=proj_id,
        fix_status=FixStatus.VERIFIED,
        confidence="HIGH",
        summary="Redis cache integrated.",
        what_to_understand=["Cache invalidation and TTL guarantees."],
        unknowns=["Build Coach cannot prove sole cause."],
        evidence_refs=[e_chg.event_id],
        ai_generated=False,
    )

    detector = KnowledgeGapDetector()
    planner = NextActionPlanner()
    ranker = ActionRanker()

    gaps = detector.detect_gaps(
        project_id=proj_id,
        events=[e_chg],
        explanation=explanation,
        comprehension_history=[],
    )

    und_gaps = [g for g in gaps if g.category == GapCategory.UNDERSTANDING]
    assert len(und_gaps) == 1
    assert und_gaps[0].evidence_ids == [e_chg.event_id]

    actions = planner.plan_actions(proj_id, gaps)
    top_act, _ = ranker.rank_actions(actions)
    assert top_act is not None
    assert top_act.action_type == ActionType.EXPLAIN_BACK
    assert "Can I Explain This?" in top_act.description


# ---------------------------------------------------------------------------
# 5. Critical Scenario 5: Project Isolation
# ---------------------------------------------------------------------------
def test_scenario_5_strict_project_isolation(clean_db):
    proj_a = _setup_project(clean_db, "proj_alpha")
    proj_b = _setup_project(clean_db, "proj_beta")
    obs_service = ObservationService(clean_db)

    # Record error on Project A
    obs_service.record_event(
        project_id=proj_a,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={"error_kind": "ValueError", "file_path": "alpha.py", "error_signature": "sig_a"},
        provenance={"source": "cli"},
    )

    # Record clean git change on Project B
    obs_service.record_event(
        project_id=proj_b,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(1),
        payload={"changed_files": ["beta.py"]},
        provenance={"source": "git"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    plan_a = guide_service.generate_plan(proj_a)
    plan_b = guide_service.generate_plan(proj_b)

    # Project A has verification gap for alpha.py
    assert any("alpha.py" in str(g.metadata) or g.category == GapCategory.VERIFICATION for g in plan_a.gaps)
    # Project B must NEVER have Project A's error or evidence
    for gap in plan_b.gaps:
        assert "sig_a" not in str(gap.metadata)
        assert gap.project_id == proj_b


# ---------------------------------------------------------------------------
# 6. Staleness & Re-evaluation Loop
# ---------------------------------------------------------------------------
def test_staleness_and_recalculation_loop(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # Initial state: error occurred
    e_err = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={"error_kind": "TypeError", "file_path": "types.py", "error_signature": "sig_type"},
        provenance={"source": "cli"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    initial_plan = guide_service.generate_plan(proj_id, incident_id=e_err.event_id)
    assert not guide_service.check_plan_staleness(initial_plan)

    # New observation arrives: user modified the file
    obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.GIT_CHANGE,
        source=ObservationSource.GIT,
        timestamp=_dt(5),
        payload={"changed_files": ["types.py"]},
        provenance={"source": "git"},
    )

    # Plan is now STALE
    assert guide_service.check_plan_staleness(initial_plan) is True

    # Recalculate
    updated_plan = guide_service.generate_plan(proj_id, incident_id=e_err.event_id)
    assert updated_plan.state_fingerprint != initial_plan.state_fingerprint
    assert not guide_service.check_plan_staleness(updated_plan)


# ---------------------------------------------------------------------------
# 7. Priority Ranking & Secondary Actions Capping
# ---------------------------------------------------------------------------
def test_priority_ranking_and_secondary_capping():
    ranker = ActionRanker()

    candidate_actions = [
        NextAction(
            action_id="act_low",
            project_id="p1",
            action_type=ActionType.DOCUMENT_DECISION,
            title="Document rationale",
            description="Add comment",
            priority=ActionPriority.LOW,
            completion_condition="Add docs",
        ),
        NextAction(
            action_id="act_crit",
            project_id="p1",
            action_type=ActionType.INVESTIGATE_ERROR,
            title="Investigate crash",
            description="Check logs",
            priority=ActionPriority.CRITICAL,
            completion_condition="Crash resolved",
        ),
        NextAction(
            action_id="act_high",
            project_id="p1",
            action_type=ActionType.RUN_TEST,
            title="Run test suite",
            description="pytest",
            priority=ActionPriority.HIGH,
            completion_condition="Tests pass",
        ),
        NextAction(
            action_id="act_med1",
            project_id="p1",
            action_type=ActionType.INSPECT_DIFF,
            title="Inspect diff",
            description="Review",
            priority=ActionPriority.MEDIUM,
            completion_condition="Reviewed",
        ),
        NextAction(
            action_id="act_med2",
            project_id="p1",
            action_type=ActionType.EXPLAIN_BACK,
            title="Explain mechanism",
            description="Explain",
            priority=ActionPriority.MEDIUM,
            completion_condition="Understood",
        ),
    ]

    top, secondary = ranker.rank_actions(candidate_actions, max_secondary=2)
    assert top is not None
    assert top.action_id == "act_crit"
    assert len(secondary) == 2
    assert secondary[0].action_id == "act_high"
    # Never exposes more than max_secondary
    assert len(secondary) <= 2


# ---------------------------------------------------------------------------
# 8. Dependency Gap Detection
# ---------------------------------------------------------------------------
def test_dependency_gap_detection(clean_db):
    proj_id = _setup_project(clean_db)
    obs_service = ObservationService(clean_db)

    # ModuleNotFoundError
    e_err = obs_service.record_event(
        project_id=proj_id,
        event_type=ObservationEventType.RUNTIME_ERROR,
        source=ObservationSource.TERMINAL,
        timestamp=_dt(0),
        payload={
            "error_kind": "ModuleNotFoundError",
            "message": "No module named 'fastapi'",
            "file_path": "main.py",
            "error_signature": "sig_dep_mod",
        },
        provenance={"source": "cli"},
    )

    guide_service = GuidanceService(clean_db, obs_service)
    plan = guide_service.generate_plan(proj_id, incident_id=e_err.event_id)

    dep_gaps = [g for g in plan.gaps if g.category == GapCategory.DEPENDENCY]
    assert len(dep_gaps) == 1
    assert "dependency" in dep_gaps[0].description.lower() or "fastapi" in dep_gaps[0].description.lower()
