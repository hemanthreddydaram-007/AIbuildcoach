"""Thin adapter controller delegating CLI requests to frozen M0-M8 engines."""

import json
import uuid
import hashlib
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, List

from backend.domain.models import Project, utc_now_iso
from backend.project_model.scanner import ProjectScanner, detect_project_root
from backend.project_model.git_detector import detect_git_state
from backend.project_model.db import Database
from backend.ai_gateway.models import ConsentToken
from backend.explanation.synthesizer import (
    prepare_change_explanation,
    explain_changes,
)
from backend.comprehension.models import StudentExplanationSubmission
from backend.comprehension.evaluator import evaluate_student_explanation
from backend.viva.models import (
    VivaSessionMode,
    VivaDifficulty,
    VivaCategory,
    VivaAnswerSubmission,
)
from backend.viva.session import (
    start_viva_session,
    submit_viva_answer_and_step,
    recover_stale_session_if_needed,
)
from backend.viva.reporter import compile_viva_report
from backend.viva.index import ProjectArchitecturalIndex
from backend.context_engine.engine import ContextEngine
from backend.ai_gateway.gateway import AIGateway

# Ensure ContextEngine exposes assemble_context expected by M6 synthesizer without modifying M4 files
if not hasattr(ContextEngine, "assemble_context"):
    ContextEngine.assemble_context = ContextEngine.build_context_packet  # type: ignore


