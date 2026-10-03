# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 4: Context Engine (Pipeline, Secret Redaction, Compression)**

## Status
**Completed / Ready for Audit Gate Review**

---

## What Works
- Baseline project architecture and contracts (`docs/`).
- Python package setup (`pyproject.toml`) with Pydantic and pytest.
- Pydantic domain models:
  - M1: `Project`, `ProjectFile`, `GitState`, `SchemaVersion`, `ProjectSummary`, `ScanResult`.
  - M2: `ProjectGraph`, `GraphNode`, `GraphEdge`, `ProvenanceRecord`, `NodeType`, `EdgeType`.
  - M3: `ChangeSet`, `FileChange`, `DiffHunk`, `EvidenceRecord`, `ChangeType`.
  - M4: `ContextPurpose`, `ContextSourceType`, `ContextItem`, `ContextRequest`, `ContextPacket`.
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Migration v2: `graph_nodes` and `graph_edges` tables with cascading foreign keys and indexes.
  - Migration v3: `change_sets`, `file_changes`, `diff_hunks`, and `evidence_records` tables with cascading foreign keys and indexes.
  - Migration v4: `context_requests`, `context_packets`, and `context_items` tables with composite primary keys, foreign keys, and indexes.
  - Deterministic caching (`cache_key`) for ContextPackets based on project ID, changeset ID, graph node/edge signature, purpose, target files, and token budget.
- Safe, read-only Git state detector (`detect_git_state`):
  - Inspects branch name, HEAD commit hash, dirty flag, untracked/modified/staged file counts.
  - Safe fallback for non-git directories, empty repositories, and Git worktree file pointers.
  - Zero state-mutating Git commands executed.
- Gitignore & Exclusion Filter (`IgnoreFilter`):
  - Built-in default exclusions (`.git`, `node_modules`, `venv`, `__pycache__`, `.buildcoach`, etc.).
  - Root-anchored leading-slash rules (`/build`, `/secrets`, `/temp/*.log`), wildcards, directory-only patterns, and negations.
- Passive Local Project Scanner (`ProjectScanner`):
  - Project root detection upward traversal supporting directory and file inputs.
  - Safe file inspection strictly confined to workspace boundaries.
  - Streaming SHA-256 computation in 64 KB chunks.
  - 8 KB null-byte inspection for binary file detection.
  - 1 MB file size threshold tagging for large files (`is_large`).
  - Alphabetically sorted file walking for 100% deterministic output.
  - Automatic integration with Project Graph generation and Development Context collection.
- Project Graph Builder & Passive Relationship Extractor (`ProjectGraphBuilder`, `relationship_extractor`):
  - Node entities: `PROJECT`, `DIRECTORY`, `FILE`, `MODULE`.
  - Edge entities: `CONTAINS`, `IMPORTS`, `REFERENCES`.
  - Deterministic import extraction for Python (`ast.parse`) and JavaScript/TypeScript (safe statement accumulation for single-line and multiline imports).
  - Relative import resolution to internal project files (`file:{path}`) and external module resolution (`module:{pkg}`).
  - Strict provenance recording (`source_file`, `line_number`, `raw_statement`, `source_type`, `confidence`).
  - Graph synchronization: additions, modifications, and deletions cleanly reconcile without stale edges.
- Development Context & Change Evidence Engine (`ContextDetector`, `diff_parser`):
  - 5-layer context priority: 1. Working tree, 2. Git status, 3. Unstaged diff, 4. Staged diff, 5. Recent commit info.
  - Deterministic unified diff parsing for added, modified, deleted, and renamed files.
  - Multiline hunk tracking with old/new line ranges and content preservation.
  - Binary file modification detection without reading raw binary payloads into memory.
  - Strictly factual EvidenceRecords (zero inference; zero claims of user intent).
  - 100% deterministic SHA-256 IDs for ChangeSets, FileChanges, DiffHunks, and EvidenceRecords.
