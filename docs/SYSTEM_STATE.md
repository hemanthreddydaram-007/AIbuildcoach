# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 2: Project Brain Foundation (Project Graph)**

## Status
**Completed / Ready for Audit Gate Review**

---

## What Works
- Baseline project architecture and contracts (`docs/`).
- Python package setup (`pyproject.toml`) with Pydantic and pytest.
- Pydantic domain models:
  - `Project`, `ProjectFile`, `GitState`, `SchemaVersion`, `ProjectSummary`, `ScanResult`.
  - `ProjectGraph`, `GraphNode`, `GraphEdge`, `ProvenanceRecord`, `NodeType`, `EdgeType`.
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Migration v2: `graph_nodes` and `graph_edges` tables with cascading foreign keys and indexes.
  - Upserting projects, syncing file changes, recording Git states, tracking scan runs, and saving/loading project graphs.
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
  - Graceful handling of permission-denied files, unreadable directories (`os.walk` `onerror`), and non-UTF-8 byte sequences.
- Project Graph Builder & Passive Relationship Extractor (`ProjectGraphBuilder`, `relationship_extractor`):
  - Node entities: `PROJECT`, `DIRECTORY`, `FILE`, `MODULE`.
  - Edge entities: `CONTAINS`, `IMPORTS`, `REFERENCES`.
  - Deterministic import extraction for Python (`ast.parse`) and JavaScript/TypeScript (regex tokenization).
  - Relative import resolution to internal project files (`file:{path}`).
  - External module resolution (`module:{pkg}`).
  - Strict provenance recording (`source_file`, `line_number`, `raw_statement`, `source_type`, `confidence`).
  - Non-crashing error resilience for malformed files (syntax errors recorded in node metadata).
  - Graph synchronization: additions, modifications, and deletions cleanly reconcile without stale edges.
  - 100% idempotent: repeated scans on identical state produce identical nodes, edges, IDs, and ordering.

---

## What Does Not Work (Intentional Scope Boundaries)
- Diff parsing and working-tree change classification (scheduled for Milestone 3).
- Context compression and secret redaction engine (scheduled for Milestone 4).
- AI Gateway provider integrations (scheduled for Milestone 5).
- User-facing workflows (scheduled for Milestones 6–8).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 36 automated tests passing in `tests/`:
  - `test_db.py`: Schema migrations v1 and v2 initialization, incremental migration to v3, project/file CRUD & sync, git state recording, large file-set synchronization (2,500+ records) with batched parameterized deletion.
  - `test_git_detector.py`: Non-git directory handling, empty repository, untracked files, clean committed repository, git worktree `.git` file pointer support.
  - `test_gitignore.py`: Default exclusions, custom `.gitignore` with wildcards, negations, directory-only rules, root-anchored leading-slash rules, and out-of-bounds rejection.
  - `test_scanner.py`: Root marker detection with directory, file, nested file, nonexistent path inputs, empty project scan, nested projects, ignored directories, large file detection (>1MB), binary file detection, repeated scan idempotence, path traversal boundary enforcement, permission error handling, non-UTF-8 byte handling, and inaccessible directory traversal.
  - `test_graph.py`: Project, Directory, and File node creation, CONTAINS relationships, Python import extraction, JavaScript import extraction, TypeScript import extraction, unsupported language behavior (Go, SQL, etc.), relationship provenance, stable node identities, stable edge identities, repeated graph build idempotence, added file synchronization, deleted file synchronization, changed relationship synchronization, malformed source code handling, binary file handling, path boundary safety, empty project behavior, and mixed-language projects.
- **Live Local Test**: Scanned current repository (23 files indexed into `.buildcoach/state.db` and full project graph generated in ~215ms).

---

## Known Limitations
- **Language Relationship Extraction Scope**: In M2, import extraction is implemented for Python, JavaScript, and TypeScript without full compiler infrastructure. Other languages (`.go`, `.rs`, `.sql`, `.md`, etc.) are captured as `FILE` nodes with `extraction_supported=False` and zero hallucinated edges.
- **Subfolder-level `.gitignore` files**: In M1/M2, `IgnoreFilter` loads the root `.gitignore` and default exclusions. Nested `.gitignore` files in subdirectories are not loaded during traversal (only root-level rules and hierarchical propagation apply).
- **Repository Scale**: Very large codebases (e.g. 100,000+ files) will benefit from shallow scanning or subfolder scoping. Default exclusions eliminate `node_modules`, `.git`, `venv`, etc. Deletions in SQLite are batched in chunks of 500 to protect against variable limits.

---

## Known Risks
1. **Repository Scale**: Very large codebases (e.g. 100,000+ files) will require shallow scanning or subfolder scoping. Default exclusions currently eliminate `node_modules`, `.git`, `venv`, etc.
2. **Symlink Loops**: Uncontrolled symlinks outside project boundary are skipped via `Path.resolve().relative_to(root)`.

---

## Next Milestone
**Milestone 3: Git + Development Context**
- Capture working-tree changes, staged/unstaged diffs, and classify file mutations.
- Extract evidence records for Git observations with freshness and confidence metrics.
