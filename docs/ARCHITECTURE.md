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

## 4. Context Pipeline

All project data routed to an external AI provider must traverse this deterministic pipeline:

```
[ RAW PROJECT DATA ]
        ↓
[ NORMALIZE ]              Strip non-printable characters, unify line endings (LF), resolve canonical paths
        ↓
[ RELEVANCE FILTER ]       Filter out lockfiles, build artifacts, test binaries, and unmodified files
        ↓
[ SECRET DETECTION ]       Scan for high-entropy tokens, private keys, API keys, password literals
        ↓
[ REDACTION ]              Replace detected secrets with [REDACTED_API_KEY] or deterministic tokens
        ↓
[ CONTEXT COMPRESSION ]    Trim boilerplate; target budget of 3,000–6,000 tokens for rapid evaluation
        ↓
[ AI GATEWAY ]             Enforce timeout, retry policy, error fallbacks, and model abstractions
        ↓
[ MODEL RESPONSE ]         Receive raw response
        ↓
[ VALIDATION ]             Validate structured JSON schema, enforce claim classifications, reject hallucinations
        ↓
[ USER INTERFACE ]         Render verified, evidence-grounded insights to the developer
```

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

## 6. AI Gateway & Provider Abstraction

The system must not be hardcoded to any single LLM provider. The backend interacts through an abstract interface:

```python
class AIProvider(ABC):
    @abstractmethod
    def explain_change(self, context_packet: ContextPacket) -> ChangeExplanation:
        """Summarizes changes and provides grounded evidence."""
        pass

    @abstractmethod
    def evaluate_comprehension(self, question: str, user_answer: str, context: ContextPacket) -> ComprehensionEvaluation:
        """Evaluates human understanding against project evidence."""
        pass

    @abstractmethod
    def generate_viva_questions(self, project_summary: ProjectContext, difficulty: DifficultyLevel) -> list[VivaQuestion]:
        """Generates project-specific viva defence questions."""
        pass
```

- V1 implements a single provider (e.g. Gemini via standard HTTP/SDK), but all internal modules only reference `AIProvider`.
- Handles timeouts, rate limits, network outages, and malformed model responses gracefully without crashing local operations.

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
