"""Tests for SQLite database and schema migrations."""

import sqlite3
from pathlib import Path
from backend.domain.models import Project, ProjectFile, GitState
from backend.project_model.db import Database
from backend.project_model.migrations import apply_migrations, get_current_schema_version, MIGRATIONS


def test_schema_migrations_initialization(tmp_path: Path):
    db_file = tmp_path / "state.db"
    db = Database(db_file)
    
    # Version should equal latest migration in MIGRATIONS (version 2 in M2)
    assert db.get_schema_version() == len(MIGRATIONS)
    
    # Repeated migration application should be a no-op
    conn = db.get_connection()
    newly_applied = apply_migrations(conn)
    assert len(newly_applied) == 0
    conn.close()


def test_schema_migration_step(tmp_path: Path, monkeypatch):
    import backend.project_model.migrations as mig
    db_file = tmp_path / "state_migrate.db"
    db = Database(db_file)
    current_ver = len(mig.MIGRATIONS)
    assert db.get_schema_version() == current_ver

    # Simulate registering a next incremental migration
    next_ver = current_ver + 1
    def dummy_next(conn: sqlite3.Connection):
        conn.execute("CREATE TABLE test_next_step (id INTEGER PRIMARY KEY, name TEXT)")

    custom_migrations = list(mig.MIGRATIONS) + [(next_ver, f"Test migration v{next_ver}", dummy_next)]
    monkeypatch.setattr(mig, "MIGRATIONS", custom_migrations)

    conn = db.get_connection()
    applied = mig.apply_migrations(conn)
    assert applied == [next_ver]
    assert mig.get_current_schema_version(conn) == next_ver

    # Verify table created
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_next_step'")
    assert cursor.fetchone() is not None
    conn.close()


def test_project_and_file_crud_and_sync(tmp_path: Path):
    db_file = tmp_path / "state.db"
    db = Database(db_file)
    
    project = Project(
        id="proj_1",
        name="test_proj",
        root_path=str(tmp_path),
    )
    db.upsert_project(project)
    
    retrieved = db.get_project_by_id("proj_1")
    assert retrieved is not None
    assert retrieved.name == "test_proj"
    
    file1 = ProjectFile(
        path="src/main.py",
        absolute_path=str(tmp_path / "src" / "main.py"),
        file_size=100,
        last_modified=1700000000.0,
        sha256_hash="hash1",
        file_type=".py",
        is_binary=False,
        is_large=False,
        is_ignored=False,
    )
    file2 = ProjectFile(
        path="src/utils.py",
        absolute_path=str(tmp_path / "src" / "utils.py"),
        file_size=200,
        last_modified=1700000000.0,
        sha256_hash="hash2",
        file_type=".py",
        is_binary=False,
        is_large=False,
        is_ignored=False,
    )
    
    # Sync 2 files
    db.sync_files(project.id, [file1, file2])
    files = db.get_files_for_project(project.id)
    assert len(files) == 2
    assert {f.path for f in files} == {"src/main.py", "src/utils.py"}
    
    # File modification and removal of file2
    file1_modified = ProjectFile(
        path="src/main.py",
        absolute_path=str(tmp_path / "src" / "main.py"),
        file_size=150,
        last_modified=1700000500.0,
        sha256_hash="hash1_new",
        file_type=".py",
        is_binary=False,
        is_large=False,
        is_ignored=False,
    )
    file3 = ProjectFile(
        path="src/extra.py",
        absolute_path=str(tmp_path / "src" / "extra.py"),
        file_size=50,
        last_modified=1700000500.0,
        sha256_hash="hash3",
        file_type=".py",
        is_binary=False,
        is_large=False,
        is_ignored=False,
    )
    db.sync_files(project.id, [file1_modified, file3])
    
    updated_files = db.get_files_for_project(project.id)
    assert len(updated_files) == 2
    paths = {f.path for f in updated_files}
    assert paths == {"src/main.py", "src/extra.py"}
    assert "src/utils.py" not in paths
    
    main_f = next(f for f in updated_files if f.path == "src/main.py")
    assert main_f.sha256_hash == "hash1_new"
    assert main_f.file_size == 150


def test_git_state_recording(tmp_path: Path):
    db_file = tmp_path / "state.db"
    db = Database(db_file)
    
    project = Project(id="proj_1", name="test_proj", root_path=str(tmp_path))
    db.upsert_project(project)
    
    state1 = GitState(
        is_git_repo=True,
        current_branch="feature",
        head_commit="abcdef123456",
        is_dirty=True,
        untracked_count=2,
        modified_count=1,
        staged_count=0,
    )
    db.record_git_state(project.id, state1)
    
    latest = db.get_latest_git_state(project.id)
    assert latest is not None
    assert latest.is_git_repo is True
    assert latest.current_branch == "feature"
    assert latest.head_commit == "abcdef123456"
    assert latest.is_dirty is True
    assert latest.untracked_count == 2


