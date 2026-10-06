from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import MagicMock

from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
    FixStatus,
    TimelineExplanationPacket,
    IncidentExplanation,
    StatementWithEvidence,
)
from backend.observation.correlator import ObservationCorrelator
from backend.observation.validator import TimelineExplanationValidator
from backend.observation.generator import TimelineExplanationGenerator
from backend.observation.service import ObservationService


def _dt(offset_seconds: int = 0) -> str:
    base = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def test_packet_builder_focused_window_and_relevance():
    # 1. Old unrelated file change
    # 2. File change on routes.py
    # 3. Error on routes.py (incident)
    # 4. Another unrelated change on frontend.js
    # 5. Fix change on routes.py
    # 6. Verification: restart / test passed
    events = [
        ObservationEvent(
            event_id="evt_old",
            project_id="proj_1",
            timestamp=_dt(-100),
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            payload={"changed_files": ["backend/old.py"], "summary": "old edit"},
        ),
        ObservationEvent(
            event_id="evt_pre",
            project_id="proj_1",
            timestamp=_dt(-10),
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            payload={"changed_files": ["backend/routes.py"], "summary": "bad import added"},
        ),
        ObservationEvent(
            event_id="evt_err",
            project_id="proj_1",
            timestamp=_dt(0),
            event_type=ObservationEventType.RUNTIME_ERROR,
            source=ObservationSource.TERMINAL,
            payload={
                "error_kind": "ModuleNotFoundError",
                "message": "No module named 'missing'",
                "file_path": "backend/routes.py",
                "error_signature": "sig_err_1",
            },
        ),
        ObservationEvent(
            event_id="evt_unrelated",
            project_id="proj_1",
            timestamp=_dt(5),
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            payload={"changed_files": ["frontend/styles.css"], "summary": "css tweak"},
        ),
        ObservationEvent(
            event_id="evt_fix",
            project_id="proj_1",
            timestamp=_dt(10),
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            payload={"changed_files": ["backend/routes.py"], "summary": "fixed import"},
        ),
        ObservationEvent(
            event_id="evt_verify",
            project_id="proj_1",
            timestamp=_dt(20),
            event_type=ObservationEventType.TEST_FINISHED,
            source=ObservationSource.PYTEST,
            payload={"status": "PASSED", "test_name": "test_routes"},
        ),
    ]

    correlator = ObservationCorrelator()
    packet = correlator.build_incident_explanation_packet(events, "proj_1", "evt_err")
    assert packet is not None
    assert packet.packet_version == "explanation-v1"
    assert packet.project_id == "proj_1"
    assert packet.incident["event_id"] == "evt_err"
    assert packet.fix_status == FixStatus.VERIFIED
    assert packet.confidence == "HIGH"

    # Check focused events: old.py should NOT be included; styles.css should NOT be included
    timeline_ids = [e["event_id"] for e in packet.timeline]
    assert "evt_err" in timeline_ids
    assert "evt_pre" in timeline_ids
    assert "evt_fix" in timeline_ids
    assert "evt_verify" in timeline_ids
    assert "evt_old" not in timeline_ids
    assert "evt_unrelated" not in timeline_ids


def test_fix_status_transitions():
    correlator = ObservationCorrelator()
    # Scenario A: Error + Change + No verification -> UNKNOWN
    events_a = [
        ObservationEvent(
            event_id="err_1",
            project_id="p1",
            timestamp=_dt(0),
            event_type=ObservationEventType.RUNTIME_ERROR,
            source=ObservationSource.TERMINAL,
            payload={"error_kind": "ValueError", "file_path": "app.py", "error_signature": "s1"},
        ),
        ObservationEvent(
            event_id="chg_1",
            project_id="p1",
            timestamp=_dt(5),
            event_type=ObservationEventType.GIT_CHANGE,
            source=ObservationSource.GIT,
            payload={"changed_files": ["app.py"]},
        ),
    ]
    packet_a = correlator.build_incident_explanation_packet(events_a, "p1", "err_1")
    assert packet_a.fix_status == FixStatus.UNKNOWN
    assert packet_a.confidence == "LOW"

    # Scenario B: Error + Change + Process restart / HTTP 200 (Recovered, NOT Verified)
    events_b = list(events_a) + [
        ObservationEvent(
            event_id="proc_1",
            project_id="p1",
            timestamp=_dt(10),
            event_type=ObservationEventType.COMMAND_FINISHED,
            source=ObservationSource.TERMINAL,
            payload={"command": "python app.py", "exit_code": 0},
        ),
        ObservationEvent(
            event_id="http_1",
            project_id="p1",
            timestamp=_dt(12),
            event_type=ObservationEventType.HTTP_RESPONSE,
            source=ObservationSource.HTTP,
            payload={"status_code": 200, "url": "http://127.0.0.1:8000/api"},
        ),
    ]
    packet_b = correlator.build_incident_explanation_packet(events_b, "p1", "err_1")
    assert packet_b.fix_status == FixStatus.RECOVERED
    assert packet_b.fix_status != FixStatus.VERIFIED
    assert packet_b.confidence == "MEDIUM"

    # Scenario C: Error + Change + Targeted Test Pass -> VERIFIED
    events_c = list(events_b) + [
        ObservationEvent(
            event_id="test_1",
            project_id="p1",
            timestamp=_dt(15),
            event_type=ObservationEventType.TEST_FINISHED,
            source=ObservationSource.PYTEST,
            payload={"passed": True, "failed": False, "target": "test_app", "status": "PASSED"},
        )
    ]
    packet_c = correlator.build_incident_explanation_packet(events_c, "p1", "err_1")
    assert packet_c.fix_status == FixStatus.VERIFIED
    assert packet_c.confidence == "HIGH"

    # Scenario D: Error + Change + Recurred -> PERSISTING
    events_d = list(events_b) + [
        ObservationEvent(
            event_id="err_2",
            project_id="p1",
            timestamp=_dt(20),
            event_type=ObservationEventType.RUNTIME_ERROR,
            source=ObservationSource.TERMINAL,
            payload={"error_kind": "ValueError", "file_path": "app.py", "error_signature": "s1"},
        )
    ]
    packet_d = correlator.build_incident_explanation_packet(events_d, "p1", "err_1")
    assert packet_d.fix_status == FixStatus.PERSISTING
    assert packet_d.confidence == "HIGH"


