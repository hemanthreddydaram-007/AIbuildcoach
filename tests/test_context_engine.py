"""Tests for Milestone 4 - Deterministic Context Engine."""

import pytest
import sqlite3
from pathlib import Path
from backend.domain.models import (
    Project,
    ProjectFile,
    GitState,
    ProjectGraph,
    GraphNode,
    GraphEdge,
    ProvenanceRecord,
    NodeType,
    EdgeType,
    ChangeSet,
    FileChange,
    DiffHunk,
    EvidenceRecord,
    ChangeType,
    ContextRequest,
    ContextPacket,
    ContextItem,
    ContextPurpose,
    ContextSourceType,
)
from backend.project_model.db import Database
from backend.context_engine.engine import ContextEngine, compute_packet_id, compute_cache_key
from backend.context_engine.secrets import detect_and_redact, SecretCategory
from backend.context_engine.relevance import (
    extract_relevance_candidates,
    is_test_file,
    is_related_test,
    SCORE_DIRECTLY_CHANGED_FILE,
    SCORE_CHANGED_DIFF_HUNK,
    SCORE_EXPLICITLY_REQUESTED,
    SCORE_DIRECT_DEPENDENCY,
    SCORE_DIRECT_DEPENDENT,
    SCORE_RELATED_TEST,
    SCORE_RELATED_CONFIG,
    SCORE_CHANGE_EVIDENCE,
)
from backend.context_engine.compression import (
    estimate_tokens,
    normalize_whitespace,
    compress_item_content,
    deduplicate_and_compress,
    apply_budget,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_buildcoach.db"
    db = Database(db_file)
    project = Project(id="proj_1", name="TestProject", root_path=str(tmp_path))
    db.upsert_project(project)
    return db


def create_sample_changeset(project_id: str = "proj_1") -> ChangeSet:
    hunk = DiffHunk(
        id="hunk_1",
        file_change_id="fc_1",
        old_start=10,
        old_lines=5,
        new_start=10,
        new_lines=8,
        header="@@ -10,5 +10,8 @@ def authenticate():",
        content="@@ -10,5 +10,8 @@\n+    token = request.headers.get('Authorization')\n+    return verify_token(token)",
    )
    fc = FileChange(
        id="fc_1",
        change_set_id="cs_1",
        old_path="auth.py",
        new_path="auth.py",
        change_type=ChangeType.MODIFIED,
        is_staged=False,
        is_untracked=False,
        line_ranges=[(10, 18)],
        hunks=[hunk],
    )
    ev = EvidenceRecord(
        id="ev_1",
        project_id=project_id,
        change_set_id="cs_1",
        evidence_type="GIT_STATUS",
        source="git status --porcelain",
        file_path="auth.py",
        observation="M auth.py",
        confidence="HIGH",
    )
    return ChangeSet(
        id="cs_1",
        project_id=project_id,
        git_state=GitState(is_git_repo=True, is_dirty=True, modified_count=1),
        file_changes=[fc],
        evidence=[ev],
        summary={"modified": 1},
    )


# 1. Empty project context
def test_1_empty_project_context(temp_db):
    engine = ContextEngine(db=temp_db)
    req = ContextRequest(project_id="proj_1", purpose=ContextPurpose.PROJECT_OVERVIEW)
    packet = engine.build_context_packet(req)

    assert packet.project_id == "proj_1"
    assert packet.purpose == ContextPurpose.PROJECT_OVERVIEW
    assert packet.items == []
    assert packet.truncation_status == "NONE"
    assert packet.token_estimate == 0


# 2. Clean working tree
def test_2_clean_working_tree(temp_db):
    engine = ContextEngine(db=temp_db)
    ev = EvidenceRecord(
        id="ev_clean",
        project_id="proj_1",
        evidence_type="GIT_STATUS",
        source="git status --porcelain",
        file_path=None,
        observation="working tree clean",
        confidence="HIGH",
    )
    clean_cs = ChangeSet(
        id="cs_clean",
        project_id="proj_1",
        git_state=GitState(is_git_repo=True, is_dirty=False),
        file_changes=[],
        evidence=[ev],
    )
    req = ContextRequest(project_id="proj_1", change_set=clean_cs)
    packet = engine.build_context_packet(req)

    assert len(packet.items) == 1
    assert packet.items[0].source_type == ContextSourceType.EVIDENCE
    assert "clean" in packet.items[0].content


# 3. Project with current changes
def test_3_project_with_current_changes(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "def authenticate():\n    return True\n"}
    packet = engine.build_context_packet(req, project_files_content=files)

    source_types = [item.source_type for item in packet.items]
    assert ContextSourceType.CHANGESET in source_types
    assert ContextSourceType.DIFF in source_types
    assert ContextSourceType.EVIDENCE in source_types


# 4. Changed file ranking
def test_4_changed_file_ranking():
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    candidates = extract_relevance_candidates(req, {"auth.py": "content"})
    
    file_candidates = [c for c in candidates if c[1]["source_type"] == ContextSourceType.CHANGESET]
    hunk_candidates = [c for c in candidates if c[1]["source_type"] == ContextSourceType.DIFF]
    
    assert file_candidates[0][0] == SCORE_DIRECTLY_CHANGED_FILE
    assert hunk_candidates[0][0] == SCORE_CHANGED_DIFF_HUNK


# 5. Direct dependency ranking
def test_5_direct_dependency_ranking():
    cs = create_sample_changeset()
    graph = ProjectGraph(project_id="proj_1")
    graph.add_node(GraphNode(id="file:auth.py", project_id="proj_1", node_type=NodeType.FILE, name="auth.py", path="auth.py"))
    graph.add_node(GraphNode(id="file:middleware.py", project_id="proj_1", node_type=NodeType.FILE, name="middleware.py", path="middleware.py"))
    graph.add_edge(GraphEdge(
        id="edge_1",
        project_id="proj_1",
        source_node_id="file:auth.py",
        target_node_id="file:middleware.py",
        edge_type=EdgeType.IMPORTS,
        evidence=ProvenanceRecord(source_file="auth.py", line_number=2, raw_statement="import middleware"),
    ))

    req = ContextRequest(project_id="proj_1", change_set=cs, graph=graph)
    files = {"auth.py": "import middleware", "middleware.py": "def check(): pass"}
    candidates = extract_relevance_candidates(req, files)

    dep_candidates = [c for c in candidates if c[1]["file_path"] == "middleware.py"]
    assert len(dep_candidates) == 1
    assert dep_candidates[0][0] == SCORE_DIRECT_DEPENDENCY
    assert dep_candidates[0][1]["source_type"] == ContextSourceType.PROJECT_GRAPH


# 6. Direct dependent ranking
def test_6_direct_dependent_ranking():
    cs = create_sample_changeset()
    graph = ProjectGraph(project_id="proj_1")
    graph.add_node(GraphNode(id="file:auth.py", project_id="proj_1", node_type=NodeType.FILE, name="auth.py", path="auth.py"))
    graph.add_node(GraphNode(id="file:server.py", project_id="proj_1", node_type=NodeType.FILE, name="server.py", path="server.py"))
    graph.add_edge(GraphEdge(
        id="edge_2",
        project_id="proj_1",
        source_node_id="file:server.py",
        target_node_id="file:auth.py",
        edge_type=EdgeType.IMPORTS,
        evidence=ProvenanceRecord(source_file="server.py", line_number=1, raw_statement="import auth"),
    ))

    req = ContextRequest(project_id="proj_1", change_set=cs, graph=graph)
    files = {"auth.py": "content", "server.py": "import auth"}
    candidates = extract_relevance_candidates(req, files)

    dependent_candidates = [c for c in candidates if c[1]["file_path"] == "server.py"]
    assert len(dependent_candidates) == 1
    assert dependent_candidates[0][0] == SCORE_DIRECT_DEPENDENT


# 7. Unrelated file exclusion
def test_7_unrelated_file_exclusion():
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {
        "auth.py": "def auth(): pass",
        "unrelated.py": "def unrelated(): pass",
        "billing/calculator.py": "def calc(): pass",
    }
    candidates = extract_relevance_candidates(req, files)
    candidate_paths = {c[1].get("file_path") for c in candidates}

    assert "unrelated.py" not in candidate_paths
    assert "billing/calculator.py" not in candidate_paths
    assert "auth.py" in candidate_paths


# 8. Relevant test selection
def test_8_relevant_test_selection():
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {
        "auth.py": "def auth(): pass",
        "tests/test_auth.py": "def test_auth(): pass",
        "tests/test_billing.py": "def test_billing(): pass",
    }
    candidates = extract_relevance_candidates(req, files)
    candidate_paths = {c[1].get("file_path") for c in candidates}

    assert "tests/test_auth.py" in candidate_paths
    assert "tests/test_billing.py" not in candidate_paths
    
    test_item = [c for c in candidates if c[1].get("file_path") == "tests/test_auth.py"][0]
    assert test_item[0] == SCORE_RELATED_TEST
    assert test_item[1]["source_type"] == ContextSourceType.TEST


# 9. Evidence selection
def test_9_evidence_selection():
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    candidates = extract_relevance_candidates(req, {"auth.py": "content"})
    
    ev_candidates = [c for c in candidates if c[1]["source_type"] == ContextSourceType.EVIDENCE]
    assert len(ev_candidates) == 1
    assert ev_candidates[0][0] == SCORE_CHANGE_EVIDENCE
    assert ev_candidates[0][1]["source_reference"] == "ev_1"


# 10. Duplicate context removal
def test_10_duplicate_context_removal():
    items = [
        ContextItem(
            item_id="i1",
            source_type=ContextSourceType.FILE,
            source_reference="ref1",
            file_path="a.py",
            relevance_reason="test",
            relevance_score=10.0,
            content="print('hello')",
        ),
        ContextItem(
            item_id="i2",
            source_type=ContextSourceType.FILE,
            source_reference="ref1",
            file_path="a.py",
            relevance_reason="test",
            relevance_score=10.0,
            content="print('hello')",
        ),
    ]
    deduped = deduplicate_and_compress(items)
    assert len(deduped) == 1


# 11. Deterministic ranking
def test_11_deterministic_ranking():
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "content", "tests/test_auth.py": "test"}

    c1 = extract_relevance_candidates(req, files)
    c2 = extract_relevance_candidates(req, files)

    assert [score for score, _ in c1] == [score for score, _ in c2]
    assert [item["source_reference"] for _, item in c1] == [item["source_reference"] for _, item in c2]


