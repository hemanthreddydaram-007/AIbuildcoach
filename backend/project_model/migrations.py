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


def migration_v2(conn: sqlite3.Connection) -> None:
    """Version 2: Graph nodes and edges tables."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            node_type TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_project ON graph_nodes(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(project_id, node_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_path ON graph_nodes(project_id, path)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_edges (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            source_file TEXT,
            line_number INTEGER,
            raw_statement TEXT,
            source_type TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (source_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (target_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_project ON graph_edges(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_node_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_type ON graph_edges(project_id, edge_type)")


def migration_v3(conn: sqlite3.Connection) -> None:
    """Version 3: Development context (change_sets, file_changes, diff_hunks, evidence_records)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS change_sets (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            head_commit TEXT,
            is_dirty INTEGER NOT NULL,
            total_changed_files INTEGER NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_change_sets_project ON change_sets(project_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_changes (
            id TEXT PRIMARY KEY,
            change_set_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            old_path TEXT,
            new_path TEXT NOT NULL,
            change_type TEXT NOT NULL,
            is_staged INTEGER NOT NULL,
            is_untracked INTEGER NOT NULL,
            old_line_count INTEGER,
            new_line_count INTEGER,
            line_ranges TEXT NOT NULL,
            is_binary INTEGER NOT NULL,
            FOREIGN KEY (change_set_id) REFERENCES change_sets(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_set ON file_changes(change_set_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_project ON file_changes(project_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS diff_hunks (
            id TEXT PRIMARY KEY,
            file_change_id TEXT NOT NULL,
            change_set_id TEXT NOT NULL,
            old_start INTEGER NOT NULL,
            old_lines INTEGER NOT NULL,
            new_start INTEGER NOT NULL,
            new_lines INTEGER NOT NULL,
            header TEXT,
            content TEXT NOT NULL,
            FOREIGN KEY (file_change_id) REFERENCES file_changes(id) ON DELETE CASCADE,
            FOREIGN KEY (change_set_id) REFERENCES change_sets(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_diff_hunks_file_change ON diff_hunks(file_change_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_diff_hunks_set ON diff_hunks(change_set_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evidence_records (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            change_set_id TEXT,
            evidence_type TEXT NOT NULL,
            source TEXT NOT NULL,
            file_path TEXT,
            observation TEXT NOT NULL,
            raw_data TEXT,
            confidence TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (change_set_id) REFERENCES change_sets(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_records_project ON evidence_records(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_records_set ON evidence_records(change_set_id)")


def migration_v4(conn: sqlite3.Connection) -> None:
    """Version 4: Context engine (context_requests, context_packets, context_items)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_requests (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            change_set_id TEXT,
            target_files TEXT NOT NULL,
            budget_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_requests_project ON context_requests(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_requests_changeset ON context_requests(change_set_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_packets (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            request_id TEXT,
            purpose TEXT NOT NULL,
            packet_version TEXT NOT NULL,
            token_estimate INTEGER NOT NULL,
            truncation_status TEXT NOT NULL,
            redaction_summary TEXT NOT NULL,
            evidence_refs TEXT NOT NULL,
            cache_key TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (request_id) REFERENCES context_requests(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_packets_project ON context_packets(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_packets_cache ON context_packets(cache_key)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_items (
            id TEXT NOT NULL,
            packet_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            file_path TEXT,
            line_start INTEGER,
            line_end INTEGER,
            relevance_reason TEXT NOT NULL,
            relevance_score REAL NOT NULL,
            redacted INTEGER NOT NULL,
            evidence_refs TEXT NOT NULL,
            content TEXT NOT NULL,
            item_order INTEGER NOT NULL,
            PRIMARY KEY (packet_id, id),
            FOREIGN KEY (packet_id) REFERENCES context_packets(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_items_packet ON context_items(packet_id)")


def migration_v5(conn: sqlite3.Connection) -> None:
    """Version 5: AI Gateway audit table (gateway_runs)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gateway_runs (
            id TEXT PRIMARY KEY,
            packet_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_prompt INTEGER NOT NULL,
            tokens_candidate INTEGER NOT NULL,
            latency_ms REAL NOT NULL,
            claims_count INTEGER NOT NULL,
            grounded_count INTEGER NOT NULL,
            unknown_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gateway_runs_packet ON gateway_runs(packet_id)")


def migration_v6(conn: sqlite3.Connection) -> None:
    """Version 6: Understand What Changed audit table (understand_change_runs)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS understand_change_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            changeset_id TEXT NOT NULL,
            packet_id TEXT NOT NULL,
            gateway_run_id TEXT,
            primary_category TEXT NOT NULL,
            files_changed_count INTEGER NOT NULL,
            grounding_ratio REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_understand_change_project ON understand_change_runs(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_understand_change_set ON understand_change_runs(changeset_id)")


def migration_v7(conn: sqlite3.Connection) -> None:
    """Version 7: Comprehension runs (comprehension_runs)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comprehension_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            changeset_id TEXT NOT NULL,
            packet_id TEXT NOT NULL,
            prompt_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            run_status TEXT NOT NULL,
            overall_state TEXT NOT NULL,
            gap_count INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CONSTRAINT uq_comprehension_attempt UNIQUE (project_id, changeset_id, prompt_id, attempt_number)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_comprehension_runs_lookup ON comprehension_runs(project_id, changeset_id, prompt_id, attempt_number)")


def migration_v8(conn: sqlite3.Connection) -> None:
    """Version 8: Viva Defence Engine (viva_sessions, viva_questions, viva_turns, viva_reports)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viva_sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            current_turn INTEGER NOT NULL,
            base_questions_asked INTEGER NOT NULL,
            followups_asked INTEGER NOT NULL,
            current_difficulty TEXT NOT NULL,
            target_categories_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_viva_sessions_proj ON viva_sessions(project_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viva_questions (
            question_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            category TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            question_text TEXT NOT NULL,
            target_modules_json TEXT NOT NULL,
            target_files_json TEXT NOT NULL,
            expected_concepts_json TEXT NOT NULL,
            supporting_evidence_ids_json TEXT NOT NULL,
            is_follow_up INTEGER NOT NULL,
            parent_question_id TEXT,
            packet_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES viva_sessions(session_id) ON DELETE CASCADE,
            CONSTRAINT uq_viva_question_turn UNIQUE (session_id, turn_index)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_viva_questions_lookup ON viva_questions(session_id, turn_index)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viva_turns (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            question_id TEXT NOT NULL,
            category TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            rating TEXT NOT NULL,
            is_project_grounded INTEGER NOT NULL,
            gap_count INTEGER NOT NULL,
            is_follow_up INTEGER NOT NULL,
            evaluated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES viva_sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES viva_questions(question_id) ON DELETE CASCADE,
            CONSTRAINT uq_viva_session_turn UNIQUE (session_id, turn_index)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_viva_turns_lookup ON viva_turns(session_id, turn_index)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS viva_reports (
            report_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            total_turns INTEGER NOT NULL,
            readiness TEXT NOT NULL,
            summary TEXT NOT NULL,
            category_masteries_json TEXT NOT NULL,
            strengths_json TEXT NOT NULL,
            gaps_json TEXT NOT NULL,
            study_files_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES viva_sessions(session_id) ON DELETE CASCADE
        )
    """)


def migration_v9(conn: sqlite3.Connection) -> None:
    """Version 9: Conversation Bridge Foundation (conversations, conversation_messages, conversation_consents)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            source TEXT NOT NULL,
            project_id TEXT,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_provider ON conversations(provider)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT,
            sequence INTEGER NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_messages_conv_seq ON conversation_messages(conversation_id, sequence)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_consents (
            consent_id TEXT PRIMARY KEY,
            approved INTEGER NOT NULL,
            scope TEXT NOT NULL,
            reason TEXT,
            granted_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        )
    """)


def migration_v10(conn: sqlite3.Connection) -> None:
    """Version 10: Conversation-to-Project Binding (conversation_project_bindings)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_project_bindings (
            binding_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            binding_source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bindings_conv ON conversation_project_bindings(conversation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bindings_proj ON conversation_project_bindings(project_id)")


def migration_v11(conn: sqlite3.Connection) -> None:
    """Version 11: Runtime Failure & Change Observation (observation_events)."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observation_events (
            event_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obs_project ON observation_events(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obs_type ON observation_events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_obs_ts ON observation_events(timestamp)")


def ensure_conversation_tables(conn: sqlite3.Connection) -> None:
    """Ensures conversation bridge tables exist with smallest compatible persistence design."""
    migration_v9(conn)
    migration_v10(conn)


MIGRATIONS: List[Migration] = [
    (1, "Initial schema: projects, files, git_states, scan_runs", migration_v1),
    (2, "Project graph: graph_nodes and graph_edges", migration_v2),
    (3, "Development context: change_sets, file_changes, diff_hunks, evidence_records", migration_v3),
    (4, "Context engine: context_requests, context_packets, context_items", migration_v4),
    (5, "AI Gateway: gateway_runs", migration_v5),
    (6, "Understand What Changed: understand_change_runs", migration_v6),
    (7, "Comprehension runs: comprehension_runs", migration_v7),
    (8, "Viva Defence Engine: viva_sessions, viva_questions, viva_turns, viva_reports", migration_v8),
    (9, "Conversation Bridge Foundation: conversations, conversation_messages, conversation_consents", migration_v9),
    (10, "Conversation-to-Project Binding: conversation_project_bindings", migration_v10),
    (11, "Runtime Failure & Change Observation: observation_events", migration_v11),
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