def test_validator_groundedness_and_forbidden_language():
    packet = TimelineExplanationPacket(
        packet_id="pkt_123",
        project_id="proj_1",
        incident={"event_id": "evt_err", "type": "runtime_error"},
        timeline=[
            {"event_id": "evt_err"},
            {"event_id": "evt_chg"},
            {"event_id": "evt_test"},
        ],
        changes=[{"event_id": "evt_chg"}],
        correlations=[],
        verification=[{"event_id": "evt_test"}],
        fix_status=FixStatus.VERIFIED,
        confidence="HIGH",
        unknowns=["Whether load test succeeds"],
    )

    valid_explanation = IncidentExplanation(
        explanation_id="exp_1",
        packet_id="pkt_123",
        project_id="proj_1",
        fix_status=FixStatus.VERIFIED,
        confidence="HIGH",
        summary="Error followed by change and passing test.",
        problem=[StatementWithEvidence(statement="Runtime error in routes.", evidence_refs=["evt_err"])],
        observed_sequence=[
            StatementWithEvidence(statement="Error seen", evidence_refs=["evt_err"]),
            StatementWithEvidence(statement="Code changed", evidence_refs=["evt_chg"]),
        ],
        changes=[StatementWithEvidence(statement="Updated imports", evidence_refs=["evt_chg"])],
        verification=[StatementWithEvidence(statement="Tests passed", evidence_refs=["evt_test"])],
        what_to_understand=["Ensure modules exist in dependencies."],
        unknowns=["Build Coach cannot prove the code change was the sole cause."],
        evidence_refs=["evt_err", "evt_chg", "evt_test"],
        ai_generated=True,
    )

    validator = TimelineExplanationValidator()
    is_valid, errors = validator.validate_explanation(valid_explanation, packet)
    assert is_valid, f"Expected valid explanation, got errors: {errors}"

    # 1. Hallucinated evidence ID
    invalid_refs_exp = valid_explanation.model_copy(deep=True)
    invalid_refs_exp.problem[0].evidence_refs = ["evt_fake_999"]
    is_valid, errors = validator.validate_explanation(invalid_refs_exp, packet)
    assert not is_valid
    assert any("cited unknown evidence reference" in e for e in errors)

    # 2. Missing evidence refs in factual section
    no_refs_exp = valid_explanation.model_copy(deep=True)
    no_refs_exp.verification[0].evidence_refs = []
    is_valid, errors = validator.validate_explanation(no_refs_exp, packet)
    assert not is_valid
    assert any("lacks evidence references" in e for e in errors)

    # 3. Forbidden causality claim
    forbidden_lang_exp = valid_explanation.model_copy(deep=True)
    forbidden_lang_exp.summary = "This change was the sole cause of the fix."
    is_valid, errors = validator.validate_explanation(forbidden_lang_exp, packet)
    assert not is_valid
    assert any("Forbidden ungrounded causality language" in e for e in errors)

    # 4. Status mismatch
    mismatch_status_exp = valid_explanation.model_copy(deep=True)
    mismatch_status_exp.fix_status = FixStatus.RECOVERED
    # Modify packet to UNKNOWN to test mismatch check
    packet_unknown = packet.model_copy(deep=True)
    packet_unknown.fix_status = FixStatus.UNKNOWN
    is_valid, errors = validator.validate_explanation(mismatch_status_exp, packet_unknown)
    assert not is_valid
    assert any("Fix status mismatch" in e for e in errors)