- Context Engine (`ContextEngine`, `backend.context_engine`):
  - Deterministic 7-stage pipeline: `RAW DATA -> NORMALIZE -> RELEVANCE FILTER -> SECRET DETECTION -> REDACTION -> COMPRESS -> CONTEXT PACKET`.
  - Documented scoring tiers (100.0 directly changed files, 95.0 diff hunks, 90.0 explicit targets, 70.0 dependencies, 60.0 dependents, 50.0 related tests, 40.0 configs, 30.0 change evidence, 0.0 unrelated files filtered out).
  - Canonical item ordering rules: changeset summary -> changed files -> changed hunks -> dependencies/dependents -> tests -> evidence -> explicit targets -> supporting context.
  - Deterministic secret detection and redaction (API keys, bearer tokens, passwords, private keys, .env credentials) with zero raw secret strings written to logs, context packets, or SQLite.
  - Compression and budgeting (engineering target 3,000–6,000 tokens, default 4,000 tokens) with whitespace normalization, deduplication, oversized file line-pruning, binary file omission, and explicit `truncation_status` tracking.
  - Deterministic ContextPacket SHA-256 IDs and cache keys.

---

## What Does Not Work (Intentional Scope Boundaries)
- AI Gateway provider integrations (scheduled for Milestone 5).
- User-facing workflows (scheduled for Milestones 6–8).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 85 automated tests passing in `tests/`:
  - `test_context_engine.py` (30 tests):
    1. Empty project context
    2. Clean working tree
    3. Project with current changes
    4. Changed file ranking (100.0 / 95.0)
    5. Direct dependency ranking (70.0)
    6. Direct dependent ranking (60.0)
    7. Unrelated file exclusion
    8. Relevant test selection (50.0)
    9. Evidence selection (30.0)
    10. Duplicate context removal
    11. Deterministic ranking
    12. Deterministic ContextPacket ID
    13. Deterministic item ordering
    14. Secret detection
    15. Secret redaction
    16. Multiple secrets in one file
    17. Secret not persisted in memory or SQLite
    18. Provenance preservation
    19. Context budget within limit
    20. Context truncation behavior
    21. Critical-item preservation during truncation
    22. Compression behavior
    23. Binary-file handling
    24. Large-file handling
    25. Malformed input handling
    26. Missing evidence handling
    27. Missing Project Graph handling
    28. Cache invalidation after ChangeSet changes
    29. Repeated ContextPacket generation
    30. End-to-end pipeline fixture (`auth.py` -> `middleware.py` -> `auth_test.py` -> `unrelated.py`)
  - `test_context.py` (18 tests): ChangeSets, FileChanges, DiffHunks, EvidenceRecords, Git status/diff parsing, determinism, syncing.
  - `test_db.py` (5 tests): Migrations v1–v3, CRUD, syncing, large file sets.
  - `test_git_detector.py` (3 tests): Non-git, worktree, empty and dirty repos.
  - `test_gitignore.py` (3 tests): Root-anchored rules, wildcards, negations, boundaries.
  - `test_graph.py` (14 tests): Nodes, edges, AST & regex imports, multiline statements, sync lifecycle.
  - `test_scanner.py` (12 tests): Root detection, binary files, large files (>1MB), boundary enforcement, permissions.
- **Live Local Test**: 85 passed in 15.85s.

---

## Known Limitations
- **Secret Detection Scope**: Detection relies on deterministic regular expressions targeting high-entropy keys, bearer tokens, passwords, private keys, and environment variables. It does not claim 100% discovery of novel, custom-encoded, or obfuscated secrets.
- **Language Relationship Extraction Scope**: Import extraction covers Python, JavaScript, and TypeScript without full compiler infrastructure. Other languages (`.go`, `.rs`, `.sql`, `.md`, etc.) are captured as `FILE` nodes with `extraction_supported=False` and zero hallucinated edges.
- **Dynamic Imports with Variable Expressions**: Statically unknown expressions (e.g. `import(variable)`) are intentionally omitted from static relationship extraction.
- **Token Estimation Heuristic**: Character-based heuristic (`max(1, len(text) // 4)`) provides an engineering estimate, not exact tokenizer parity for every third-party LLM.

---

## Next Milestone
**Milestone 5: AI Gateway**
- Decoupled AI provider interface (`AIProvider`).
- Initial provider adapter (e.g. Gemini Provider).
- Timeout, retry with exponential backoff, circuit breaking, and structured output parsing.
- Safe handling of redacted context packets and zero raw secret exposure in logs.