# 12. Deterministic ContextPacket ID
def test_12_deterministic_context_packet_id(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "content"}

    packet1 = engine.build_context_packet(req, files, use_cache=False)
    packet2 = engine.build_context_packet(req, files, use_cache=False)

    assert packet1.id == packet2.id


# 13. Deterministic item ordering
def test_13_deterministic_item_ordering(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "content", "tests/test_auth.py": "test"}

    packet = engine.build_context_packet(req, files, use_cache=False)
    types = [item.source_type for item in packet.items]

    # Verify canonical order: CHANGESET -> DIFF -> TEST -> EVIDENCE
    indices = {t: types.index(t) for t in set(types)}
    assert indices[ContextSourceType.CHANGESET] < indices[ContextSourceType.DIFF]
    assert indices[ContextSourceType.DIFF] < indices[ContextSourceType.TEST]
    assert indices[ContextSourceType.TEST] < indices[ContextSourceType.EVIDENCE]


# 14. Secret detection
def test_14_secret_detection():
    secret_text = "api_key = 'sk-1234567890abcdef1234567890abcdef'\nBearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz\nDB_PASS='super_secret_pw'"
    _, was_redacted, summary = detect_and_redact(secret_text)

    assert was_redacted is True
    assert summary["total_secrets_detected"] >= 3
    assert SecretCategory.API_KEY in summary["categories"]
    assert SecretCategory.BEARER_TOKEN in summary["categories"]


