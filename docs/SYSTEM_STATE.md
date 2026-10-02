# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 1: Local Project Model**

## Status
**Completed / Ready for Audit Gate Review**

---

## What Works
- Baseline project architecture and contracts (`docs/`).
- Python package setup (`pyproject.toml`) with Pydantic and pytest.
- Pydantic domain models:
  - `Project`, `ProjectFile`, `GitState`, `SchemaVersion`, `ProjectSummary`, `ScanResult`.
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Upserting projects, syncing file changes (detecting updates/deletions), recording Git states, and tracking scan durations.
- Safe, read-only Git state detector (`detect_git_state`):
  - Inspects branch name, HEAD commit hash, dirty flag, untracked/modified/staged file counts.
  - Safe fallback for non-git directories and empty repositories.
  - Zero state-mutating Git commands executed.
- Gitignore & Exclusion Filter (`IgnoreFilter`):
  - Built-in default exclusions (`.git`, `node_modules`, `venv`, `__pycache__`, `.buildcoach`, etc.).
  - Parses `.gitignore` files including wildcards, directory-only patterns (`folder/`), and negations (`!file`).
  - Hierarchical directory ignore propagation.
- Passive Local Project Scanner (`ProjectScanner`):
  - Project root detection upward traversal.
  - Safe file inspection strictly confined to workspace boundaries.
  - Streaming SHA-256 computation in 64 KB chunks.
  - 8 KB null-byte inspection for binary file detection.
  - 1 MB file size threshold tagging for large files (`is_large`).
  - Alphabetically sorted file walking for 100% deterministic output.
  - Graceful handling of permission-denied files and non-UTF-8 byte sequences.

---

## What Does Not Work (Intentional Scope Boundaries)
- Project Graph AST relationship extraction (scheduled for Milestone 2).
- Diff parsing and working-tree change classification (scheduled for Milestone 3).
- Context compression and secret redaction engine (scheduled for Milestone 4).
- AI Gateway provider integrations (scheduled for Milestone 5).
- User-facing workflows (scheduled for Milestones 6–8).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 22 automated tests passing in `tests/`:
  - `test_db.py`: Schema migrations initialization, incremental migration to v2, project/file CRUD & sync, git state recording, large file-set synchronization (2,500+ records) with batched parameterized deletion.
  - `test_git_detector.py`: Non-git directory handling, empty repository, untracked files, clean committed repository, git worktree `.git` file pointer support.
  - `test_gitignore.py`: Default exclusions, custom `.gitignore` with wildcards, negations, directory-only rules, root-anchored leading-slash rules (`/build`, `/secrets`, `/temp/*.log`), and out-of-bounds rejection.
  - `test_scanner.py`: Root marker detection with directory, file, nested file, nonexistent path inputs, empty project scan, nested projects, ignored directories, large file detection (>1MB), binary file detection (null bytes), repeated scan idempotence (inserts, updates, deletes), path traversal boundary enforcement, permission error handling, non-UTF-8 byte handling, and inaccessible directory traversal (`os.walk` `onerror`).
- **Live Local Test**: Scanned current repository (22 files indexed into `.buildcoach/state.db` in ~197ms).

---

## Known Limitations
- **Subfolder-level `.gitignore` files**: In M1, `IgnoreFilter` loads the root `.gitignore` and default exclusions. Nested `.gitignore` files in subdirectories are not loaded during traversal (only root-level rules and hierarchical propagation apply).
- **Repository Scale**: Very large codebases (e.g. 100,000+ files) will benefit from shallow scanning or subfolder scoping. Default exclusions eliminate `node_modules`, `.git`, `venv`, etc. Deletions in SQLite are batched in chunks of 500 to protect against variable limits.

---

## Known Risks
1. **Repository Scale**: Very large codebases (e.g. 100,000+ files) will require shallow scanning or subfolder scoping. Default exclusions currently eliminate `node_modules`, `.git`, `venv`, etc.
2. **Symlink Loops**: Uncontrolled symlinks outside project boundary are skipped via `Path.resolve().relative_to(root)`.

---

## Next Milestone
**Milestone 2: Project Brain Foundation (Project Graph)**
- Construct the first deterministic Project Graph representing files, modules, features, dependencies, and route relationships with explicit provenance.