def test_generator_offline_and_consent_fallback():
    generator = TimelineExplanationGenerator()
    packet = TimelineExplanationPacket(
        packet_id="pkt_999",
        project_id="proj_1",
        incident={
            "event_id": "evt_err",
            "type": "runtime_error",
            "error_type": "ImportError",
            "message": "cannot import name 'X'",
            "location": "app.py",
        },
        timeline=[
            {
                "event_id": "evt_err",
                "timestamp": _dt(0),
                "type": "runtime_error",
                "summary": "ImportError: cannot import name 'X'",
            }
        ],
        changes=[],
        correlations=[],
        verification=[],
        fix_status=FixStatus.UNKNOWN,
        confidence="LOW",
        unknowns=["No subsequent code changes or verification recorded."],
    )

    # No consent provided -> Must fall back cleanly to deterministic explanation without exception
    result_no_consent = generator.explain(packet, consent_token=None)
    assert result_no_consent is not None
    assert result_no_consent.ai_generated is False
    assert result_no_consent.packet_id == "pkt_999"
    assert result_no_consent.fix_status == FixStatus.UNKNOWN
    assert "evt_err" in result_no_consent.evidence_refs


def test_generator_with_mocked_llm_success_and_ungrounded_rejection():
    from backend.ai_gateway.consent import ConsentManager
    packet = TimelineExplanationPacket(
        packet_id="pkt_mock",
        project_id="proj_1",
        incident={
            "event_id": "err_1",
            "type": "runtime_error",
            "error_type": "KeyError",
            "message": "'id'",
            "location": "service.py",
        },
        timeline=[
            {"event_id": "err_1", "type": "runtime_error", "summary": "KeyError: 'id'"},
            {"event_id": "chg_1", "type": "code_change", "summary": "Added id validation"},
            {"event_id": "test_1", "type": "test_run", "summary": "test_service passed"},
        ],
        changes=[{"event_id": "chg_1"}],
        correlations=[],
        verification=[{"event_id": "test_1"}],
        fix_status=FixStatus.VERIFIED,
        confidence="HIGH",
        unknowns=["Production load response"],
    )

    mock_llm_response = """
    {
      "summary": "The KeyError occurred when looking for key 'id', and after adding validation the tests passed.",
      "problem": [
        {"statement": "Service crashed due to KeyError 'id' in service.py.", "evidence_refs": ["err_1"]}
      ],
      "observed_sequence": [
        {"statement": "KeyError raised", "evidence_refs": ["err_1"]},
        {"statement": "Code updated with validation", "evidence_refs": ["chg_1"]},
        {"statement": "Unit tests executed and passed", "evidence_refs": ["test_1"]}
      ],
      "changes": [
        {"statement": "Validated 'id' parameter presence before access", "evidence_refs": ["chg_1"]}
      ],
      "verification": [
        {"statement": "test_service verified no error occurred", "evidence_refs": ["test_1"]}
      ],
      "what_to_understand": ["Accessing missing dict keys without .get() or prior checks causes KeyError."],
      "unknowns": ["Build Coach cannot prove the fix was the sole cause; temporal correlation does not establish causality."]
    }
    """

    mock_gateway = MagicMock()
    mock_gateway.provider_adapter.get_provider_name.return_value = "gemini"
    mock_gateway.provider_adapter.get_model_name.return_value = "gemini-3.8-flash"
    mock_gateway.provider_adapter.complete_interaction.return_value = MagicMock(content=mock_llm_response)
    mock_gateway.credential_store.get_gemini_api_key.return_value = "fake_key"

    generator = TimelineExplanationGenerator(gateway=mock_gateway)
    ctx_pkt = generator.convert_packet_to_context_packet(packet)
    token = ConsentManager.grant_consent(ctx_pkt, provider="gemini", model="gemini-3.8-flash")

    # 1. Successful LLM explanation pass
    explanation = generator.explain(packet, consent_token=token)
    assert explanation.ai_generated is True
    assert explanation.fix_status == FixStatus.VERIFIED
    assert "err_1" in explanation.problem[0].evidence_refs
    assert "test_1" in explanation.verification[0].evidence_refs

    # 2. Hallucinated LLM response (cites fake evidence id 'err_fake') -> must be rejected and fallback to deterministic
    mock_hallucinated_response = mock_llm_response.replace('"err_1"', '"err_fake"')
    mock_gateway.provider_adapter.complete_interaction.return_value = MagicMock(content=mock_hallucinated_response)

    fallback_explanation = generator.explain(packet, consent_token=token)
    assert fallback_explanation.ai_generated is False  # Rejected and safely fell back!
    assert "err_1" in fallback_explanation.evidence_refs
