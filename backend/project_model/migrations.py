"""SQLite schema migrations management."""

import sqlite3
from typing import List, Tuple, Callable

# Migration functions must accept a sqlite3.Connection and execute their DDL/DML.
Migration = Tuple[int, str, Callable[[sqlite3.Connection], None]]


def migration_v1(conn: sqlite3.Connection) -> None:
    """Initial schema version 1."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            path TEXT NOT NULL,
            absolute_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            last_modified REAL NOT NULL,
            sha256_hash TEXT NOT NULL,
            file_type TEXT NOT NULL,
            is_binary INTEGER NOT NULL,
            is_large INTEGER NOT NULL,
            is_ignored INTEGER NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, path)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_files_project_path ON files(project_id, path)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS git_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            is_git_repo INTEGER NOT NULL,
            current_branch TEXT,
            head_commit TEXT,
            is_dirty INTEGER NOT NULL,
            untracked_count INTEGER NOT NULL,
            modified_count INTEGER NOT NULL,
            staged_count INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            total_files INTEGER NOT NULL,
            duration_ms REAL NOT NULL,
            scanned_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)


MIGRATIONS: List[Migration] = [
    (1, "Initial schema: projects, files, git_states, scan_runs", migration_v1),
]


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)
    conn.commit()


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    ensure_migration_table(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(version) FROM schema_migrations")
    row = cursor.fetchone()
    if row and row[0] is not None:
        return int(row[0])
    return 0


def apply_migrations(conn: sqlite3.Connection) -> List[int]:
    ensure_migration_table(conn)
    current_version = get_current_schema_version(conn)
    applied = []

    for version, description, func in MIGRATIONS:
        if version > current_version:
            with conn:
                func(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), ?)",
                    (version, description),
                )
            applied.append(version)

    return applied
