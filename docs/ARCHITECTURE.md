# Architecture Specification: AI Build Coach

## 1. System Overview & The 4-Layer Model

The system is structured as an **Evidence-Backed Human Project Model** composed of four decoupled layers:

```
+-------------------------------------------------------------------------+
|                        AI BUILD COACH ARCHITECTURE                      |
+-------------------------------------------------------------------------+
| Layer 1: PROJECT GRAPH                                                  |
| - Files, directories, modules, components, routes, APIs, dependencies   |
| - Answers: "What exists?"                                               |
+-------------------------------------------------------------------------+
| Layer 2: EVIDENCE GRAPH                                                  |
| - Working tree diffs, Git status, test runs, diagnostics, provenance     |
| - Answers: "What proves it?"                                            |
+-------------------------------------------------------------------------+
| Layer 3: HUMAN KNOWLEDGE MODEL                                          |
| - Tracked concepts, user explanations, evaluations, identified gaps     |
| - Answers: "What does the human understand?"                            |
+-------------------------------------------------------------------------+
| Layer 4: GUIDANCE ENGINE                                                |
| - Change summarizer, Viva question generator, comprehension verifier    |
| - Answers: "What should the human do / learn next?"                     |
+-------------------------------------------------------------------------+
```

### Layer 1: Project Graph Specification

The Project Graph is the deterministic, evidence-backed foundation answering *"What exists in this project, and how are the parts related?"*. It is constructed passively without executing untrusted project code.

#### 1. Node Types
- **`PROJECT`**: The top-level root node representing the repository workspace.
- **`DIRECTORY`**: Directory hierarchies extracted from file paths (`dir:{rel_path}`).
- **`FILE`**: Individual tracked source, config, asset, or binary files (`file:{rel_path}`).
- **`MODULE`**: External packages or unmapped library modules referenced by source files (`module:{pkg_name}`).

#### 2. Edge Types
- **`CONTAINS`**: Hierarchical structural containment (Project contains root files/dirs; directories contain child directories/files).
- **`IMPORTS`**: Source file dependencies (a file imports another file or external module).
- **`REFERENCES`**: Explicit citations or references between artifacts.

#### 3. Provenance Model
Every relationship must originate from deterministic project observations, never AI inference:
- **`source_file`**: Relative path of the declaring file.
- **`line_number`**: Exact line number where the relationship appears.
- **`raw_statement`**: Verbatim source line (e.g. `import { useState } from 'react'`).
- **`source_type`**: Parsing mechanism (`"ast"` for Python, `"regex"` for JS/TS, `"filesystem"` for directory structure).
- **`confidence`**: Degree of determinism (`"HIGH"` for verified AST/regex parsing).

#### 4. Synchronization & Storage
- Stored relationally in SQLite (`.buildcoach/state.db`) using tables `graph_nodes` and `graph_edges` managed by versioned migrations (`migration_v2`).
- Upon rescanning, file changes and removals are reconciled via set difference: deleted files have their nodes and associated edges automatically pruned, preventing stale graph relationships.
- Idempotent and deterministic: repeated scans on identical workspaces produce identical node and edge IDs and ordering.

#### 5. Limitations
- Deterministic relationship extraction in M2 is scoped to Python (`ast.parse`) and JavaScript/TypeScript (regex tokenization).
- Unsupported file types (`.go`, `.rs`, `.sql`, `.md`, binary assets, etc.) are indexed as `FILE` nodes with `extraction_supported=False`; no edges are guessed.

### Layer 2: Development Context (Change Evidence Layer)

The Development Context layer is the deterministic, read-only inspection engine answering *"What is changing in the project right now?"*. It operates with uncommitted changes without requiring commits.

