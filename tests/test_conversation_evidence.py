"""Comprehensive tests for Milestone 11.1: Conversation ↔ Project Evidence & Schema v9."""

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    ConversationConsent,
    ConversationClaim,
    EvidenceLink,
    ClaimStatus,
    EvidenceRelation,
    Project,
    ProjectFile,
    GitState,
    ProjectGraph,
    GraphNode,
    GraphEdge,
    ProvenanceRecord,
    ChangeSet,
    FileChange,
    ChangeType,
    EvidenceRecord,
)
from backend.project_model.db import Database
from backend.project_model.migrations import (
    apply_migrations,
    get_current_schema_version,
    ensure_conversation_tables,
    MIGRATIONS,
    migration_v8,
    migration_v9,
)
from backend.conversation.evidence_service import ConversationEvidenceService
from backend.cli.main import main


# ---------------------------------------------------------------------------
# PART 1: SCHEMA V9 TESTS
# ---------------------------------------------------------------------------

def test_schema_migration_v8_to_v9_clean(tmp_path: Path):
    """Verifies that a clean database at v8 properly upgrades to v9 with all tables and indices."""
    db_file = tmp_path / "clean_v8.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply migrations 1 through 8
    from backend.project_model.migrations import ensure_migration_table
    ensure_migration_table(conn)
    for ver, desc, func in MIGRATIONS[:8]:
        with conn:
            func(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), ?)",
                (ver, desc),
            )

    cursor = conn.cursor()
    cursor.execute("SELECT MAX(version) FROM schema_migrations")
    assert cursor.fetchone()[0] == 8

    # Apply migrations with v9
    applied = apply_migrations(conn)
    assert applied == [9]

    # Verify schema version is 9
    assert get_current_schema_version(conn) == 9

    # Verify tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('conversations', 'conversation_messages', 'conversation_consents')")
    found_tables = {row[0] for row in cursor.fetchall()}
    assert found_tables == {"conversations", "conversation_messages", "conversation_consents"}
    conn.close()