def test_large_file_set_synchronization(tmp_path: Path):
    db_file = tmp_path / "state_large.db"
    db = Database(db_file)

    project = Project(id="large_proj", name="large_proj", root_path=str(tmp_path))
    db.upsert_project(project)

    # 1. Programmatically generate 2,500 file models
    initial_count = 2500
    files_v1 = [
        ProjectFile(
            path=f"src/module_{i}/file_{i}.py",
            absolute_path=str(tmp_path / f"src/module_{i}/file_{i}.py"),
            file_size=100 + i,
            last_modified=1700000000.0,
            sha256_hash=f"hash_{i}",
            file_type=".py",
            is_binary=False,
            is_large=False,
            is_ignored=False,
        )
        for i in range(initial_count)
    ]

    # Sync large file set
    db.sync_files(project.id, files_v1)
    stored_files = db.get_files_for_project(project.id)
    assert len(stored_files) == initial_count

    # 2. Modify: keep first 1,000 files, remove 1,500 files, add 200 new files
    files_v2 = files_v1[:1000] + [
        ProjectFile(
            path=f"src/new_module/new_{j}.py",
            absolute_path=str(tmp_path / f"src/new_module/new_{j}.py"),
            file_size=50,
            last_modified=1700001000.0,
            sha256_hash=f"new_hash_{j}",
            file_type=".py",
            is_binary=False,
            is_large=False,
            is_ignored=False,
        )
        for j in range(200)
    ]

    # Deleting 1,500 files at once verifies batched deletion does not exceed SQLite variable limits
    db.sync_files(project.id, files_v2)
    updated_files = db.get_files_for_project(project.id)
    assert len(updated_files) == 1200
    updated_paths = {f.path for f in updated_files}
    assert "src/module_0/file_0.py" in updated_paths
    assert "src/module_999/file_999.py" in updated_paths
    assert "src/module_1500/file_1500.py" not in updated_paths
    assert "src/new_module/new_0.py" in updated_paths


def test_conversation_project_binding_lifecycle(tmp_path: Path):
    """Verifies project registration, conversation binding, 1:1 cardinality, rebinding, and removal."""
    from backend.domain.models import Conversation, ConversationMessage, ConversationSource

    db_file = tmp_path / "binding_test.db"
    db = Database(db_file)

    p1 = Project(id="prj_1", name="Project One", root_path=str(tmp_path / "p1"))
    p2 = Project(id="prj_2", name="Project Two", root_path=str(tmp_path / "p2"))
    db.upsert_project(p1)
    db.upsert_project(p2)

    # 1. Project list
    projects = db.list_projects()
    assert len(projects) == 2
    assert {p.id for p in projects} == {"prj_1", "prj_2"}

    # 2. Ingest conversation
    conv = Conversation(
        conversation_id="conv_lifecycle_1",
        provider="CHATGPT",
        source=ConversationSource.WEB_EXTENSION,
        title="Binding Test",
        messages=[ConversationMessage(message_id="m1", role="USER", content="Hello", sequence=1)],
    )
    db.save_conversation(conv)

    # Initially unbound
    assert db.get_conversation_binding("conv_lifecycle_1") is None

    # 3. Bind to Project One
    binding1 = db.bind_conversation_to_project("conv_lifecycle_1", "prj_1")
    assert binding1.project_id == "prj_1"
    assert binding1.conversation_id == "conv_lifecycle_1"
    assert binding1.binding_source == "USER_SELECTED"

    # Verify conversation table updated
    saved_conv = db.get_conversation("conv_lifecycle_1")
    assert saved_conv.project_id == "prj_1"

    # 4. Rebind to Project Two (1:0..1 cardinality constraint)
    binding2 = db.bind_conversation_to_project("conv_lifecycle_1", "prj_2")
    assert binding2.project_id == "prj_2"
    assert binding2.binding_id == binding1.binding_id  # Stable binding row updated

    current_binding = db.get_conversation_binding("conv_lifecycle_1")
    assert current_binding.project_id == "prj_2"

    saved_conv = db.get_conversation("conv_lifecycle_1")
    assert saved_conv.project_id == "prj_2"

    # 5. Remove binding -> sets conversation back to unbound
    removed = db.remove_conversation_binding("conv_lifecycle_1")
    assert removed is True
    assert db.get_conversation_binding("conv_lifecycle_1") is None

    saved_conv = db.get_conversation("conv_lifecycle_1")
    assert saved_conv.project_id is None


def test_schema_migration_v10_preserves_existing_data(tmp_path: Path):
    """Verifies that migration v10 applies cleanly to an existing v9 database preserving records."""
    import backend.project_model.migrations as mig
    from backend.domain.models import Conversation, ConversationMessage, ConversationSource

    db_file = tmp_path / "v9_upgrade.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    # Apply up to v9 only
    mig.ensure_migration_table(conn)
    for v, desc, func in mig.MIGRATIONS:
        if v <= 9:
            with conn:
                func(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), ?)",
                    (v, desc),
                )

    # Insert a v9 project and conversation
    with conn:
        conn.execute(
            "INSERT INTO projects (id, name, root_path, created_at, updated_at) VALUES ('p_old', 'Old Project', '/old', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO conversations (conversation_id, provider, source, title, created_at, updated_at, metadata_json) VALUES ('c_old', 'CLAUDE', 'IMPORT', 'Old Conv', '2026-01-01', '2026-01-01', '{}')"
        )
    conn.close()

    # Now open with Database class (which runs migrations up to v10)
    db = Database(db_file)
    assert db.get_schema_version() == 10

    # Verify old data survived intact
    p = db.get_project_by_id("p_old")
    assert p is not None
    assert p.name == "Old Project"

    c = db.get_conversation("c_old")
    assert c is not None
    assert c.title == "Old Conv"

    # Verify binding can be added to the upgraded database
    binding = db.bind_conversation_to_project("c_old", "p_old")
    assert binding.project_id == "p_old"
    assert db.get_conversation_binding("c_old") is not None

