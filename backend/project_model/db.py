"""Database management and repository operations for AI Build Coach."""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

from backend.domain.models import (
    Project,
    ProjectFile,
    GitState,
    ProjectSummary,
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
    ContextPacket,
    ContextItem,
    ContextRequest,
    ContextPurpose,
    ContextSourceType,
)
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

    def save_graph(self, graph: ProjectGraph) -> None:
        """Saves or synchronizes project graph nodes and edges into the database."""
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                # 1. Synchronize nodes
                cursor.execute("SELECT id FROM graph_nodes WHERE project_id = ?", (graph.project_id,))
                existing_node_ids = {row["id"] for row in cursor.fetchall()}
                current_node_ids = set(graph.nodes.keys())
                deleted_node_ids = existing_node_ids - current_node_ids

                # 2. Synchronize edges
                cursor.execute("SELECT id FROM graph_edges WHERE project_id = ?", (graph.project_id,))
                existing_edge_ids = {row["id"] for row in cursor.fetchall()}
                current_edge_ids = {e.id for e in graph.edges}
                deleted_edge_ids = existing_edge_ids - current_edge_ids

                # Delete removed edges first to respect foreign keys
                if deleted_edge_ids:
                    del_edges = list(deleted_edge_ids)
                    batch_size = 500
                    for i in range(0, len(del_edges), batch_size):
                        batch = del_edges[i : i + batch_size]
                        placeholders = ",".join("?" for _ in batch)
                        conn.execute(
                            f"DELETE FROM graph_edges WHERE project_id = ? AND id IN ({placeholders})",
                            [graph.project_id] + batch,
                        )

                # Delete removed nodes
                if deleted_node_ids:
                    del_nodes = list(deleted_node_ids)
                    batch_size = 500
                    for i in range(0, len(del_nodes), batch_size):
                        batch = del_nodes[i : i + batch_size]
                        placeholders = ",".join("?" for _ in batch)
                        conn.execute(
                            f"DELETE FROM graph_nodes WHERE project_id = ? AND id IN ({placeholders})",
                            [graph.project_id] + batch,
                        )

                # Upsert current nodes
                if graph.nodes:
                    node_records = [
                        (
                            n.id,
                            n.project_id,
                            n.node_type,
                            n.name,
                            n.path,
                            json.dumps(n.metadata, sort_keys=True),
                            n.created_at,
                        )
                        for n in sorted(graph.nodes.values(), key=lambda x: x.id)
                    ]
                    conn.executemany(
                        """
                        INSERT INTO graph_nodes (id, project_id, node_type, name, path, metadata, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            node_type = excluded.node_type,
                            name = excluded.name,
                            path = excluded.path,
                            metadata = excluded.metadata
                        """,
                        node_records,
                    )

                # Upsert current edges
                if graph.edges:
                    edge_records = [
                        (
                            e.id,
                            e.project_id,
                            e.source_node_id,
                            e.target_node_id,
                            e.edge_type,
                            e.evidence.source_file,
                            e.evidence.line_number,
                            e.evidence.raw_statement,
                            e.evidence.source_type,
                            e.evidence.confidence,
                            e.status,
                            e.created_at,
                        )
                        for e in sorted(graph.edges, key=lambda x: x.id)
                    ]
                    conn.executemany(
                        """
                        INSERT INTO graph_edges (
                            id, project_id, source_node_id, target_node_id, edge_type,
                            source_file, line_number, raw_statement, source_type, confidence, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            source_file = excluded.source_file,
                            line_number = excluded.line_number,
                            raw_statement = excluded.raw_statement,
                            source_type = excluded.source_type,
                            confidence = excluded.confidence,
                            status = excluded.status
                        """,
                        edge_records,
                    )
        finally:
            conn.close()

    def get_graph(self, project_id: str) -> Optional[ProjectGraph]:
        """Loads the project graph from database."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
            if not cursor.fetchone():
                return None

            cursor.execute(
                "SELECT id, project_id, node_type, name, path, metadata, created_at FROM graph_nodes WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            )
            node_rows = cursor.fetchall()
            nodes: Dict[str, GraphNode] = {}
            for r in node_rows:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
                nodes[r["id"]] = GraphNode(
                    id=r["id"],
                    project_id=r["project_id"],
                    node_type=r["node_type"],
                    name=r["name"],
                    path=r["path"],
                    metadata=meta,
                    created_at=r["created_at"],
                )

            cursor.execute(
                """
                SELECT id, project_id, source_node_id, target_node_id, edge_type,
                       source_file, line_number, raw_statement, source_type, confidence, status, created_at
                FROM graph_edges WHERE project_id = ? ORDER BY id ASC
                """,
                (project_id,),
            )
            edge_rows = cursor.fetchall()
            edges: List[GraphEdge] = []
            for r in edge_rows:
                evidence = ProvenanceRecord(
                    source_file=r["source_file"] or "",
                    line_number=r["line_number"],
                    raw_statement=r["raw_statement"],
                    source_type=r["source_type"] or "unknown",
                    confidence=r["confidence"] or "HIGH",
                )
                edges.append(
                    GraphEdge(
                        id=r["id"],
                        project_id=r["project_id"],
                        source_node_id=r["source_node_id"],
                        target_node_id=r["target_node_id"],
                        edge_type=r["edge_type"],
                        evidence=evidence,
                        status=r["status"] or "CONFIRMED",
                        created_at=r["created_at"],
                    )
                )

            return ProjectGraph(project_id=project_id, nodes=nodes, edges=edges)
        finally:
            conn.close()

    def save_change_set(self, change_set: ChangeSet) -> None:
        """Persists a ChangeSet, its file changes, diff hunks, and evidence records atomically."""
        conn = self.get_connection()
        try:
            with conn:
                # 1. Upsert change_set
                conn.execute(
                    """
                    INSERT INTO change_sets (id, project_id, head_commit, is_dirty, total_changed_files, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        head_commit = excluded.head_commit,
                        is_dirty = excluded.is_dirty,
                        total_changed_files = excluded.total_changed_files,
                        summary = excluded.summary,
                        created_at = excluded.created_at
                    """,
                    (
                        change_set.id,
                        change_set.project_id,
                        change_set.git_state.head_commit,
                        1 if change_set.git_state.is_dirty else 0,
                        len(change_set.file_changes),
                        json.dumps(change_set.summary),
                        change_set.created_at,
                    ),
                )

                # Delete existing child items for this change_set if re-saving
                conn.execute("DELETE FROM diff_hunks WHERE change_set_id = ?", (change_set.id,))
                conn.execute("DELETE FROM file_changes WHERE change_set_id = ?", (change_set.id,))
                conn.execute("DELETE FROM evidence_records WHERE change_set_id = ?", (change_set.id,))

                # 2. Insert file_changes
                for fc in change_set.file_changes:
                    conn.execute(
                        """
                        INSERT INTO file_changes (
                            id, change_set_id, project_id, old_path, new_path, change_type,
                            is_staged, is_untracked, old_line_count, new_line_count, line_ranges, is_binary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fc.id,
                            change_set.id,
                            change_set.project_id,
                            fc.old_path,
                            fc.new_path,
                            fc.change_type,
                            1 if fc.is_staged else 0,
                            1 if fc.is_untracked else 0,
                            fc.old_line_count,
                            fc.new_line_count,
                            json.dumps(fc.line_ranges),
                            1 if fc.is_binary else 0,
                        ),
                    )

                    # 3. Insert diff_hunks
                    for hunk in fc.hunks:
                        conn.execute(
                            """
                            INSERT INTO diff_hunks (
                                id, file_change_id, change_set_id, old_start, old_lines, new_start, new_lines, header, content
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                hunk.id,
                                fc.id,
                                change_set.id,
                                hunk.old_start,
                                hunk.old_lines,
                                hunk.new_start,
                                hunk.new_lines,
                                hunk.header,
                                hunk.content,
                            ),
                        )

                # 4. Insert evidence_records
                for ev in change_set.evidence:
                    conn.execute(
                        """
                        INSERT INTO evidence_records (
                            id, project_id, change_set_id, evidence_type, source, file_path, observation, raw_data, confidence, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ev.id,
                            change_set.project_id,
                            change_set.id,
                            ev.evidence_type,
                            ev.source,
                            ev.file_path,
                            ev.observation,
                            ev.raw_data,
                            ev.confidence,
                            ev.created_at,
                        ),
                    )
        finally:
            conn.close()

    def get_latest_change_set(self, project_id: str) -> Optional[ChangeSet]:
        """Retrieves the latest ChangeSet recorded for a project."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM change_sets WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (project_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self.get_change_set_by_id(row["id"])
        finally:
            conn.close()

    def get_change_set_by_id(self, change_set_id: str) -> Optional[ChangeSet]:
        """Retrieves a ChangeSet with all its file changes, diff hunks, and evidence records."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, head_commit, is_dirty, total_changed_files, summary, created_at
                FROM change_sets WHERE id = ?
                """,
                (change_set_id,),
            )
            cs_row = cursor.fetchone()
            if not cs_row:
                return None

            cursor.execute(
                """
                SELECT id, change_set_id, project_id, old_path, new_path, change_type,
                       is_staged, is_untracked, old_line_count, new_line_count, line_ranges, is_binary
                FROM file_changes WHERE change_set_id = ? ORDER BY new_path ASC, id ASC
                """,
                (change_set_id,),
            )
            fc_rows = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, file_change_id, change_set_id, old_start, old_lines, new_start, new_lines, header, content
                FROM diff_hunks WHERE change_set_id = ? ORDER BY file_change_id ASC, new_start ASC, id ASC
                """,
                (change_set_id,),
            )
            hunk_rows = cursor.fetchall()
            hunks_by_fc: Dict[str, List[DiffHunk]] = {}
            for hr in hunk_rows:
                fc_id = hr["file_change_id"]
                if fc_id not in hunks_by_fc:
                    hunks_by_fc[fc_id] = []
                hunks_by_fc[fc_id].append(
                    DiffHunk(
                        id=hr["id"],
                        file_change_id=fc_id,
                        old_start=hr["old_start"],
                        old_lines=hr["old_lines"],
                        new_start=hr["new_start"],
                        new_lines=hr["new_lines"],
                        header=hr["header"],
                        content=hr["content"],
                    )
                )

            file_changes: List[FileChange] = []
            for fcr in fc_rows:
                fc_id = fcr["id"]
                raw_ranges = json.loads(fcr["line_ranges"]) if fcr["line_ranges"] else []
                ranges = [tuple(r) for r in raw_ranges]
                file_changes.append(
                    FileChange(
                        id=fc_id,
                        change_set_id=change_set_id,
                        old_path=fcr["old_path"],
                        new_path=fcr["new_path"],
                        change_type=fcr["change_type"],
                        is_staged=bool(fcr["is_staged"]),
                        is_untracked=bool(fcr["is_untracked"]),
                        old_line_count=fcr["old_line_count"],
                        new_line_count=fcr["new_line_count"],
                        line_ranges=ranges,
                        hunks=hunks_by_fc.get(fc_id, []),
                        is_binary=bool(fcr["is_binary"]),
                    )
                )

            cursor.execute(
                """
                SELECT id, project_id, change_set_id, evidence_type, source, file_path, observation, raw_data, confidence, created_at
                FROM evidence_records WHERE change_set_id = ? ORDER BY created_at ASC, id ASC
                """,
                (change_set_id,),
            )
            ev_rows = cursor.fetchall()
            evidence: List[EvidenceRecord] = []
            for er in ev_rows:
                evidence.append(
                    EvidenceRecord(
                        id=er["id"],
                        project_id=er["project_id"],
                        change_set_id=er["change_set_id"],
                        evidence_type=er["evidence_type"],
                        source=er["source"],
                        file_path=er["file_path"],
                        observation=er["observation"],
                        raw_data=er["raw_data"],
                        confidence=er["confidence"],
                        created_at=er["created_at"],
                    )
                )

            cursor.execute(
                "SELECT is_git_repo, current_branch, head_commit, is_dirty, untracked_count, modified_count, staged_count "
                "FROM git_states WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (cs_row["project_id"],),
            )
            gs_row = cursor.fetchone()
            if gs_row:
                git_state = GitState(
                    is_git_repo=bool(gs_row["is_git_repo"]),
                    current_branch=gs_row["current_branch"],
                    head_commit=gs_row["head_commit"],
                    is_dirty=bool(gs_row["is_dirty"]),
                    untracked_count=gs_row["untracked_count"],
                    modified_count=gs_row["modified_count"],
                    staged_count=gs_row["staged_count"],
                )
            else:
                git_state = GitState(
                    is_git_repo=True if cs_row["head_commit"] else False,
                    head_commit=cs_row["head_commit"],
                    is_dirty=bool(cs_row["is_dirty"]),
                )

            summary = json.loads(cs_row["summary"]) if cs_row["summary"] else {}

            return ChangeSet(
                id=cs_row["id"],
                project_id=cs_row["project_id"],
                git_state=git_state,
                file_changes=file_changes,
                evidence=evidence,
                summary=summary,
                created_at=cs_row["created_at"],
            )
        finally:
            conn.close()

    def get_evidence_for_change_set(self, change_set_id: str) -> List[EvidenceRecord]:
        """Retrieves evidence records linked to a specific change set."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, change_set_id, evidence_type, source, file_path, observation, raw_data, confidence, created_at
                FROM evidence_records WHERE change_set_id = ? ORDER BY id ASC
                """,
                (change_set_id,),
            )
            rows = cursor.fetchall()
            return [
                EvidenceRecord(
                    id=r["id"],
                    project_id=r["project_id"],
                    change_set_id=r["change_set_id"],
                    evidence_type=r["evidence_type"],
                    source=r["source"],
                    file_path=r["file_path"],
                    observation=r["observation"],
                    raw_data=r["raw_data"],
                    confidence=r["confidence"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def save_context_packet(
        self,
        packet: ContextPacket,
        request: Optional[ContextRequest] = None,
        cache_key: Optional[str] = None,
    ) -> None:
        """Persists a context packet, its items, and optional request metadata."""
        conn = self.get_connection()
        try:
            with conn:
                request_id = None
                if request is not None:
                    request_id = f"req_{packet.id}"
                    cs_id = request.change_set.id if request.change_set else None
                    conn.execute(
                        """
                        INSERT INTO context_requests (id, project_id, purpose, change_set_id, target_files, budget_tokens, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            purpose = excluded.purpose,
                            target_files = excluded.target_files,
                            budget_tokens = excluded.budget_tokens
                        """,
                        (
                            request_id,
                            request.project_id,
                            request.purpose,
                            cs_id,
                            json.dumps(request.target_files),
                            request.budget_tokens,
                            packet.generated_at,
                        ),
                    )

                conn.execute(
                    """
                    INSERT INTO context_packets (
                        id, project_id, request_id, purpose, packet_version, token_estimate,
                        truncation_status, redaction_summary, evidence_refs, cache_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        token_estimate = excluded.token_estimate,
                        truncation_status = excluded.truncation_status,
                        redaction_summary = excluded.redaction_summary,
                        evidence_refs = excluded.evidence_refs,
                        cache_key = excluded.cache_key
                    """,
                    (
                        packet.id,
                        packet.project_id,
                        request_id,
                        packet.purpose,
                        packet.packet_version,
                        packet.token_estimate,
                        packet.truncation_status,
                        json.dumps(packet.redaction_summary),
                        json.dumps(packet.evidence_refs),
                        cache_key,
                        packet.generated_at,
                    ),
                )

                # Overwrite items atomically for this packet
                conn.execute("DELETE FROM context_items WHERE packet_id = ?", (packet.id,))
                for idx, item in enumerate(packet.items):
                    conn.execute(
                        """
                        INSERT INTO context_items (
                            id, packet_id, source_type, source_reference, file_path,
                            line_start, line_end, relevance_reason, relevance_score,
                            redacted, evidence_refs, content, item_order
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(packet_id, id) DO UPDATE SET
                            source_type = excluded.source_type,
                            source_reference = excluded.source_reference,
                            file_path = excluded.file_path,
                            line_start = excluded.line_start,
                            line_end = excluded.line_end,
                            relevance_reason = excluded.relevance_reason,
                            relevance_score = excluded.relevance_score,
                            redacted = excluded.redacted,
                            evidence_refs = excluded.evidence_refs,
                            content = excluded.content,
                            item_order = excluded.item_order
                        """,
                        (
                            item.item_id,
                            packet.id,
                            item.source_type,
                            item.source_reference,
                            item.file_path,
                            item.line_start,
                            item.line_end,
                            item.relevance_reason,
                            item.relevance_score,
                            1 if item.redacted else 0,
                            json.dumps(item.evidence_refs),
                            item.content,
                            idx,
                        ),
                    )
        finally:
            conn.close()

    def get_context_packet_by_id(self, packet_id: str) -> Optional[ContextPacket]:
        """Retrieves a persisted context packet by its identifier."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, request_id, purpose, packet_version, token_estimate,
                       truncation_status, redaction_summary, evidence_refs, cache_key, created_at
                FROM context_packets WHERE id = ?
                """,
                (packet_id,),
            )
            p_row = cursor.fetchone()
            if not p_row:
                return None

            cursor.execute(
                """
                SELECT id, packet_id, source_type, source_reference, file_path, line_start,
                       line_end, relevance_reason, relevance_score, redacted, evidence_refs, content, item_order
                FROM context_items WHERE packet_id = ? ORDER BY item_order ASC
                """,
                (packet_id,),
            )
            i_rows = cursor.fetchall()

            items = [
                ContextItem(
                    item_id=ir["id"],
                    source_type=ir["source_type"],
                    source_reference=ir["source_reference"],
                    file_path=ir["file_path"],
                    line_start=ir["line_start"],
                    line_end=ir["line_end"],
                    evidence_refs=json.loads(ir["evidence_refs"]) if ir["evidence_refs"] else [],
                    relevance_reason=ir["relevance_reason"],
                    relevance_score=float(ir["relevance_score"]),
                    redacted=bool(ir["redacted"]),
                    content=ir["content"],
                )
                for ir in i_rows
            ]

            return ContextPacket(
                id=p_row["id"],
                project_id=p_row["project_id"],
                purpose=p_row["purpose"],
                generated_at=p_row["created_at"],
                packet_version=p_row["packet_version"],
                items=items,
                evidence_refs=json.loads(p_row["evidence_refs"]) if p_row["evidence_refs"] else [],
                redaction_summary=json.loads(p_row["redaction_summary"]) if p_row["redaction_summary"] else {},
                token_estimate=p_row["token_estimate"],
                truncation_status=p_row["truncation_status"],
            )
        finally:
            conn.close()

    def get_cached_context_packet(self, cache_key: str) -> Optional[ContextPacket]:
        """Retrieves a cached context packet matching the exact deterministic cache key."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM context_packets WHERE cache_key = ? ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self.get_context_packet_by_id(row["id"])
        finally:
            conn.close()

    def record_gateway_run(
        self,
        run_id: str,
        packet_id: str,
        provider: str,
        model: str,
        tokens_prompt: int,
        tokens_candidate: int,
        latency_ms: float,
        claims_count: int,
        grounded_count: int,
        unknown_count: int,
        created_at: Optional[str] = None,
    ) -> None:
        """Records an execution of the AI Gateway in the local audit table."""
        from backend.domain.models import utc_now_iso

        ts = created_at or utc_now_iso()
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO gateway_runs (
                        id, packet_id, provider, model, tokens_prompt, tokens_candidate,
                        latency_ms, claims_count, grounded_count, unknown_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        packet_id,
                        provider,
                        model,
                        tokens_prompt,
                        tokens_candidate,
                        latency_ms,
                        claims_count,
                        grounded_count,
                        unknown_count,
                        ts,
                    ),
                )
        finally:
            conn.close()

    def get_gateway_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a gateway run audit entry by ID."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM gateway_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()

    def record_understand_change_run(
        self,
        run_id: str,
        project_id: str,
        changeset_id: str,
        packet_id: str,
        gateway_run_id: Optional[str],
        primary_category: str,
        files_changed_count: int,
        grounding_ratio: float,
        created_at: Optional[str] = None,
    ) -> None:
        """Records an execution of the Understand What Changed workflow in the local audit table."""
        from backend.domain.models import utc_now_iso

        ts = created_at or utc_now_iso()
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO understand_change_runs (
                        id, project_id, changeset_id, packet_id, gateway_run_id,
                        primary_category, files_changed_count, grounding_ratio, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        project_id,
                        changeset_id,
                        packet_id,
                        gateway_run_id,
                        primary_category,
                        files_changed_count,
                        grounding_ratio,
                        ts,
                    ),
                )
        finally:
            conn.close()

    def get_understand_change_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves an understand_change run audit entry by ID."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM understand_change_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()

    def reserve_comprehension_attempt(
        self,
        project_id: str,
        changeset_id: str,
        prompt_id: str,
        packet_id: str,
        attempt_number: int,
        timeout_seconds: float = 180.0,
    ) -> str:
        """Atomically validates attempt progression with stale crash recovery and reserves the attempt slot.
        Stale timeout (default 180s) is safely larger than the maximum expected gateway execution duration.
        """
        from backend.domain.models import utc_now_iso
        from backend.comprehension.exceptions import (
            InvalidAttemptProgressionError,
            ConcurrentAttemptError,
        )

        run_id = f"crun_{uuid.uuid4().hex[:16]}"
        now_iso = utc_now_iso()
        now_dt = datetime.now(timezone.utc)

        conn = self.get_connection()
        conn.isolation_level = None  # Explicit transaction control for BEGIN IMMEDIATE
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    """
                    SELECT run_id, attempt_number, run_status, overall_state, started_at 
                    FROM comprehension_runs 
                    WHERE project_id = ? AND changeset_id = ? AND prompt_id = ? 
                    ORDER BY attempt_number DESC LIMIT 1
                    """,
                    (project_id, changeset_id, prompt_id),
                )
                row = cursor.fetchone()

                if row is None:
                    if attempt_number != 1:
                        raise InvalidAttemptProgressionError(f"First attempt must be 1, got {attempt_number}")
                else:
                    last_run_id = row["run_id"]
                    last_attempt = row["attempt_number"]
                    last_status = row["run_status"]
                    last_state = row["overall_state"]
                    started_at_str = row["started_at"]

                    # Stale run recovery: IN_PROGRESS older than recovery timeout -> FAILED
                    if last_status == "IN_PROGRESS":
                        try:
                            started_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                            elapsed = (now_dt - started_dt).total_seconds()
                        except Exception:
                            elapsed = timeout_seconds + 1.0

                        if elapsed > timeout_seconds:
                            cursor.execute(
                                """
                                UPDATE comprehension_runs 
                                SET run_status = 'FAILED', overall_state = 'UNKNOWN' 
                                WHERE run_id = ?
                                """,
                                (last_run_id,),
                            )
                            last_status = "FAILED"
                        else:
                            raise ConcurrentAttemptError(f"Attempt {last_attempt} is currently IN_PROGRESS.")

                    if last_state == "UNDERSTOOD":
                        raise InvalidAttemptProgressionError("Prompt already UNDERSTOOD; further attempts disallowed.")
                    if last_attempt >= 3:
                        raise InvalidAttemptProgressionError("Maximum attempt limit (3) reached.")
                    if attempt_number != last_attempt + 1:
                        raise InvalidAttemptProgressionError(f"Expected attempt {last_attempt + 1}, got {attempt_number}")

                cursor.execute(
                    """
                    INSERT INTO comprehension_runs (
                        run_id, project_id, changeset_id, packet_id, prompt_id,
                        attempt_number, run_status, overall_state, gap_count, started_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'IN_PROGRESS', 'UNKNOWN', 0, ?, ?)
                    """,
                    (run_id, project_id, changeset_id, packet_id, prompt_id, attempt_number, now_iso, now_iso),
                )
                cursor.execute("COMMIT")
                return run_id
            except Exception:
                cursor.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def finalize_comprehension_run(
        self,
        run_id: str,
        run_status: str,
        overall_state: str,
        gap_count: int,
    ) -> None:
        """Updates reserved attempt record upon completion or failure."""
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE comprehension_runs 
                    SET run_status = ?, overall_state = ?, gap_count = ?
                    WHERE run_id = ?
                    """,
                    (run_status, overall_state, gap_count, run_id),
                )
        finally:
            conn.close()

    def get_latest_comprehension_run(
        self,
        project_id: str,
        changeset_id: str,
        prompt_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves the latest comprehension run record for a prompt."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM comprehension_runs 
                WHERE project_id = ? AND changeset_id = ? AND prompt_id = ? 
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (project_id, changeset_id, prompt_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()

    def get_comprehension_runs(
        self,
        project_id: str,
        changeset_id: str,
        prompt_id: str,
    ) -> List[Dict[str, Any]]:
        """Retrieves all comprehension run records for a prompt ordered by attempt number."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM comprehension_runs 
                WHERE project_id = ? AND changeset_id = ? AND prompt_id = ? 
                ORDER BY attempt_number ASC
                """,
                (project_id, changeset_id, prompt_id),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Milestone 8: Viva Defence Engine Operations
    # -------------------------------------------------------------------------

    def create_viva_session(
        self,
        session_id: str,
        project_id: str,
        status: str = "ACTIVE",
        mode: str = "PROJECT_WIDE",
        current_turn: int = 0,
        base_questions_asked: int = 0,
        followups_asked: int = 0,
        current_difficulty: str = "EASY",
        target_categories: Optional[List[str]] = None,
        started_at: Optional[str] = None,
        initial_difficulty: Optional[str] = None,
    ) -> None:
        """Creates a new Viva Defence session in SQLite."""
        from backend.domain.models import utc_now_iso

        diff = initial_difficulty or current_difficulty
        cats = target_categories or []
        start_ts = started_at or utc_now_iso()

        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO viva_sessions (
                        session_id, project_id, status, mode, current_turn,
                        base_questions_asked, followups_asked, current_difficulty,
                        target_categories_json, started_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        session_id,
                        project_id,
                        status,
                        mode,
                        current_turn,
                        base_questions_asked,
                        followups_asked,
                        diff,
                        json.dumps(cats),
                        start_ts,
                        start_ts,
                    ),
                )
        finally:
            conn.close()

    def get_viva_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a viva session record by session_id."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM viva_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["target_categories"] = json.loads(data["target_categories_json"])
            return data
        finally:
            conn.close()

    def update_viva_session(
        self,
        session_id: str,
        status: Optional[str] = None,
        current_turn: Optional[int] = None,
        base_questions_asked: Optional[int] = None,
        followups_asked: Optional[int] = None,
        current_difficulty: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        """Updates viva session status, turn counts, and difficulty."""
        from backend.domain.models import utc_now_iso

        now_iso = utc_now_iso()
        fields = ["updated_at = ?"]
        params = [now_iso]

        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if current_turn is not None:
            fields.append("current_turn = ?")
            params.append(current_turn)
        if base_questions_asked is not None:
            fields.append("base_questions_asked = ?")
            params.append(base_questions_asked)
        if followups_asked is not None:
            fields.append("followups_asked = ?")
            params.append(followups_asked)
        if current_difficulty is not None:
            fields.append("current_difficulty = ?")
            params.append(current_difficulty)
        if completed_at is not None:
            fields.append("completed_at = ?")
            params.append(completed_at)

        params.append(session_id)
        query = f"UPDATE viva_sessions SET {', '.join(fields)} WHERE session_id = ?"

        conn = self.get_connection()
        try:
            with conn:
                conn.execute(query, tuple(params))
        finally:
            conn.close()

    def save_viva_question(
        self,
        question_id: str,
        session_id: str,
        turn_index: int,
        category: str,
        difficulty: str,
        question_text: str,
        target_modules: List[str],
        target_files: List[str],
        expected_concepts: List[str],
        supporting_evidence_ids: List[str],
        is_follow_up: bool,
        parent_question_id: Optional[str],
        packet_id: str,
        created_at: str,
    ) -> None:
        """Persists a viva question so the session can resume or recover after restart."""
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO viva_questions (
                        question_id, session_id, turn_index, category, difficulty,
                        question_text, target_modules_json, target_files_json,
                        expected_concepts_json, supporting_evidence_ids_json,
                        is_follow_up, parent_question_id, packet_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, turn_index) DO UPDATE SET
                        question_text = excluded.question_text,
                        difficulty = excluded.difficulty,
                        category = excluded.category
                    """,
                    (
                        question_id,
                        session_id,
                        turn_index,
                        category,
                        difficulty,
                        question_text,
                        json.dumps(target_modules),
                        json.dumps(target_files),
                        json.dumps(expected_concepts),
                        json.dumps(supporting_evidence_ids),
                        1 if is_follow_up else 0,
                        parent_question_id,
                        packet_id,
                        created_at,
                    ),
                )
        finally:
            conn.close()

    def get_viva_question(self, session_id: str, turn_index: int) -> Optional[Dict[str, Any]]:
        """Retrieves a persisted viva question by session and turn index."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM viva_questions WHERE session_id = ? AND turn_index = ?",
                (session_id, turn_index),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["target_modules"] = json.loads(data["target_modules_json"])
            data["target_files"] = json.loads(data["target_files_json"])
            data["expected_concepts"] = json.loads(data["expected_concepts_json"])
            data["supporting_evidence_ids"] = json.loads(data["supporting_evidence_ids_json"])
            data["is_follow_up"] = bool(data["is_follow_up"])
            return data
        finally:
            conn.close()

    def get_viva_questions_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all questions generated for a session ordered by turn index."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM viva_questions WHERE session_id = ? ORDER BY turn_index ASC",
                (session_id,),
            )
            results = []
            for row in cursor.fetchall():
                data = dict(row)
                data["target_modules"] = json.loads(data["target_modules_json"])
                data["target_files"] = json.loads(data["target_files_json"])
                data["expected_concepts"] = json.loads(data["expected_concepts_json"])
                data["supporting_evidence_ids"] = json.loads(data["supporting_evidence_ids_json"])
                data["is_follow_up"] = bool(data["is_follow_up"])
                results.append(data)
            return results
        finally:
            conn.close()

    def record_viva_turn(
        self,
        turn_id: str,
        session_id: str,
        turn_index: int,
        question_id: str,
        category: str,
        difficulty: str,
        rating: str,
        is_project_grounded: bool,
        gap_count: int,
        is_follow_up: bool,
        evaluated_at: str,
    ) -> None:
        """Records an evaluated viva turn in SQLite (zero student answer text)."""
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO viva_turns (
                        turn_id, session_id, turn_index, question_id,
                        category, difficulty, rating, is_project_grounded,
                        gap_count, is_follow_up, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, turn_index) DO UPDATE SET
                        rating = excluded.rating,
                        is_project_grounded = excluded.is_project_grounded,
                        gap_count = excluded.gap_count,
                        evaluated_at = excluded.evaluated_at
                    """,
                    (
                        turn_id,
                        session_id,
                        turn_index,
                        question_id,
                        category,
                        difficulty,
                        rating,
                        1 if is_project_grounded else 0,
                        gap_count,
                        1 if is_follow_up else 0,
                        evaluated_at,
                    ),
                )
        finally:
            conn.close()

    def get_viva_turns_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieves all evaluated viva turns for a session."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM viva_turns WHERE session_id = ? ORDER BY turn_index ASC",
                (session_id,),
            )
            results = []
            for row in cursor.fetchall():
                data = dict(row)
                data["is_project_grounded"] = bool(data["is_project_grounded"])
                data["is_follow_up"] = bool(data["is_follow_up"])
                results.append(data)
            return results
        finally:
            conn.close()

    def save_viva_report(
        self,
        report_id: str,
        session_id: str,
        project_id: str,
        mode: str,
        total_turns: int,
        readiness: str,
        summary: str,
        category_masteries_json: str,
        strengths_json: str,
        gaps_json: str,
        study_files_json: str,
        created_at: str,
    ) -> None:
        """Persists final Viva Defence Report with full reconstructability."""
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO viva_reports (
                        report_id, session_id, project_id, mode, total_turns,
                        readiness, summary, category_masteries_json, strengths_json,
                        gaps_json, study_files_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        readiness = excluded.readiness,
                        summary = excluded.summary,
                        category_masteries_json = excluded.category_masteries_json,
                        strengths_json = excluded.strengths_json,
                        gaps_json = excluded.gaps_json,
                        study_files_json = excluded.study_files_json
                    """,
                    (
                        report_id,
                        session_id,
                        project_id,
                        mode,
                        total_turns,
                        readiness,
                        summary,
                        category_masteries_json,
                        strengths_json,
                        gaps_json,
                        study_files_json,
                        created_at,
                    ),
                )
        finally:
            conn.close()

    def get_viva_report(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves persisted final Viva Defence Report by session_id."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM viva_reports WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()