# 15. Secret redaction
def test_15_secret_redaction():
    text = "Authorization: Bearer mySecretToken123456789012345\nAPI_KEY=\"AIzaSyA1234567890abcdefghij1234567890\""
    redacted, was_redacted, summary = detect_and_redact(text)

    assert was_redacted is True
    assert "mySecretToken123456789012345" not in redacted
    assert "AIzaSyA1234567890abcdefghij1234567890" not in redacted
    assert "Authorization: Bearer [REDACTED]" in redacted
    assert 'API_KEY="[REDACTED]"' in redacted


# 16. Multiple secrets in one file
def test_16_multiple_secrets_in_one_file():
    text = """
    OPENAI_API_KEY = "sk-01234567890123456789012345678901"
    password = "top_secret_pass"
    AWS_KEY = "AKIA1234567890ABCDEF"
    """
    redacted, was_redacted, summary = detect_and_redact(text)

    assert was_redacted is True
    assert summary["total_secrets_detected"] == 3
    assert "sk-01234567890123456789012345678901" not in redacted
    assert "top_secret_pass" not in redacted
    assert "AKIA1234567890ABCDEF" not in redacted


# 17. Secret not persisted
def test_17_secret_not_persisted(temp_db):
    engine = ContextEngine(db=temp_db)
    secret_val = "sk-99999999999999999999999999999999"
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": f"SECRET_KEY = '{secret_val}'\n"}

    packet = engine.build_context_packet(req, files, use_cache=False)
    
    # 1. Check packet in memory
    for item in packet.items:
        assert secret_val not in item.content
    assert secret_val not in str(packet.redaction_summary)

    # 2. Check SQLite persistence
    conn = temp_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM context_items WHERE packet_id = ?", (packet.id,))
        rows = cursor.fetchall()
        for r in rows:
            assert secret_val not in r["content"]
    finally:
        conn.close()


