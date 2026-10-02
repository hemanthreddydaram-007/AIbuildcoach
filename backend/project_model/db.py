"""Database management and repository operations for AI Build Coach."""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

from backend.domain.models import Project, ProjectFile, GitState, ProjectSummary
from backend.project_model.migrations import apply_migrations, get_current_schema_version


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_parent_dir()
        self.init_schema()

    def _ensure_parent_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> List[int]:
        conn = self.get_connection()
        try:
            return apply_migrations(conn)
        finally:
            conn.close()

    def get_schema_version(self) -> int:
        conn = self.get_connection()
        try:
            return get_current_schema_version(conn)
        finally:
            conn.close()

    def upsert_project(self, project: Project) -> None:
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO projects (id, name, root_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        root_path = excluded.root_path,
                        updated_at = excluded.updated_at
                    """,
                    (project.id, project.name, project.root_path, project.created_at, project.updated_at),
                )
        finally:
            conn.close()

    def get_project_by_id(self, project_id: str) -> Optional[Project]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, root_path, created_at, updated_at FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            if row:
                return Project(
                    id=row["id"],
                    name=row["name"],
                    root_path=row["root_path"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            return None
        finally:
            conn.close()

    def get_project_by_root(self, root_path: str) -> Optional[Project]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, root_path, created_at, updated_at FROM projects WHERE root_path = ?", (root_path,))
            row = cursor.fetchone()
            if row:
                return Project(
                    id=row["id"],
                    name=row["name"],
                    root_path=row["root_path"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            return None
        finally:
            conn.close()

    def sync_files(self, project_id: str, files: List[ProjectFile]) -> None:
        """Syncs files for a project: updates existing, inserts new, removes deleted."""
        conn = self.get_connection()
        try:
            with conn:
                # 1. Read existing database paths for the project
                cursor = conn.cursor()
                cursor.execute("SELECT path FROM files WHERE project_id = ?", (project_id,))
                existing_paths = {row["path"] for row in cursor.fetchall()}

                # 2. Build set of current scanned paths
                current_paths = {f.path for f in files}

                # 3. Calculate paths to delete
                deleted_paths = existing_paths - current_paths

                # 4. Delete removed paths using safe batched parameterized operations
                if deleted_paths:
                    deleted_list = list(deleted_paths)
                    batch_size = 500
                    for i in range(0, len(deleted_list), batch_size):
                        batch = deleted_list[i : i + batch_size]
                        placeholders = ",".join("?" for _ in batch)
                        conn.execute(
                            f"DELETE FROM files WHERE project_id = ? AND path IN ({placeholders})",
                            [project_id] + batch,
                        )

                # 5. Upsert all current scanned files
                if files:
                    records = [
                        (
                            project_id,
                            f.path,
                            f.absolute_path,
                            f.file_size,
                            f.last_modified,
                            f.sha256_hash,
                            f.file_type,
                            1 if f.is_binary else 0,
                            1 if f.is_large else 0,
                            1 if f.is_ignored else 0,
                        )
                        for f in files
                    ]
                    conn.executemany(
                        """
                        INSERT INTO files (
                            project_id, path, absolute_path, file_size, last_modified,
                            sha256_hash, file_type, is_binary, is_large, is_ignored
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_id, path) DO UPDATE SET
                            absolute_path = excluded.absolute_path,
                            file_size = excluded.file_size,
                            last_modified = excluded.last_modified,
                            sha256_hash = excluded.sha256_hash,
                            file_type = excluded.file_type,
                            is_binary = excluded.is_binary,
                            is_large = excluded.is_large,
                            is_ignored = excluded.is_ignored
                        """,
                        records,
                    )
        finally:
            conn.close()

    def get_files_for_project(self, project_id: str) -> List[ProjectFile]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT path, absolute_path, file_size, last_modified,
                       sha256_hash, file_type, is_binary, is_large, is_ignored
                FROM files WHERE project_id = ? ORDER BY path ASC
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
            return [
                ProjectFile(
                    path=r["path"],
                    absolute_path=r["absolute_path"],
                    file_size=r["file_size"],
                    last_modified=r["last_modified"],
                    sha256_hash=r["sha256_hash"],
                    file_type=r["file_type"],
                    is_binary=bool(r["is_binary"]),
                    is_large=bool(r["is_large"]),
                    is_ignored=bool(r["is_ignored"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def record_git_state(self, project_id: str, git_state: GitState) -> None:
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO git_states (
                        project_id, is_git_repo, current_branch, head_commit,
                        is_dirty, untracked_count, modified_count, staged_count, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        project_id,
                        1 if git_state.is_git_repo else 0,
                        git_state.current_branch,
                        git_state.head_commit,
                        1 if git_state.is_dirty else 0,
                        git_state.untracked_count,
                        git_state.modified_count,
                        git_state.staged_count,
                    ),
                )
        finally:
            conn.close()

    def get_latest_git_state(self, project_id: str) -> Optional[GitState]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT is_git_repo, current_branch, head_commit, is_dirty,
                       untracked_count, modified_count, staged_count
                FROM git_states WHERE project_id = ? ORDER BY id DESC LIMIT 1
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            if row:
                return GitState(
                    is_git_repo=bool(row["is_git_repo"]),
                    current_branch=row["current_branch"],
                    head_commit=row["head_commit"],
                    is_dirty=bool(row["is_dirty"]),
                    untracked_count=row["untracked_count"],
                    modified_count=row["modified_count"],
                    staged_count=row["staged_count"],
                )
            return None
        finally:
            conn.close()

    def record_scan_run(self, project_id: str, total_files: int, duration_ms: float) -> None:
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO scan_runs (project_id, total_files, duration_ms, scanned_at)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (project_id, total_files, duration_ms),
                )
        finally:
            conn.close()
