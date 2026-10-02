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
