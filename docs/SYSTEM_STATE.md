# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 6: Understand What Changed (Workflow 1)**

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
- Deterministic SQLite database (`.buildcoach/state.db`):
  - Explicit schema versioning and migration framework (`schema_migrations`, `projects`, `files`, `git_states`, `scan_runs`).
  - Migration v2: `graph_nodes` and `graph_edges` tables with cascading foreign keys and indexes.
  - Migration v3: `change_sets`, `file_changes`, `diff_hunks`, and `evidence_records` tables with cascading foreign keys and indexes.
  - Migration v4: `context_requests`, `context_packets`, and `context_items` tables with composite primary keys, foreign keys, and indexes.
  - Migration v5: `gateway_runs` minimal local audit table tracking run ID, packet ID, provider, model, tokens, latency, claim counts, and timestamp.
  - Migration v6: `understand_change_runs` minimal local audit table tracking run ID, project ID, changeset ID, packet ID, gateway run ID, primary category, files changed count, grounding ratio, and timestamp.
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

---

## What Does Not Work (Intentional Scope Boundaries)
- Viva Question Generator / Comprehension Verification (scheduled for Milestones 6–8).
- User-facing UI and VS Code extension (scheduled for Milestones 9–10).
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- **Test Suite**: 122 automated tests passing in `tests/`:
  - `test_understand_change.py` (12 tests):
    1. Deterministic file and changeset categorization
    2. Clean working tree preview with zero token cost and zero AI calls
    3. Physical truth immutability (M3 file changes, counts, and types strictly preserved; supplementary AI narrative cannot alter physical reality)
    4. File criticality calculation (relevance_score >= 90.0 on any context item)
    5. Epistemic classification: EXPLICIT when actual explanatory comment/docstring exists
    6. Epistemic classification: INFERRED with explicit unknown gap when unannotated
    7. Verbatim local snippet resolution from ContextItem.content (never model text)
    8. Evidence-supported concept formulation and CanIExplainThis prompt boundary (zero evaluation)
    9. Provider-failure graceful fallback (M3 truth preserved, Why=UNKNOWN, CanIExplainThis is_available=False, zero invented AI content)
    10. Consent token enforcement
    11. Ungrounded and missing items in EvidenceSection
    12. SQLite audit persistence in `understand_change_runs`
  - `test_ai_gateway.py` (23 tests): Consent, BYOK credentials, Interactions API payload shape, timeouts, retries, grounding.
  - `test_context_engine.py` (32 tests): Pipeline, scoring, secret detection, redaction, budget truncation, compression, caching.
  - `test_context.py` (18 tests): ChangeSets, FileChanges, DiffHunks, EvidenceRecords, Git status/diff parsing, determinism, syncing.
  - `test_db.py` (5 tests): Migrations v1–v6, CRUD, syncing, large file sets.
  - `test_git_detector.py` (3 tests): Non-git, worktree, empty and dirty repos.
  - `test_gitignore.py` (3 tests): Root-anchored rules, wildcards, negations, boundaries.
  - `test_graph.py` (14 tests): Nodes, edges, AST & regex imports, multiline statements, sync lifecycle.
  - `test_scanner.py` (12 tests): Root detection, binary files, large files (>1MB), boundary enforcement, permissions.
- **Live Local Test**: 122 passed in 30.22s.

---

## Known Limitations
- **Secret Detection Scope**: Detection relies on deterministic regular expressions targeting high-entropy keys, bearer tokens, passwords, private keys, and environment variables. It does not claim 100% discovery of novel, custom-encoded, or obfuscated secrets.
- **Language Relationship Extraction Scope**: Import extraction covers Python, JavaScript, and TypeScript without full compiler infrastructure. Other languages (`.go`, `.rs`, `.sql`, `.md`, etc.) are captured as `FILE` nodes with `extraction_supported=False` and zero hallucinated edges.
- **Token Estimation Heuristic**: Character-based heuristic (`max(1, len(text) // 4)`) provides an engineering estimate, not exact tokenizer parity for every third-party LLM.

---

## Next Milestone
**Milestone 7: "Can I Explain This?" Comprehension Loop**
- Interactive comprehension question presentation.
- Human answer collection and multi-dimensional evaluation (Purpose, Mechanism, Failure Modes, Impact).
- Qualitative grading (`UNDERSTOOD`, `PARTIALLY UNDERSTOOD`, `NEEDS REVIEW`, `UNKNOWN`) without uncalibrated percentages.
- Knowledge gap detection and targeted follow-up question generation.