# 18. Provenance preservation
def test_18_provenance_preservation(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "def auth(): pass"}

    packet = engine.build_context_packet(req, files, use_cache=False)
    for item in packet.items:
        assert item.source_type in [
            ContextSourceType.CHANGESET,
            ContextSourceType.DIFF,
            ContextSourceType.EVIDENCE,
        ]
        assert item.source_reference is not None
        assert item.relevance_reason is not None


# 19. Context budget within limit
def test_19_context_budget_within_limit():
    items = [
        ContextItem(
            item_id="i1",
            source_type=ContextSourceType.FILE,
            source_reference="ref1",
            relevance_reason="reason",
            relevance_score=50.0,
            content="short content",
        )
    ]
    budgeted, tokens, status = apply_budget(items, budget_tokens=1000)
    assert len(budgeted) == 1
    assert status == "NONE"


# 20. Context truncation behavior
def test_20_context_truncation_behavior():
    items = [
        ContextItem(
            item_id="critical",
            source_type=ContextSourceType.CHANGESET,
            source_reference="ref1",
            relevance_reason="critical file",
            relevance_score=100.0,
            content="A" * 40,  # ~10 tokens
        ),
        ContextItem(
            item_id="low_priority",
            source_type=ContextSourceType.FILE,
            source_reference="ref2",
            relevance_reason="supporting",
            relevance_score=40.0,
            content="B" * 200,  # ~50 tokens
        ),
    ]
    # Set budget to 20 tokens -> low_priority cannot fit
    budgeted, tokens, status = apply_budget(items, budget_tokens=20)
    assert len(budgeted) == 1
    assert budgeted[0].item_id == "critical"
    assert status == "TRUNCATED"


# 21. Critical-item preservation during truncation
def test_21_critical_item_preservation_during_truncation():
    critical_item = ContextItem(
        item_id="crit",
        source_type=ContextSourceType.CHANGESET,
        source_reference="fc_1",
        relevance_reason="directly changed",
        relevance_score=100.0,
        content="critical code change",
    )
    supporting_item = ContextItem(
        item_id="supp",
        source_type=ContextSourceType.FILE,
        source_reference="cfg_1",
        relevance_reason="config",
        relevance_score=40.0,
        content="configuration code",
    )
    budgeted, _, status = apply_budget([critical_item, supporting_item], budget_tokens=1)
    
    assert critical_item in budgeted
    assert supporting_item not in budgeted
    assert status == "TRUNCATED"


