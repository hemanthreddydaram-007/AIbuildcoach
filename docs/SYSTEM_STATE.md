# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 3: Development Context (Change Evidence Layer)**

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
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Migration v2: `graph_nodes` and `graph_edges` tables with cascading foreign keys and indexes.
  - Migration v3: `change_sets`, `file_changes`, `diff_hunks`, and `evidence_records` tables with cascading foreign keys and indexes.
  - CRUD operations for projects, file syncing, Git states, scan runs, project graphs, and development context change sets.
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

---

## What Does Not Work (Intentional Scope Boundaries)
- Context compression and secret redaction engine (scheduled for Milestone 4).
- AI Gateway provider integrations (scheduled for Milestone 5).
- User-facing workflows (scheduled for Milestones 6–8).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 55 automated tests passing in `tests/`:
  - `test_context.py` (18 tests): Clean working tree, unstaged modified files, untracked/staged added files, deleted files, renamed files, multiple changed files, unstaged diff details, staged diff details, multiple non-contiguous hunks, binary files, empty diff, missing git repo, git command failure, deterministic ChangeSet IDs, deterministic Evidence IDs, repeated context collection & persistence, synchronization after file reverted, scanner integration with M3 change set.
  - `test_db.py` (5 tests): Schema migrations v1, v2, v3, project/file CRUD & sync, git state recording, large file-set synchronization (2,500+ records) with batched parameterized deletion.
  - `test_git_detector.py` (3 tests): Non-git directory handling, empty repository, untracked files, clean committed repository, git worktree `.git` file pointer support.
  - `test_gitignore.py` (3 tests): Default exclusions, custom `.gitignore` with wildcards, negations, directory-only rules, root-anchored leading-slash rules, and out-of-bounds rejection.
  - `test_graph.py` (14 tests): Project, Directory, File, Module nodes, CONTAINS relationships, Python AST imports, JS/TS single-line imports, TS type imports, multiline JS/TS import extraction with safe statement accumulation, unsupported languages, provenance, stable identities, repeated build idempotence, synchronization lifecycle, malformed files, binary files, path security, empty projects, mixed-language projects.
  - `test_scanner.py` (12 tests): Root marker detection, empty project scan, nested projects, ignored directories, large file detection (>1MB), binary file detection, repeated scan idempotence, path traversal boundary enforcement, permission error handling, non-UTF-8 byte handling, inaccessible directory traversal.
- **Live Local Test**: 55 passed in 22.66s.

---

## Known Limitations
- **Language Relationship Extraction Scope**: Import extraction covers Python, JavaScript, and TypeScript without full compiler infrastructure. Other languages (`.go`, `.rs`, `.sql`, `.md`, etc.) are captured as `FILE` nodes with `extraction_supported=False` and zero hallucinated edges.
- **Dynamic Imports with Variable Expressions**: Statically unknown expressions (e.g. `import(variable)`) are intentionally omitted from static relationship extraction.
- **Subfolder-level `.gitignore` files**: `IgnoreFilter` loads root `.gitignore` and default exclusions.
- **Repository Scale**: Very large codebases (e.g. 100,000+ files) will benefit from shallow scanning or subfolder scoping. Default exclusions eliminate `node_modules`, `.git`, `venv`, etc. Deletions in SQLite are batched in chunks of 500 to protect against variable limits.

---

## Next Milestone
**Milestone 4: Context Engine (Pipeline, Secret Redaction, Compression)**
- Implement deterministic context normalization and boilerplate filtering.
- Deterministic secret detection and redaction engine.
- Context compression budgeting (targeting 3,000–6,000 tokens).
