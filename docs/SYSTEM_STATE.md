# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 9: Unified Engine CLI & One-Shot Headless JSON Interface**

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
  - M5: `ClaimType`, `StructuredClaim`, `ExplanationResponse`, `ConsentToken`, `TransmissionPreview`, `RawInteractionResponse`, `ValidatedGatewayResult`.
  - M6: `IntentEpistemicStatus`, `ChangeCategory`, `DeterministicFileChange`, `WhatChangedSection`, `IntentRationale`, `WhySection`, `LocalEvidenceTrace`, `EvidenceSection`, `ConceptToUnderstand`, `CanIExplainThisPrompt`, `ChangeExplanationPreview`, `UnderstandChangeResult`.
  - M7: `ComprehensionRating`, `ComprehensionDimension`, `GapSeverity`, `RunStatus`, `KnowledgeGap`, `DimensionEvaluation`, `StudentExplanationSubmission`, `ReverificationPrompt`, `TargetedTeaching`, `ComprehensionEvaluationResult`, `ComprehensionRunRecord`.
  - M8: `VivaDifficulty`, `VivaCategory`, `VivaRating`, `VivaGapSeverity`, `VivaDefenceReadiness`, `VivaSessionStatus`, `VivaSessionMode`, `VivaQuestion`, `VivaAnswerSubmission`, `VivaKnowledgeGap`, `VivaTurnEvaluation`, `CategoryMastery`, `VivaDefenceReport`, `VivaSessionRecord`.
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Migration v2: `graph_nodes` and `graph_edges` tables with cascading foreign keys and indexes.
  - Migration v3: `change_sets`, `file_changes`, `diff_hunks`, and `evidence_records` tables with cascading foreign keys and indexes.
  - Migration v4: `context_requests`, `context_packets`, and `context_items` tables with composite primary keys, foreign keys, and indexes.
  - Migration v5: `gateway_runs` minimal local audit table tracking run ID, packet ID, provider, model, tokens, latency, claim counts, and timestamp.
  - Migration v6: `understand_change_runs` minimal local audit table tracking run ID, project ID, changeset ID, packet ID, gateway run ID, primary category, files changed count, grounding ratio, and timestamp.
  - Migration v7: `comprehension_runs` minimal local operational table tracking run ID, project ID, changeset ID, packet ID, prompt ID, attempt number, run status, overall state, gap count, started at, and created at.
  - Migration v8: `viva_sessions`, `viva_questions`, `viva_turns`, and `viva_reports` tables with foreign keys and unique constraints.
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
- AI Gateway (`AIGateway`, `backend.ai_gateway`):
  - Abstract provider interface (`AIProviderAdapter`) decoupled from concrete transports.
  - Google Gemini 3.8 Flash (`gemini-3.8-flash`) transport (`GeminiInteractionsAdapter`) using standard library `urllib.request` (zero third-party SDK dependencies).
  - Gemini Interactions API request structure: `model="gemini-3.8-flash"`, plain string `system_instruction`, plain string `input`, `response_format`, `generation_config={"thinking_level": "low"}`, `store=False`.
  - Zero retries on ambiguous POST timeouts; exponential backoff retry on transient HTTP 429/503.
  - Explicit user consent management (`ConsentManager`) binding `ConsentToken` to the 6-tuple `(user_acknowledged, packet_id, packet_hash, provider, model, expires_at)`.
  - BYOK credential handling (`CredentialStore`) enforcing environment variables and rejecting sensitive keys in `config.json`.
  - Zero-trust prompt fencing wrapping evidence in `<untrusted_project_evidence>` with anti-injection instructions.
  - Strict evidence grounding (`EvidenceValidator`): enforces canonical `ContextItem.item_id` references; unsupported observations are coerced to `UNKNOWN` (never silently converted to `INFERENCE`).
  - Local audit logging in SQLite table `gateway_runs`.
- Milestone 8 Viva Defence Engine (`backend.viva`):
  - `ProjectArchitecturalIndex` mapping repository files to 9 architectural categories deterministically.
  - Multi-tier viva question generator (`generate_viva_question`) for discrete difficulty tiers (`EASY`, `MEDIUM`, `HARD`, `DEEP`) and adaptive follow-ups using targeted M4 ContextPackets (4,000 tokens) and frozen M5 AIGateway.
  - Evaluator (`evaluate_viva_answer`) with fast-path detection, anti-injection XML fencing, tagged claim protocol (`VIVA_EVAL`, `VIVA_GAP`, `VIVA_FOLLOWUP`), strict project-grounding check (trivia capped at `PARTIAL`), intellectual honesty recognition (undocumented rationale rated `STRONG`), and zero persistence of raw student answers.
  - Session state machine (`start_viva_session`, `submit_viva_answer_and_step`, `recover_stale_session_if_needed`) with difficulty promotion/demotion, turn progression guards, bounded limits (`MAX_BASE_QUESTIONS = 5`, `MAX_FOLLOWUP_PER_BASE = 1`, `MAX_FOLLOWUPS_PER_SESSION = 3`, `MAX_TOTAL_TURNS = 8`), and stale recovery (>180s) recording turn as `UNKNOWN` and session as `FAILED`.
  - Comprehensive defence reporter (`compile_viva_report`) strictly distinguishing `NOT_EVALUATED` from `UNKNOWN`, with deterministic readiness assessment (`DEFENCE_READY`, `NEEDS_PREPARATION`, `SUBSTANTIAL_GAPS`, `INCOMPLETE`) and full report reconstructability.