#### 1. Context Priority Hierarchy
1. **Working-Tree State**: Direct inspection of untracked files and working-tree modifications.
2. **Git Status (`git status --porcelain=v1 -uall`)**: Deterministic porcelain status codes for staged, unstaged, untracked, deleted, and renamed files.
3. **Unstaged Diff (`git diff --no-color -p -U3`)**: File-by-file hunks and line ranges between index and worktree.
4. **Staged Diff (`git diff --cached --no-color -p -U3`)**: File-by-file hunks and line ranges between HEAD and index.
5. **Recent Commit Information (`git log -1`)**: Baseline commit metadata for context.

#### 2. Domain Entities
- **`ChangeSet`**: A deterministic snapshot of current working tree changes, head commit, and file mutation summaries.
- **`FileChange`**: Individual file mutation record supporting `ADDED`, `MODIFIED`, `DELETED`, and `RENAMED` changes, tracking line counts, line ranges, and staged/untracked flags.
- **`DiffHunk`**: Structured hunk record preserving old/new line ranges, headers, and diff text.
- **`EvidenceRecord`**: Source-backed factual observation (`GIT_STATUS`, `GIT_DIFF`, `WORKING_TREE`, `RECENT_COMMIT`) with zero inference of developer intent.

#### 3. Storage & Schema
- Persisted relationally in SQLite (`.buildcoach/state.db`) via `migration_v3` across tables `change_sets`, `file_changes`, `diff_hunks`, and `evidence_records` with cascading foreign keys and indexes.
- Idempotent and deterministic: SHA-256 IDs for ChangeSets, FileChanges, DiffHunks, and EvidenceRecords.

---

## 2. Technology Strategy

To guarantee determinism, auditability, and speed, the architectural stack is phased strictly:

### Foundational Stack (Milestones 0–8)
- **Language Runtime**: Python 3.11+
- **Data Modeling & Validation**: `pydantic` v2
- **Persistence**: SQLite 3 (`sqlite3` standard library with explicit migration scripts)
- **Testing**: `pytest`
- **Utility / Standard Library**: `pathlib`, `hashlib`, `re`, `subprocess` (strictly controlled for Git read operations only)

### Explicitly Excluded from Foundational Milestones
The following technologies are **strictly forbidden** until their scheduled milestones:
- No web/frontend frameworks: React, Next.js, Vue, Tailwind, TypeScript
- No API servers: FastAPI, Flask, Django (CLI/embedded engine first)
- No heavyweight databases: PostgreSQL, MySQL, Redis, MongoDB
- No container/cloud orchestration: Docker, Kubernetes, Helm, Cloud Queues
- No vector databases: ChromaDB, Pinecone, Qdrant (local SQLite relational + deterministic graphs first)

---

## 3. Context Architecture & Priority Hierarchy

When constructing context packets for the AI Gateway, context sources are prioritized deterministically:

1. **Working-Tree / File Changes** (highest priority: uncommitted edits represent what the developer is doing *now*)
2. **Git Status & Git Diff** (staged and unstaged changes)
3. **IDE State** (active file, open buffers, cursor context)
4. **Terminal Output** (recent compilation / test logs)
5. **Diagnostics & Test Results** (linter output, test failure traces)
6. **Browser Evidence** (client-side runtime logs if permissioned)
7. **Agent Events** (BCAP events if available)
8. **Commit History** (historical commits provide background context, not immediate state)

---

## 4. Context Pipeline & Context Engine Specification (Milestone 4)

The Context Engine sits between the deterministic project/evidence layers (M1–M3) and the future AI Gateway (M5). It transforms raw project information into a structured, safe, provenance-preserving `ContextPacket`.

Context selection is 100% deterministic and evidence-driven:
- No LLMs are called to decide what context is relevant.
- No model-generated summaries are used for compression.
- No developer intent or architectural rationale is invented.

### 4.1 Core Pipeline Stages

```
RAW DATA
    ↓
NORMALIZE
    ↓
RELEVANCE FILTER
    ↓
SECRET DETECTION
    ↓
REDACTION
    ↓
COMPRESS
    ↓
CONTEXT PACKET
```