# 22. Compression behavior
def test_22_compression_behavior():
    raw_text = "def foo():   \n\n\n\n\n    pass   \n"
    normalized = normalize_whitespace(raw_text)
    
    assert "pass   " not in normalized
    assert "pass" in normalized
    assert "\n\n\n" not in normalized


# 23. Binary-file handling
def test_23_binary_file_handling():
    compressed, was_truncated = compress_item_content("binary\x00data", is_binary=True, file_size=1024)
    assert "[Binary file content omitted: 1024 bytes]" in compressed
    assert "\x00" not in compressed


# 24. Large-file handling
def test_24_large_file_handling():
    lines = [f"line {i}" for i in range(500)]
    large_content = "\n".join(lines)
    compressed, was_truncated = compress_item_content(large_content)

    assert was_truncated is True
    assert "lines pruned for budget" in compressed
    assert "line 0" in compressed
    assert "line 499" in compressed


# 25. Malformed input handling
def test_25_malformed_input_handling(temp_db):
    engine = ContextEngine(db=temp_db)
    # Empty paths, malformed targets, 0 budget
    req = ContextRequest(
        project_id="proj_1",
        target_files=["   ", "\\bad\\path//"],
        budget_tokens=0,
    )
    packet = engine.build_context_packet(req)
    assert packet.project_id == "proj_1"
    assert packet.truncation_status == "NONE"