---

## What Does Not Work (Intentional Scope Boundaries)
- User-facing UI and VS Code extension (scheduled for Milestones 9–10).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 163 automated tests passing in `tests/`:
  - `test_viva.py` (24 tests):
    1. Architectural index mapping across all categories
    2. Session creation and first question generation
    3. Fast-path empty and repetitive gibberish bypass without AI gateway calls
    4. XML escaping and prompt-injection defense
    5. Strict project-grounding cap (textbook trivia cannot exceed PARTIAL)
    6. Intellectual honesty reward for undocumented rationale
    7. Tagged claim parsing for evaluation, gaps, and follow-ups
    8. Groundedness validation with ungrounded claim detection
    9. Difficulty promotion and demotion boundaries
    10. Adaptive follow-up trigger and limits (MAX_FOLLOWUP_PER_BASE = 1)
    11. Session bounds and completion with report generation
    12. Zero persistence of student answer text in SQLite
    13. Stale EVALUATING crash recovery (>180s) recording turn as UNKNOWN and returning to AWAITING_ANSWER
    14. Distinction between NOT_EVALUATED and UNKNOWN in viva reports
    15. Invalid turn progression and mismatch errors
    16. In-flight EVALUATING session concurrency guard
    17. CATEGORY_FOCUS mode completion and readiness
    18. Unhandled gateway failure recovery with UNKNOWN turn recording
    19. MAX_FOLLOWUPS_PER_SESSION cap enforcement
    20. DEEP difficulty ceiling prevents promotion past DEEP
    21. Regression test: 8 STRONG + 1 UNKNOWN categories never produce DEFENCE_READY
    22. AWAITING_ANSWER restart recovery without regenerating active question
    23. MAX_FOLLOWUP_PER_BASE hard stop on weak follow-up answers
    24. Atomic concurrent answer submission conditional update
  - `test_cli.py` (25 tests):
    1. Version output `--version` (package metadata & pyproject.toml fallback)
    2. Python module entrypoint `python -m backend.cli`
    3. Global and subparser `--help`
    4. Flexible `--json` flag position (leading, trailing, subcommand)
    5. Automatic workspace root and project discovery
    6. Clean git repository status inspection
    7. Dirty working tree status inspection
    8. Structured JSON status envelope
    9. Passive project scanner execution via CLI
    10. Interactive preview of uncommitted changes
    11. Clean tree handling in change explanation preview
    12. Structured JSON change explanation preview
    13. Explanation synthesis with user consent token
    14. Safe abortion on consent rejection
    15. Interactive stdin explanation submission & rating
    16. Headless JSON explanation submission & evaluation
    17. Interactive viva session initialization
    18. Headless JSON viva session initialization
    19. Adaptive turn progression via `--answer-stdin`
    20. Fast-path empty/gibberish turn handling
    21. Final comprehensive viva report generation
    22. Invariant test: Zero persistence of student answers in SQLite
    23. Pure stdout isolation in `--json` mode
    24. Standardized error envelope format on failure
    25. CLI invariant: rejection of `--answer` argument flag
  - `test_comprehension.py` (17 tests): Canonical M6 context binding, fast-path, 4 dimensions, grounding, crash recovery, teaching.
  - `test_understand_change.py` (12 tests): File and changeset categorization, physical truth immutability, criticality, epistemic classification, local snippets, prompt boundary, fallback, consent.
  - `test_ai_gateway.py` (23 tests): Consent, BYOK credentials, Interactions API payload shape, timeouts, retries, grounding.
  - `test_context_engine.py` (32 tests): Pipeline, scoring, secret detection, redaction, budget truncation, compression, caching.
  - `test_context.py` (18 tests): ChangeSets, FileChanges, DiffHunks, EvidenceRecords, Git status/diff parsing, determinism, syncing.
  - `test_db.py` (5 tests): Migrations v1–v8, CRUD, syncing, large file sets.
  - `test_git_detector.py` (3 tests): Non-git, worktree, empty and dirty repos.
  - `test_gitignore.py` (3 tests): Root-anchored rules, wildcards, negations, boundaries.
  - `test_graph.py` (14 tests): Nodes, edges, AST & regex imports, multiline statements, sync lifecycle.
  - `test_scanner.py` (12 tests): Root detection, binary files, large files (>1MB), boundary enforcement, permissions.
- **Live Local Test**: 188 passed in 58.07s.

---

## Known Limitations
- **Secret Detection Scope**: Detection relies on deterministic regular expressions targeting high-entropy keys, bearer tokens, passwords, private keys, and environment variables. It does not claim 100% discovery of novel, custom-encoded, or obfuscated secrets.
- **Language Relationship Extraction Scope**: Import extraction covers Python, JavaScript, and TypeScript without full compiler infrastructure. Other languages (`.go`, `.rs`, `.sql`, `.md`, etc.) are captured as `FILE` nodes with `extraction_supported=False` and zero hallucinated edges.
- **Token Estimation Heuristic**: Character-based heuristic (`max(1, len(text) // 4)`) provides an engineering estimate, not exact tokenizer parity for every third-party LLM.

---

## Next Milestone
**Milestone 10: V1 Polish, Multi-Repo Testing & Validation**
- Comprehensive multi-repo test suite (Python, TypeScript, React, Java).
- Performance benchmarking (local scanning < 1s, AI response < 10s).
- Systematic verification against validation hypotheses (H1–H7).