1. **RAW DATA**: Ingests `project_id`, `ChangeSet`, `ProjectGraph`, explicit `target_files`/`target_symbols`, and `budget_tokens`.
2. **NORMALIZE**: Canonicalizes paths to forward slashes, trims whitespace, standardizes line ranges `(start, end)`, and validates inputs.
3. **RELEVANCE FILTER**: Deterministically ranks candidates according to explicit scoring tiers and discards candidates with score <= 0.0 (unrelated files).
4. **SECRET DETECTION**: Evaluates candidate contents with precompiled regex patterns to detect API keys, bearer tokens, passwords, private keys, and `.env` credentials before any compression.
5. **REDACTION**: Replaces sensitive values with deterministic placeholders (`[REDACTED]`, `[REDACTED_PRIVATE_KEY]`), records counts and categories in `redaction_summary`, and never stores raw secret values in memory, logs, or SQLite.
6. **COMPRESS**: Deduplicates identical items, normalizes whitespace (stripping trailing spaces, collapsing consecutive blank lines), prunes lines in oversized files, and applies token budgeting.
7. **CONTEXT PACKET**: Assembles the sorted `ContextItem` list, computes a deterministic SHA-256 `id`, records `token_estimate` and `truncation_status`, and optionally persists and caches in SQLite.

### 4.2 Deterministic Relevance Model & Scoring Tiers

Every item evaluated by the relevance filter receives a transparent, documented score:

| Candidate Category | Relevance Score | Description / Criteria |
|---|---|---|
| Directly Changed File | **100.0** | Files in `ChangeSet.file_changes` (`ADDED`, `MODIFIED`, `DELETED`, `RENAMED`) |
| Changed Diff Hunk | **95.0** | Individual diff hunks linked to changed files with exact line ranges |
| Explicit Target File | **90.0** | Files specifically requested via `ContextRequest.target_files` |
| Direct Dependency | **70.0** | Files directly imported by changed files (`ProjectGraph` out-edges `IMPORTS`) |
| Direct Dependent | **60.0** | Files directly importing changed files (`ProjectGraph` in-edges `IMPORTS`) |
| Related Test File | **50.0** | Test files matching changed stems (`test_<stem>`, `<stem>_test`, etc.) or importing changed files |
| Project Configuration | **40.0** | Known build/config files (`pyproject.toml`, `package.json`, `tsconfig.json`, etc.) |
| Change Evidence | **30.0** | Deterministic `EvidenceRecord`s (`GIT_STATUS`, `GIT_DIFF`, `WORKING_TREE`) |
| Supporting Graph Node | **20.0** | Parent directories and related module nodes |
| Unrelated Project File | **0.0** | Files with no direct connection to changed files (**Filtered Out**) |

### 4.3 Deterministic Item Ordering

Context items are ordered deterministically using the following canonical order rules:
1. Current change summary (`source_type = CHANGESET`, summary)
2. Changed files (`source_type = CHANGESET` or `FILE`)
3. Changed diff hunks (`source_type = DIFF`)
4. Direct dependencies/dependents (`source_type = PROJECT_GRAPH`)
5. Relevant tests (`source_type = TEST`)
6. Relevant evidence (`source_type = EVIDENCE` or `GIT`)
7. Explicitly requested context (`source_type = FILE` where reason contains "requested")
8. Lower-priority supporting context

Tie-breaking order key: `(source_order_rank, -relevance_score, file_path, line_start, item_id)`.

### 4.4 Size Budgeting & Truncation Rules

- **Engineering Target**: 3,000–6,000 tokens (default: 4,000 tokens).
- **Token Estimation**: Heuristic estimate using `max(1, len(text) // 4)` (approx 4 chars per token).
- **Budget Rules**:
  1. Critical items (relevance score >= 90.0) are preserved.
  2. Lower-relevance items are dropped if adding them exceeds `budget_tokens`.
  3. Oversized files with > 300 lines retain the first 150 lines and last 50 lines with an explicit pruning notice.
  4. Binary files are never included as raw bytes; `[Binary file content omitted: <size> bytes]` is included.
  5. The packet explicitly records `truncation_status` as `"NONE"` or `"TRUNCATED"`.