# 26. Missing evidence handling
def test_26_missing_evidence_handling(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = ChangeSet(
        id="cs_no_ev",
        project_id="proj_1",
        git_state=GitState(is_git_repo=True),
        file_changes=[],
        evidence=[],
    )
    req = ContextRequest(project_id="proj_1", change_set=cs)
    packet = engine.build_context_packet(req)
    assert packet.items == []
    assert packet.evidence_refs == []


# 27. Missing Project Graph handling
def test_27_missing_project_graph_handling(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    # Explicitly graph=None
    req = ContextRequest(project_id="proj_1", change_set=cs, graph=None)
    files = {"auth.py": "def auth(): pass"}
    packet = engine.build_context_packet(req, files)

    assert packet.id is not None
    assert len(packet.items) > 0


# 28. Cache invalidation after ChangeSet changes
def test_28_cache_invalidation_after_changeset_changes(temp_db):
    engine = ContextEngine(db=temp_db)
    cs1 = create_sample_changeset()
    req1 = ContextRequest(project_id="proj_1", change_set=cs1)
    files = {"auth.py": "version 1"}
    
    packet1 = engine.build_context_packet(req1, files, use_cache=True)
    
    # Change changeset
    cs2 = create_sample_changeset()
    cs2.id = "cs_2"
    cs2.file_changes[0].change_set_id = "cs_2"
    cs2.file_changes[0].new_path = "auth_v2.py"
    req2 = ContextRequest(project_id="proj_1", change_set=cs2)
    files2 = {"auth_v2.py": "version 2"}

    packet2 = engine.build_context_packet(req2, files2, use_cache=True)

    assert packet1.id != packet2.id


# 29. Repeated ContextPacket generation
def test_29_repeated_context_packet_generation(temp_db):
    engine = ContextEngine(db=temp_db)
    cs = create_sample_changeset()
    req = ContextRequest(project_id="proj_1", change_set=cs)
    files = {"auth.py": "def auth(): pass"}

    packet_a = engine.build_context_packet(req, files, use_cache=False)
    packet_b = engine.build_context_packet(req, files, use_cache=False)

    assert packet_a.id == packet_b.id
    assert len(packet_a.items) == len(packet_b.items)
    for ia, ib in zip(packet_a.items, packet_b.items):
        assert ia.item_id == ib.item_id
        assert ia.content == ib.content


# 30. End-to-end pipeline fixture (auth.py -> middleware.py -> auth_test.py -> unrelated.py)
def test_30_end_to_end_pipeline_fixture(temp_db):
    """Verifies the complete pipeline:
    auth.py changed
        ↓
    middleware.py imported by auth.py
        ↓
    auth_test.py tests authentication
        ↓
    unrelated.py unrelated to current change
    
    Expected prioritization:
    1. auth.py
    2. middleware.py
    3. auth_test.py
    and exclude unrelated.py.
    """
    engine = ContextEngine(db=temp_db)

    # 1. ChangeSet for auth.py
    hunk = DiffHunk(
        id="hunk_auth",
        file_change_id="fc_auth",
        old_start=1,
        old_lines=3,
        new_start=1,
        new_lines=4,
        header="@@ -1,3 +1,4 @@",
        content="@@ -1,3 +1,4 @@\n import middleware\n+def authenticate(user):\n+    return middleware.validate(user)",
    )
    fc_auth = FileChange(
        id="fc_auth",
        change_set_id="cs_auth",
        new_path="auth.py",
        change_type=ChangeType.MODIFIED,
        hunks=[hunk],
    )
    ev_auth = EvidenceRecord(
        id="ev_auth",
        project_id="proj_1",
        change_set_id="cs_auth",
        evidence_type="GIT_STATUS",
        source="git status --porcelain",
        file_path="auth.py",
        observation="M auth.py",
        confidence="HIGH",
    )
    changeset = ChangeSet(
        id="cs_auth",
        project_id="proj_1",
        git_state=GitState(is_git_repo=True, is_dirty=True),
        file_changes=[fc_auth],
        evidence=[ev_auth],
    )

    # 2. Graph: auth.py -> IMPORTS -> middleware.py
    graph = ProjectGraph(project_id="proj_1")
    graph.add_node(GraphNode(id="file:auth.py", project_id="proj_1", node_type=NodeType.FILE, name="auth.py", path="auth.py"))
    graph.add_node(GraphNode(id="file:middleware.py", project_id="proj_1", node_type=NodeType.FILE, name="middleware.py", path="middleware.py"))
    graph.add_edge(GraphEdge(
        id="edge_auth_mw",
        project_id="proj_1",
        source_node_id="file:auth.py",
        target_node_id="file:middleware.py",
        edge_type=EdgeType.IMPORTS,
        evidence=ProvenanceRecord(source_file="auth.py", line_number=1, raw_statement="import middleware"),
    ))

    # 3. Project files
    files = {
        "auth.py": "import middleware\ndef authenticate(user):\n    return middleware.validate(user)\n",
        "middleware.py": "def validate(user):\n    return True\n",
        "tests/auth_test.py": "import auth\ndef test_authenticate():\n    assert auth.authenticate('admin')\n",
        "unrelated.py": "def unrelated():\n    return 42\n",
    }

    req = ContextRequest(
        project_id="proj_1",
        change_set=changeset,
        graph=graph,
        purpose=ContextPurpose.CHANGE_EXPLANATION,
    )

    packet = engine.build_context_packet(req, project_files_content=files, use_cache=False)

    included_paths = {item.file_path for item in packet.items if item.file_path}
    assert "auth.py" in included_paths
    assert "middleware.py" in included_paths
    assert "tests/auth_test.py" in included_paths
    assert "unrelated.py" not in included_paths

    # Verify order of files: auth.py first, then middleware.py / auth_test.py
    first_auth_idx = min(idx for idx, item in enumerate(packet.items) if item.file_path == "auth.py")
    middleware_idx = min(idx for idx, item in enumerate(packet.items) if item.file_path == "middleware.py")
    test_idx = min(idx for idx, item in enumerate(packet.items) if item.file_path == "tests/auth_test.py")

    assert first_auth_idx < middleware_idx
    assert first_auth_idx < test_idx

    # Verify provenance for every item
    for item in packet.items:
        assert item.source_type is not None
        assert item.source_reference is not None
        assert item.relevance_reason is not None
        assert item.relevance_score > 0