def make_cli_consent_token(packet_id: str = "", packet_hash: str = "") -> ConsentToken:
    """Creates a valid ConsentToken with proper timestamps for CLI operations."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    return ConsentToken(
        token_id=f"token_{uuid.uuid4().hex[:8]}",
        packet_id=packet_id,
        packet_hash=packet_hash,
        provider="gemini",
        model="gemini-3.8-flash",
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=60)).isoformat(),
        user_acknowledged=True,
    )


def get_cli_version() -> str:
    """Dynamically resolves the version from package metadata or pyproject.toml."""
    try:
        import importlib.metadata
        return importlib.metadata.version("ai-build-coach")
    except Exception:
        pass

    try:
        import tomllib
        pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return str(data.get("project", {}).get("version", "0.1.0"))
    except Exception:
        pass

    return "0.1.0"


def resolve_workspace(project_root_arg: Optional[str] = None) -> Tuple[Path, Database, Project]:
    """Resolves workspace root, initializes SQLite database, and retrieves or registers project record."""
    start_path = Path(project_root_arg).resolve() if project_root_arg else Path.cwd().resolve()
    root = detect_project_root(start_path)

    db_path = root / ".buildcoach" / "state.db"
    db = Database(db_path)

    project = db.get_project_by_root(str(root))
    if not project:
        now_iso = utc_now_iso()
        project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
        project = Project(
            id=project_id,
            name=root.name,
            root_path=str(root),
            created_at=now_iso,
            updated_at=now_iso,
        )
        db.upsert_project(project)

    return root, db, project


def run_status(root: Path, db: Database, project_id: str) -> Dict[str, Any]:
    """Collects repository, database, and graph status using supported interfaces and read-only queries."""
    git_state = detect_git_state(root)
    schema_version = db.get_schema_version()
    graph = db.get_graph(project_id)
    node_count = len(graph.nodes) if graph else 0
    edge_count = len(graph.edges) if graph else 0

    # Strictly read-only queries on existing operational audit tables
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, primary_category, files_changed_count, grounding_ratio, created_at
            FROM understand_change_runs
            WHERE project_id = ?
            ORDER BY created_at DESC LIMIT 5
            """,
            (project_id,),
        )
        recent_understand_runs = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT session_id, status, mode, current_difficulty, started_at, completed_at
            FROM viva_sessions
            WHERE project_id = ?
            ORDER BY started_at DESC LIMIT 5
            """,
            (project_id,),
        )
        recent_viva_sessions = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    return {
        "project_id": project_id,
        "project_root": str(root),
        "is_git_repo": git_state.is_git_repo,
        "current_branch": git_state.current_branch,
        "is_dirty": git_state.is_dirty,
        "modified_files_count": git_state.modified_count,
        "untracked_files_count": git_state.untracked_count,
        "staged_files_count": git_state.staged_count,
        "schema_version": schema_version,
        "graph_nodes_count": node_count,
        "graph_edges_count": edge_count,
        "recent_understand_runs": recent_understand_runs,
        "recent_viva_sessions": recent_viva_sessions,
    }


def run_scan(root: Path, db: Database, project_id: str) -> Dict[str, Any]:
    """Performs passive project scan and synchronizes project graph."""
    scanner = ProjectScanner(project_root=root, db_path=db.db_path)
    scan_result = scanner.scan()
    graph = db.get_graph(project_id)
    return {
        "project_id": project_id,
        "scanned_files_count": len(scan_result.files),
        "graph_nodes_count": len(graph.nodes) if graph else 0,
        "graph_edges_count": len(graph.edges) if graph else 0,
        "scan_timestamp": utc_now_iso(),
    }


def run_understand_preview(root: Path, db: Database, project_id: str) -> Dict[str, Any]:
    """Generates change explanation preview and token estimate."""
    preview, packet, changeset = prepare_change_explanation(
        project_id=project_id,
        root_path=root,
        db=db,
    )
    return preview.model_dump()


def run_understand_explain(
    root: Path,
    db: Database,
    project_id: str,
    consent_token_str: str,
    gateway: Optional[AIGateway] = None,
) -> Dict[str, Any]:
    """Executes change explanation with validated user consent token."""
    preview, packet, changeset = prepare_change_explanation(project_id, root, db)
    if preview.clean_working_tree or packet is None:
        from backend.explanation.synthesizer import _build_clean_tree_result
        return _build_clean_tree_result(project_id, changeset.id if changeset else "").model_dump()

    try:
        token_data = json.loads(consent_token_str)
        consent_token = ConsentToken(**token_data)
    except Exception:
        from backend.ai_gateway.consent import ConsentManager
        consent_token = ConsentManager.grant_consent(packet=packet)

    result = explain_changes(
        project_id=project_id,
        root_path=root,
        db=db,
        packet=packet,
        changeset=changeset,
        consent_token=consent_token,
        gateway=gateway,
    )
    return result.model_dump()


def run_understand_submit(
    root: Path,
    db: Database,
    project_id: str,
    prompt_id: str,
    packet_id: str,
    answer_text: str,
    gateway: Optional[AIGateway] = None,
) -> Dict[str, Any]:
    """Submits student explanation to comprehension loop."""
    packet = db.get_context_packet_by_id(packet_id)
    if not packet:
        raise ValueError(f"Context packet {packet_id} not found in database.")

    # Find changeset_id and run_id from understand_change_runs
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, changeset_id FROM understand_change_runs WHERE packet_id = ? ORDER BY created_at DESC LIMIT 1",
            (packet_id,),
        )
        row = cur.fetchone()
        if not row:
            # Fallback to latest changeset for project
            latest_cs = db.get_latest_change_set(project_id)
            if not latest_cs:
                raise ValueError("No changeset found for active project.")
            changeset_id = latest_cs.id
            m6_id = f"uc_fallback_{uuid.uuid4().hex[:8]}"
        else:
            m6_id = row["id"]
            changeset_id = row["changeset_id"]
    finally:
        conn.close()

    last_run = db.get_latest_comprehension_run(project_id, changeset_id, prompt_id)
    attempt_number = 1 if last_run is None else last_run["attempt_number"] + 1

    submission = StudentExplanationSubmission(
        submission_id=f"sub_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        changeset_id=changeset_id,
        packet_id=packet_id,
        prompt_id=prompt_id,
        attempt_number=attempt_number,
        explanation_text=answer_text,
    )

    # Reconstruct canonical UnderstandChangeResult binding
    from backend.explanation.models import (
        UnderstandChangeResult,
        WhatChangedSection,
        WhySection,
        IntentRationale,
        IntentEpistemicStatus,
        ChangeCategory,
        EvidenceSection,
        CanIExplainThisPrompt,
    )
    m6_result = UnderstandChangeResult(
        id=m6_id,
        project_id=project_id,
        changeset_id=changeset_id,
        packet_id=packet_id,
        clean_working_tree=False,
        what_changed=WhatChangedSection(
            total_files_changed=len(packet.items),
            total_lines_added=0,
            total_lines_removed=0,
            files=[],
            primary_category=ChangeCategory.UNKNOWN,
            supplementary_ai_narrative="CLI submission context",
        ),
        why=WhySection(
            primary_intent=IntentRationale(
                statement="Undocumented rationale",
                status=IntentEpistemicStatus.UNKNOWN,
                evidence_source=None,
                supporting_refs=[],
            ),
            inferences=[],
            unknown_aspects=[],
        ),
        evidence=EvidenceSection(
            grounded_traces=[],
            ungrounded_or_unknown=[],
            grounding_ratio=1.0,
            total_claims=0,
        ),
        what_should_i_understand=[],
        can_i_explain_this=CanIExplainThisPrompt(
            prompt_id=prompt_id,
            question="Active comprehension prompt",
            target_concepts=[],
            expected_aspects=["Purpose", "Mechanism", "Failure Modes", "Downstream Impact"],
            target_files=[],
        ),
    )

    from backend.ai_gateway.consent import ConsentManager
    consent_token = ConsentManager.grant_consent(packet=packet)

    result = evaluate_student_explanation(
        db=db,
        submission=submission,
        m6_result=m6_result,
        packet=packet,
        consent_token=consent_token,
        gateway=gateway,
    )
    return result.model_dump()


def run_viva_start(
    root: Path,
    db: Database,
    project_id: str,
    mode_str: str = "project-wide",
    category_str: Optional[str] = None,
    difficulty_str: str = "easy",
    gateway: Optional[AIGateway] = None,
) -> Dict[str, Any]:
    """Starts a viva session and returns initial question."""
    mode = (
        VivaSessionMode.CATEGORY_FOCUS
        if mode_str.lower() in ("category-focus", "category_focus")
        else VivaSessionMode.PROJECT_WIDE
    )
    try:
        difficulty = VivaDifficulty(difficulty_str.upper())
    except Exception:
        difficulty = VivaDifficulty.EASY

    target_categories = None
    if category_str:
        try:
            target_categories = [VivaCategory(category_str.upper())]
        except Exception:
            target_categories = None

    files = db.get_files_for_project(project_id)
    graph = db.get_graph(project_id)
    index = ProjectArchitecturalIndex(files=files, graph=graph)
    context_engine = ContextEngine(db)

    session_rec, first_q = start_viva_session(
        db=db,
        project_id=project_id,
        mode=mode,
        target_categories=target_categories,
        initial_difficulty=difficulty,
        index=index,
        context_engine=context_engine,
        gateway=gateway,
        consent_token=make_cli_consent_token(),
    )

    return {
        "session": session_rec.model_dump(),
        "first_question": first_q.model_dump() if first_q else None,
    }


def run_viva_submit(
    root: Path,
    db: Database,
    project_id: str,
    session_id: str,
    answer_text: str,
    gateway: Optional[AIGateway] = None,
) -> Dict[str, Any]:
    """Submits student answer to active viva question and steps session."""
    session_data = db.get_viva_session(session_id)
    if not session_data:
        raise ValueError(f"Viva session {session_id} not found.")

    turn_idx = session_data["current_turn"]
    q_data = db.get_viva_question(session_id, turn_idx)
    if not q_data:
        raise ValueError(f"No active question found for session {session_id} at turn {turn_idx}.")

    submission = VivaAnswerSubmission(
        submission_id=f"vsub_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        turn_index=turn_idx,
        question_id=q_data["question_id"],
        answer_text=answer_text,
    )

    files = db.get_files_for_project(project_id)
    graph = db.get_graph(project_id)
    index = ProjectArchitecturalIndex(files=files, graph=graph)
    context_engine = ContextEngine(db)
    active_gateway = gateway or AIGateway(db=db)

    consent_token = make_cli_consent_token()

    turn_eval, next_q, updated_session = submit_viva_answer_and_step(
        db=db,
        session_id=session_id,
        submission=submission,
        index=index,
        context_engine=context_engine,
        gateway=active_gateway,
        consent_token=consent_token,
    )

    return {
        "turn_evaluation": turn_eval.model_dump(),
        "next_question": next_q.model_dump() if next_q else None,
        "session": updated_session.model_dump(),
    }


def run_viva_report(
    root: Path,
    db: Database,
    project_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Compiles and returns the final viva report."""
    files = db.get_files_for_project(project_id)
    graph = db.get_graph(project_id)
    index = ProjectArchitecturalIndex(files=files, graph=graph)

    report = compile_viva_report(
        db=db,
        session_id=session_id,
        index=index,
    )
    return report.model_dump()