def test_schema_migration_from_m11_0_shadow_tables(tmp_path: Path):
    """Verifies that upgrading a database with M11.0 shadow-tables preserves existing conversation rows."""
    db_file = tmp_path / "shadow_v8.db"
    conn = sqlite3.connect(str(db_file))

    # Apply v1..v8
    from backend.project_model.migrations import ensure_migration_table
    ensure_migration_table(conn)
    for ver, desc, func in MIGRATIONS[:8]:
        with conn:
            func(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), ?)",
                (ver, desc),
            )

    # Simulate M11.0 shadow-schema state: tables created, but version remains 8
    ensure_conversation_tables(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(version) FROM schema_migrations")
    assert cursor.fetchone()[0] == 8

    # Insert test data into shadow tables
    conn.execute(
        """
        INSERT INTO conversations (conversation_id, provider, source, project_id, title, created_at, updated_at, metadata_json)
        VALUES ('conv_shadow_1', 'CHATGPT', 'IMPORT', 'proj_1', 'Shadow Test', '2024-01-01T00:00:00', '2024-01-01T00:00:00', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO conversation_messages (message_id, conversation_id, role, content, sequence, metadata_json)
        VALUES ('msg_shadow_1', 'conv_shadow_1', 'USER', 'Hello shadow world', 1, '{}')
        """
    )
    conn.commit()

    # Now run apply_migrations to formalize v9
    applied = apply_migrations(conn)
    assert 9 in applied
    assert get_current_schema_version(conn) == 9

    # Verify existing data is preserved and not duplicated or dropped
    cursor.execute("SELECT conversation_id, title FROM conversations WHERE conversation_id = 'conv_shadow_1'")
    row = cursor.fetchone()
    assert row is not None
    assert row[1] == "Shadow Test"

    cursor.execute("SELECT message_id, content FROM conversation_messages WHERE conversation_id = 'conv_shadow_1'")
    msg_row = cursor.fetchone()
    assert msg_row is not None
    assert msg_row[1] == "Hello shadow world"
    conn.close()


def test_schema_migration_v9_idempotency(tmp_path: Path):
    """Verifies that calling apply_migrations repeatedly on a v9 database is a safe no-op."""
    db_file = tmp_path / "idempotent.db"
    db = Database(db_file)
    assert db.get_schema_version() == 9

    conn = db.get_connection()
    applied = apply_migrations(conn)
    assert applied == []
    assert get_current_schema_version(conn) == 9
    conn.close()


# ---------------------------------------------------------------------------
# FIXTURES: Project with Files, Graph, Changeset, and Evidence Records
# ---------------------------------------------------------------------------

@pytest.fixture
def project_with_evidence(tmp_path: Path):
    """Creates a realistic project with files, graph nodes, changeset and evidence records."""
    proj_dir = tmp_path / "evidence_project"
    proj_dir.mkdir()

    # Create physical files
    auth_file = proj_dir / "auth.py"
    auth_file.write_text("def authenticate(): pass\n", encoding="utf-8")

    middleware_file = proj_dir / "middleware.py"
    middleware_file.write_text("def log_request(): pass\n", encoding="utf-8")

    test_auth_file = proj_dir / "test_auth.py"
    test_auth_file.write_text("def test_auth(): pass\n", encoding="utf-8")

    db_path = proj_dir / ".buildcoach" / "state.db"
    db = Database(db_path)

    project = Project(
        id="proj_evidence_test",
        name="evidence_project",
        root_path=str(proj_dir.resolve()),
    )
    db.upsert_project(project)

    # Sync files
    files = [
        ProjectFile(
            path="auth.py",
            absolute_path=str(auth_file),
            file_size=auth_file.stat().st_size,
            last_modified=auth_file.stat().st_mtime,
            sha256_hash="hash_auth",
            file_type="python",
        ),
        ProjectFile(
            path="middleware.py",
            absolute_path=str(middleware_file),
            file_size=middleware_file.stat().st_size,
            last_modified=middleware_file.stat().st_mtime,
            sha256_hash="hash_middleware",
            file_type="python",
        ),
        ProjectFile(
            path="test_auth.py",
            absolute_path=str(test_auth_file),
            file_size=test_auth_file.stat().st_size,
            last_modified=test_auth_file.stat().st_mtime,
            sha256_hash="hash_test_auth",
            file_type="python",
        ),
    ]
    db.sync_files(project.id, files)

    # Build ProjectGraph
    graph = ProjectGraph(project_id=project.id)
    node_auth = GraphNode(id="file:auth.py", project_id=project.id, node_type="FILE", name="auth.py", path="auth.py")
    node_mid = GraphNode(id="file:middleware.py", project_id=project.id, node_type="FILE", name="middleware.py", path="middleware.py")
    node_test = GraphNode(id="file:test_auth.py", project_id=project.id, node_type="FILE", name="test_auth.py", path="test_auth.py")
    graph.add_node(node_auth)
    graph.add_node(node_mid)
    graph.add_node(node_test)

    # Edge: middleware imports auth
    edge = GraphEdge(
        id="edge_mid_auth",
        project_id=project.id,
        source_node_id=node_mid.id,
        target_node_id=node_auth.id,
        edge_type="IMPORTS",
        evidence=ProvenanceRecord(source_file="middleware.py", line_number=1, source_type="ast", confidence="HIGH"),
    )
    graph.add_edge(edge)
    db.save_graph(graph)

    # Build ChangeSet: auth.py changed, deleted_old.py deleted, middleware.py unchanged
    change_set = ChangeSet(
        id="cs_evidence_1",
        project_id=project.id,
        git_state=GitState(is_git_repo=True, is_dirty=True, modified_count=1),
        file_changes=[
            FileChange(
                id="fc_auth",
                change_set_id="cs_evidence_1",
                new_path="auth.py",
                change_type=ChangeType.MODIFIED,
                is_staged=False,
            ),
            FileChange(
                id="fc_deleted",
                change_set_id="cs_evidence_1",
                old_path="deleted_old.py",
                new_path="deleted_old.py",
                change_type=ChangeType.DELETED,
                is_staged=False,
            ),
        ],
        evidence=[
            EvidenceRecord(
                id="ev_git_auth",
                project_id=project.id,
                change_set_id="cs_evidence_1",
                evidence_type="GIT_DIFF",
                source="git diff auth.py",
                file_path="auth.py",
                observation="Modified authentication handler",
                confidence="HIGH",
            )
        ],
    )
    db.save_change_set(change_set)

    return {
        "proj_dir": proj_dir,
        "db": db,
        "project": project,
    }


# ---------------------------------------------------------------------------
# PART 2: CONVERSATION EVIDENCE TESTS
# ---------------------------------------------------------------------------

def test_evidence_referenced_changed_file_supported(project_with_evidence):
    """Validates that referencing a file that exists and has matching change/git evidence yields SUPPORTED/PARTIAL."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_1",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_1",
                role=ConversationRole.USER,
                content="Please update auth.py",
                sequence=1,
            ),
            ConversationMessage(
                message_id="msg_2",
                role=ConversationRole.ASSISTANT,
                content="I have updated `auth.py` and modified token validation.",
                sequence=2,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    assert result.conversation_id == conv.conversation_id
    assert result.project_id == project.id
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert "auth.py" in claim.referenced_paths
    # Since test_auth.py exists and was unchanged, our conservative grounding flags partial support
    assert claim.status in (ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED)

    # Check evidence links
    evidence_types = {link.evidence_type for link in result.evidence_links}
    assert "FILE_EXISTENCE" in evidence_types
    assert "GRAPH_NODE" in evidence_types
    assert "FILE_CHANGE" in evidence_types
    assert "GIT_EVIDENCE" in evidence_types


def test_evidence_referenced_nonexistent_file(project_with_evidence):
    """Validates that referencing a nonexistent file results in UNSUPPORTED claim with MISSING_FILE link."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_missing",
        provider="CLAUDE",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_missing_1",
                role=ConversationRole.ASSISTANT,
                content="I added a new helper in `nonexistent/helper.py` to manage cookies.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.status == ClaimStatus.UNSUPPORTED
    assert any(link.evidence_type == "MISSING_FILE" and link.relation == EvidenceRelation.NO_EVIDENCE for link in result.evidence_links)


def test_evidence_multiple_referenced_files(project_with_evidence):
    """Validates that referencing both an existing changed file and a missing file produces PARTIALLY_SUPPORTED."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_multi",
        provider="GEMINI",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_multi_1",
                role=ConversationRole.ASSISTANT,
                content="I modified `auth.py` and created `missing_config.json`.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    claim = result.claims[0]
    assert claim.status == ClaimStatus.PARTIALLY_SUPPORTED
    assert len(claim.referenced_paths) == 2


def test_evidence_path_outside_project(project_with_evidence):
    """Validates that path traversal or paths outside project root are detected as UNSUPPORTED and CONTRADICTS."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_outside",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_out_1",
                role=ConversationRole.ASSISTANT,
                content="I stored the backup at `../../outside/secret.py`.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    claim = result.claims[0]
    assert claim.status == ClaimStatus.UNSUPPORTED
    assert any(link.evidence_type == "OUTSIDE_PROJECT" and link.relation == EvidenceRelation.CONTRADICTS for link in result.evidence_links)


def test_evidence_unchanged_file(project_with_evidence):
    """Validates that referencing an existing but unchanged file records FILE_STATUS partially supports."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_unchanged",
        provider="CLAUDE",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_unchanged_1",
                role=ConversationRole.ASSISTANT,
                content="I inspected `middleware.py` and verified it works.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    claim = result.claims[0]
    assert claim.status == ClaimStatus.PARTIALLY_SUPPORTED
    assert any(link.evidence_type == "FILE_STATUS" for link in result.evidence_links)


def test_evidence_deleted_file(project_with_evidence):
    """Validates that claiming to touch a deleted file yields UNSUPPORTED with CONTRADICTS."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_deleted",
        provider="GEMINI",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_del_1",
                role=ConversationRole.ASSISTANT,
                content="I modified `deleted_old.py` to fix formatting.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    claim = result.claims[0]
    assert claim.status == ClaimStatus.UNSUPPORTED
    assert any(link.evidence_type == "FILE_CHANGE" and link.relation == EvidenceRelation.CONTRADICTS for link in result.evidence_links)


def test_evidence_unknown_general_claim(project_with_evidence):
    """Validates that general claims without extractable file/fact references evaluate to UNKNOWN."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_unknown",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_unk_1",
                role=ConversationRole.ASSISTANT,
                content="I improved code performance and optimized internal loops.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.status == ClaimStatus.UNKNOWN
    assert claim.referenced_paths == []
    assert any(link.relation == EvidenceRelation.NO_EVIDENCE for link in result.evidence_links)


def test_deterministic_repeated_analysis(project_with_evidence):
    """Verifies that running evidence analysis repeatedly on the same inputs produces identical IDs and statuses."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    conv = Conversation(
        conversation_id="conv_test_determ",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_det_1",
                role=ConversationRole.ASSISTANT,
                content="I updated `auth.py` and `middleware.py`.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    res1 = service.analyze_conversation(conv.conversation_id, project.id)
    res2 = service.analyze_conversation(conv.conversation_id, project.id)

    assert [c.claim_id for c in res1.claims] == [c.claim_id for c in res2.claims]
    assert [l.link_id for l in res1.evidence_links] == [l.link_id for l in res2.evidence_links]
    assert res1.summary == res2.summary


# ---------------------------------------------------------------------------
# PART 3: SECURITY TESTS
# ---------------------------------------------------------------------------

def test_security_untrusted_content_no_execution(project_with_evidence):
    """Verifies that assistant conversation content is strictly handled as passive data and never executed."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    dangerous_payload = "__import__('os').system('echo MALICIOUS_CODE_EXECUTED')"
    conv = Conversation(
        conversation_id="conv_security_1",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_sec_1",
                role=ConversationRole.ASSISTANT,
                content=f"Here is code in `auth.py`: {dangerous_payload}",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)
    # Analysis must succeed deterministically without executing the payload
    assert len(result.claims) == 1
    assert "auth.py" in result.claims[0].referenced_paths


def test_security_secret_redaction(project_with_evidence):
    """Verifies that raw secrets present in conversation claims are redacted before analysis."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]

    raw_secret = "sk-proj-123456789012345678901234567890"
    conv = Conversation(
        conversation_id="conv_secret_1",
        provider="CLAUDE",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_sec_secret",
                role=ConversationRole.ASSISTANT,
                content=f"I configured `auth.py` with secret key {raw_secret}.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    service = ConversationEvidenceService(db)
    result = service.analyze_conversation(conv.conversation_id, project.id)

    claim = result.claims[0]
    assert raw_secret not in claim.claim_text
    assert "[REDACTED]" in claim.claim_text


# ---------------------------------------------------------------------------
# PART 4: CLI TESTS
# ---------------------------------------------------------------------------

def test_cli_conversation_analyze_json_success(project_with_evidence):
    """Verifies that 'ai-build-coach conversation analyze --json' outputs valid structured JSON."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]
    proj_dir = project_with_evidence["proj_dir"]

    conv = Conversation(
        conversation_id="conv_cli_success",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_cli_1",
                role=ConversationRole.ASSISTANT,
                content="I updated `auth.py`.",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "conversation", "analyze",
            "--conversation-id", conv.conversation_id,
            "--project-id", project.id,
            "--json",
        ])

    assert rc == 0
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)
    assert data["status"] == "success"
    assert data["command"] == "conversation"
    assert data["action"] == "analyze"
    assert "result" in data["data"]
    assert data["data"]["summary"]["total_claims"] == 1


def test_cli_conversation_analyze_missing_conversation(project_with_evidence):
    """Verifies that requesting an analysis for a nonexistent conversation returns an error envelope."""
    proj_dir = project_with_evidence["proj_dir"]
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "conversation", "analyze",
            "--conversation-id", "non_existent_conv_id",
            "--json",
        ])

    assert rc != 0
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)
    assert data["status"] == "error"
    assert data["error"]["code"] == "CONVERSATION_ANALYZE_ERROR"
    assert "Conversation not found" in data["error"]["message"]


def test_cli_conversation_analyze_missing_project(project_with_evidence):
    """Verifies that specifying a nonexistent project returns a clean JSON error envelope."""
    db = project_with_evidence["db"]
    project = project_with_evidence["project"]
    proj_dir = project_with_evidence["proj_dir"]

    conv = Conversation(
        conversation_id="conv_cli_missing_proj",
        provider="CHATGPT",
        project_id=project.id,
        messages=[
            ConversationMessage(
                message_id="msg_1",
                role=ConversationRole.ASSISTANT,
                content="Testing missing project",
                sequence=1,
            ),
        ],
    )
    db.save_conversation(conv)

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main([
            "--project-root", str(proj_dir),
            "conversation", "analyze",
            "--conversation-id", conv.conversation_id,
            "--project-id", "non_existent_project_id",
            "--json",
        ])

    assert rc != 0
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)
    assert data["status"] == "error"
    assert "Project not found" in data["error"]["message"]