### 4.5 Domain Entities & SQLite Schema (`migration_v4`)

- **`ContextItem`**: `item_id`, `source_type`, `source_reference`, `file_path`, `line_start`, `line_end`, `evidence_refs`, `relevance_reason`, `relevance_score`, `redacted`, `content`.
- **`ContextPacket`**: `id`, `project_id`, `purpose`, `generated_at`, `packet_version`, `items`, `evidence_refs`, `redaction_summary`, `token_estimate`, `truncation_status`.
- **`ContextRequest`**: `project_id`, `change_set`, `graph`, `purpose`, `target_files`, `target_symbols`, `budget_tokens`.
- **SQLite Tables**: `context_requests`, `context_packets`, `context_items` in `.buildcoach/state.db` with cascading foreign keys and deterministic caching.


---

## 5. Local-First Architecture & Directory Layout

AI Build Coach operates locally on the user's filesystem. Local state is stored in `.buildcoach/` inside the project root:

```
.buildcoach/
├── state.db          # SQLite: Project files, hashes, Git state, schema migrations
├── knowledge.db      # SQLite: Evaluated concepts, user answers, knowledge gap history
├── config.json       # Non-secret preferences, provider selection, scanner exclusions
├── cache/            # Cached file summaries, AST metadata (invalidated by file hash)
└── logs/             # Operational audit logs (secrets strictly scrubbed)
```

### Credential Storage Policy
- Raw API keys must **never** be saved in `.buildcoach/config.json`.
- Credentials must be retrieved from environment variables (e.g., `GEMINI_API_KEY`, `OPENAI_API_KEY`) or system keyring/secure credential storage.

---

## 6. AI Gateway & Provider Abstraction (Milestone 5)

The AI Gateway provides a decoupled, provider-independent integration layer that safely consumes the M4 `ContextPacket` and coordinates external AI reasoning with zero-trust security and anti-hallucination validation.

```
+-------------------------------------------------------------------------------+
|                                AI GATEWAY PIPELINE                            |
+-------------------------------------------------------------------------------+
|  ContextPacket (from M4)                                                      |
|       ↓                                                                       |
|  ConsentManager.validate_consent() [6-tuple cryptographic token binding]       |
|       ↓                                                                       |
|  CredentialStore.get_gemini_api_key() [BYOK env vars, config.json forbidden] |
|       ↓                                                                       |
|  Zero-Trust Prompt Fencer [<untrusted_project_evidence>, anti-injection sys]  |
|       ↓                                                                       |
|  GeminiInteractionsAdapter [Gemini 3.8 Flash via stdlib urllib.request]       |
|       ↓                                                                       |
|  Layered Response Parser [extracts JSON matching ExplanationResponse schema]  |
|       ↓                                                                       |
|  EvidenceValidator [canonical ContextItem.item_id check, coerce to UNKNOWN]  |
|       ↓                                                                       |
|  SQLite Audit Logging [gateway_runs table in .buildcoach/state.db]            |
|       ↓                                                                       |
|  ValidatedGatewayResult                                                       |
+-------------------------------------------------------------------------------+
```

### Core Components
1. **`AIGateway` Orchestrator**: Provider-agnostic coordinator executing the end-to-end reasoning pipeline.
2. **`AIProviderAdapter`**: Abstract base contract (`complete_interaction(...)`) allowing future providers without modifying gateway logic.
3. **`GeminiInteractionsAdapter`**: Concrete transport for Google Gemini 3.8 Flash (`gemini-3.8-flash`) implemented using standard library `urllib.request` (zero third-party SDK dependencies).
   - Strict payload structure: `model`, `system_instruction` (plain string), `input` (plain string), `response_format` (`type="text"`, `mime_type="application/json"`, `schema`), `generation_config={"thinking_level": "low"}`, `store=False`.
   - Never retries ambiguous network timeouts on POST requests.
   - Exponential backoff retry on transient HTTP 429 and HTTP 503 errors (up to 2 retries).