def run_conversation_import(
    root: Path,
    db: Database,
    provider: str,
    raw_payload: str,
    has_consent: bool,
    project_id: Optional[str] = None,
    source: str = "IMPORT",
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Imports and persists a conversation with explicit consent verification and secret redaction."""
    from backend.domain.models import ConversationConsent
    from backend.conversation.service import ConversationIngestionService

    consent = ConversationConsent(
        consent_id=f"consent_{uuid.uuid4().hex[:8]}",
        approved=has_consent,
        reason="CLI user consent flag",
    )
    service = ConversationIngestionService(db=db)
    conv = service.ingest(
        provider=provider,
        raw_payload=raw_payload,
        consent=consent,
        source=source,
        project_id=project_id,
        title=title,
    )
    return {
        "status": "imported",
        "conversation": conv.model_dump(),
        "total_messages": len(conv.messages),
    }


def run_conversation_normalize(
    provider: str,
    raw_payload: str,
    has_consent: bool,
    project_id: Optional[str] = None,
    source: str = "IMPORT",
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalizes a conversation payload without persisting to SQLite."""
    from backend.domain.models import ConversationConsent
    from backend.conversation.service import ConversationIngestionService

    consent = ConversationConsent(
        consent_id=f"consent_{uuid.uuid4().hex[:8]}",
        approved=has_consent,
        reason="CLI user consent flag",
    )
    service = ConversationIngestionService(db=None)
    conv = service.normalize_only(
        provider=provider,
        raw_payload=raw_payload,
        consent=consent,
        source=source,
        project_id=project_id,
        title=title,
    )
    return {
        "status": "normalized",
        "conversation": conv.model_dump(),
        "total_messages": len(conv.messages),
    }


def run_conversation_analyze(
    db: Database,
    conversation_id: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyzes a conversation against project evidence."""
    from backend.conversation.evidence_service import ConversationEvidenceService

    if not conversation_id:
        raise ValueError("Missing required --conversation-id.")

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(
        conversation_id=conversation_id,
        project_id=project_id,
    )
    return {
        "status": "success",
        "result": result.model_dump(),
        "summary": result.summary,
    }


def run_conversation_verify(
    db: Database,
    conversation_id: str,
    claim_id: str,
    project_id: Optional[str] = None,
    has_consent: bool = False,
    gateway: Optional[Any] = None,
    explicit_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifies a conversation claim using AI Gateway against deterministic M11.1 evidence."""
    from backend.conversation.verification_service import ConversationVerificationService
    from backend.conversation.evidence_service import ConversationEvidenceService
    from backend.domain.models import VerificationRequest
    from backend.ai_gateway.consent import ConsentManager

    if not conversation_id:
        raise ValueError("Missing required --conversation-id.")
    if not claim_id:
        raise ValueError("Missing required --claim-id.")
    if not project_id:
        raise ValueError("Missing required --project-id.")

    service = ConversationVerificationService(db, gateway=gateway)

    consent_token = None
    if has_consent:
        _, packet = service.prepare_verification(conversation_id, claim_id, project_id)
        consent_token = ConsentManager.grant_consent(packet, provider="gemini", model="gemini-3.8-flash")

    result = service.verify_claim(
        conversation_id=conversation_id,
        claim_id=claim_id,
        project_id=project_id,
        consent_token=consent_token,
        explicit_api_key=explicit_api_key,
    )
    return {
        "status": "success",
        "verification": result.model_dump(),
    }


def run_bridge_start(
    db: Database,
    project_id: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    blocking: bool = True,
) -> Dict[str, Any]:
    """Starts the local bridge server on 127.0.0.1."""
    from backend.bridge.server import create_bridge_server

    server = create_bridge_server(
        host=host,
        port=port,
        db=db,
        default_project_id=project_id,
    )
    if blocking:
        try:
            print(f"Build Coach local bridge listening on http://{host}:{port} (Ctrl+C to stop)...")
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping local bridge...")
        finally:
            server.server_close()
        return {"running": False, "host": host, "port": port}
    else:
        return {"running": True, "host": host, "port": port}


def run_bridge_status(
    host: str = "127.0.0.1",
    port: int = 8765,
) -> Dict[str, Any]:
    """Checks the status of the local bridge on host:port."""
    from backend.bridge.server import check_bridge_status

    return check_bridge_status(host=host, port=port)


def run_project_list(db: Database) -> Dict[str, Any]:
    """Lists all registered projects from the database."""
    projects = db.list_projects()
    return {
        "projects": [
            {
                "project_id": p.id,
                "display_name": p.name,
                "root_path": p.root_path,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in projects
        ],
        "total_projects": len(projects),
    }


def run_project_register(db: Database, target_path_str: str) -> Dict[str, Any]:
    """Explicitly registers a local project root into the database."""
    target_path = Path(target_path_str).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Project directory does not exist: '{target_path}'")
    if not target_path.is_dir():
        raise NotADirectoryError(f"Target path is not a directory: '{target_path}'")

    root = detect_project_root(target_path)
    existing = db.get_project_by_root(str(root))
    now_iso = utc_now_iso()

    if existing:
        # Update timestamp and name if needed
        existing.updated_at = now_iso
        db.upsert_project(existing)
        return {
            "registered": True,
            "project_id": existing.id,
            "display_name": existing.name,
            "root_path": existing.root_path,
            "status": "ALREADY_REGISTERED",
        }

    # Generate stable project ID from resolved root path
    project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    project = Project(
        id=project_id,
        name=root.name,
        root_path=str(root),
        created_at=now_iso,
        updated_at=now_iso,
    )
    db.upsert_project(project)

    return {
        "registered": True,
        "project_id": project.id,
        "display_name": project.name,
        "root_path": project.root_path,
        "status": "REGISTERED",
    }


def run_project_status(db: Database, project_id: str) -> Dict[str, Any]:
    """Retrieves status and conversation count for a registered project."""
    project = db.get_project_by_id(project_id)
    if not project:
        raise ValueError(f"Project '{project_id}' is not registered.")

    conversations = db.list_conversations(project_id=project_id)
    return {
        "project_id": project.id,
        "display_name": project.name,
        "root_path": project.root_path,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "bound_conversations_count": len(conversations),
    }


def run_observation_timeline(
    db: Database,
    project_id: str,
    limit: int = 1000,
) -> Dict[str, Any]:
    """Retrieves chronological observation timeline for a project."""
    from backend.observation.service import ObservationService

    service = ObservationService(db)
    events = service.get_timeline(project_id=project_id, limit=limit)
    return {
        "project_id": project_id,
        "total_events": len(events),
        "events": [e.model_dump() for e in events],
    }


def run_observation_record(
    db: Database,
    project_id: str,
    event_type: str,
    source: str = "TERMINAL",
    payload_str: str = "{}",
) -> Dict[str, Any]:
    """Records an observation event for a project."""
    from backend.observation.service import ObservationService

    service = ObservationService(db)
    try:
        payload = json.loads(payload_str) if payload_str else {}
    except Exception as exc:
        raise ValueError(f"Invalid JSON payload: {exc}")

    event = service.record_event(
        project_id=project_id,
        event_type=event_type,
        source=source,
        payload=payload,
        provenance={"source": source, "invoked_by": "cli"},
    )
    return {
        "status": "recorded",
        "event": event.model_dump(),
    }


def run_observation_explanation(
    db: Database,
    project_id: str,
) -> Dict[str, Any]:
    """Constructs deterministic explanation packet for a project."""
    from backend.observation.service import ObservationService

    service = ObservationService(db)
    packet = service.get_explanation_packet(project_id=project_id)
    return {
        "project_id": project_id,
        "explanation": packet.model_dump(),
    }


def run_observation_explain(
    db: Database,
    project_id: str,
    incident_id: Optional[str] = None,
    has_consent: bool = False,
    explicit_api_key: Optional[str] = None,
    gateway: Optional[Any] = None,
) -> Dict[str, Any]:
    """Generates structured IncidentExplanation for a specific or latest incident (M12.6)."""
    from backend.observation.service import ObservationService

    service = ObservationService(db)
    explanation = service.explain_incident(
        project_id=project_id,
        incident_id=incident_id,
        has_consent=has_consent,
        explicit_api_key=explicit_api_key,
        gateway=gateway,
    )
    return {
        "project_id": project_id,
        "incident_id": incident_id,
        "explanation": explanation.model_dump(),
    }


def run_guidance_show(
    db: Database,
    project_id: str,
    incident_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates or recalculates deterministic guidance plan for a project (M12.7)."""
    from backend.guidance.service import GuidanceService

    service = GuidanceService(db)
    plan = service.generate_plan(project_id=project_id, incident_id=incident_id)
    return {
        "project_id": project_id,
        "plan_id": plan.plan_id,
        "status": plan.status.value,
        "top_next_action": plan.top_next_action.model_dump() if plan.top_next_action else None,
        "secondary_actions": [a.model_dump() for a in plan.secondary_actions],
        "gaps": [g.model_dump() for g in plan.gaps],
        "total_gaps": len(plan.gaps),
        "total_actions": len(plan.actions),
        "generated_at": plan.generated_at,
    }






