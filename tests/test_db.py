"""Tests for SQLite database and schema migrations."""

import sqlite3
from pathlib import Path
from backend.domain.models import Project, ProjectFile, GitState
from backend.project_model.db import Database
from backend.project_model.migrations import apply_migrations, get_current_schema_version


def test_schema_migrations_initialization(tmp_path: Path):
    db_file = tmp_path / "state.db"
    db = Database(db_file)
    
    # Version should be 1
    assert db.get_schema_version() == 1
    
    # Repeated migration application should be a no-op
    conn = db.get_connection()
    newly_applied = apply_migrations(conn)
    assert len(newly_applied) == 0
    conn.close()


def test_schema_migration_step(tmp_path: Path, monkeypatch):
    import backend.project_model.migrations as mig
    db_file = tmp_path / "state_migrate.db"
    db = Database(db_file)
    assert db.get_schema_version() == 1

    # Simulate registering a new migration v2
    def dummy_v2(conn: sqlite3.Connection):
        conn.execute("CREATE TABLE test_v2 (id INTEGER PRIMARY KEY, name TEXT)")

    custom_migrations = list(mig.MIGRATIONS) + [(2, "Test migration v2", dummy_v2)]
    monkeypatch.setattr(mig, "MIGRATIONS", custom_migrations)

    conn = db.get_connection()
    applied = mig.apply_migrations(conn)
    assert applied == [2]
    assert mig.get_current_schema_version(conn) == 2

    # Verify table created
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_v2'")
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