4. **`ConsentManager`**: Enforces explicit human consent prior to any external context transmission.
   - Generates inspectable `TransmissionPreview`.
   - Issues and verifies `ConsentToken` cryptographically bound to the 6-tuple `(user_acknowledged, packet_id, packet_hash, provider, model, expires_at)`.
5. **`CredentialStore`**: Bring-Your-Own-Key (BYOK) manager resolving `GEMINI_API_KEY` / `BUILDCOACH_GEMINI_API_KEY` from environment variables. Actively scans `.buildcoach/config.json` and raises `SecurityConfigurationError` if sensitive credentials appear on disk.
6. **`EvidenceValidator`**: Anti-hallucination verification engine.
   - Validates claim citations against the canonical `ContextItem.item_id` namespace.
   - Unsupported factual observations are never silently rewritten into inferences; they are strictly marked ungrounded and coerced to `UNKNOWN` or rejected.
7. **Audit Persistence (`gateway_runs`)**: Local SQLite record capturing run metrics, latency, token usage, and grounding ratios without creating brittle foreign keys on ephemeral context packets.

---

## 7. Evidence Object Model & Provenance

Every statement presented to the user must be backed by an evidence model:

```json
{
  "statement": "middleware.py intercepts incoming requests to validate JWT tokens",
  "claim_type": "OBSERVATION",
  "evidence_refs": [
    "file:backend/middleware.py:12-28",
    "import:backend/routes/auth.py:4"
  ],
  "confidence": "HIGH",
  "freshness": "CURRENT",
  "source": "working_tree_scan"
}
```

### Claim Classification Rules
- **`OBSERVATION`**: Direct fact extracted from files, AST, or Git diff.
- **`INFERENCE`**: Deductive conclusion based on multiple observations.
- **`RECOMMENDATION`**: Suggested verification step for the developer.
- **`UNKNOWN`**: Explicit marker for missing context or undocumented rationale.

---

## 8. Anti-Hallucination & Provenance Rules

1. If code does not contain comments or documentation explaining *why* an architectural choice was made, the system must report `UNKNOWN` for rationale.
2. Inferences must be clearly labeled and segregated from observations.
3. Model outputs that claim file modifications or route definitions not verified by the local project scanner must be rejected during validation.

---

## 9. Repository Structure

```
ai-build-coach/
├── docs/
│   ├── PRODUCT_SPEC.md
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── MILESTONES.md
│   ├── DECISIONS.md
│   └── SYSTEM_STATE.md
│
├── backend/
│   ├── domain/           # Core models (ProjectFile, GitState, Claim, Evidence, Concept)
│   ├── project_model/    # Scanner, file hasher, directory walker, .gitignore filter
│   ├── evidence/         # Git status, diff parser, AST analyzer, provenance tracker
│   ├── knowledge/        # Concept tracker, gap detector, evaluation ledger
│   ├── guidance/         # Change summarizer, Viva question generator, teaching engine
│   ├── context/          # Context pipeline, secret detector, redactor, compressor
│   └── ai_gateway/       # Abstract provider interface, Gemini adapter, response validator
│
├── tests/
│   ├── unit/             # Scanner, diff parser, redactor, model tests
│   ├── integration/      # Scanner -> SQLite, Context -> Redactor -> Gateway
│   ├── fixtures/         # Synthetic repos (python, react, broken, with-secrets)
│   └── e2e/              # End-to-end workflow verification
│
├── scripts/              # Developer scripts (db setup, test runner)
├── extension/vscode/     # Future VS Code extension (Milestone 9)
├── .buildcoach/          # Local runtime directory (ignored by git)
├── .gitignore
├── README.md
└── pyproject.toml
```
