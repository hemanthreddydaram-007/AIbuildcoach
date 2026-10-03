# Milestone Roadmap: AI Build Coach

## 1. Development Discipline & Loop

Every milestone must strictly execute the following engineering cycle before progressing:

```
[ READ CURRENT STATE ]
        ↓
[ UNDERSTAND REQUIREMENT ]
        ↓
[ DESIGN & ADR REVIEW ]
        ↓
[ IMPLEMENT ]
        ↓
[ RUN AUTOMATED TESTS ]
        ↓
[ SELF-REVIEW & CODE AUDIT ]
        ↓
[ EDGE CASE DISCOVERY ]
        ↓
[ SECURITY AUDIT ]
        ↓
[ DOCUMENTATION UPDATE ]
        ↓
[ MILESTONE REPORT ]
        ↓
[ GIT COMMIT ]
        ↓
[ STOP & WAIT FOR HUMAN REVIEW ]
```

---

## 2. Milestone Directory & Detailed Gates

### Phase 1: Foundation & Core Engine (V1)

#### Milestone 0 — Product + Architecture Audit (CURRENT)
- **Goal**: Audit repository state, eliminate scope creep, resolve contradictions, and establish baseline architectural contracts.
- **Deliverables**:
  - `docs/PRODUCT_SPEC.md`
  - `docs/ARCHITECTURE.md`
  - `docs/SECURITY.md`
  - `docs/MILESTONES.md`
  - `docs/DECISIONS.md`
  - `docs/SYSTEM_STATE.md`
  - `.gitignore`, `README.md`
- **Gate**: Complete internal consistency across all docs; strict zero-code gate before Milestone 1.

#### Milestone 1 — Local Project Model
- **Goal**: Deterministic project inspection, file metadata calculation, and SQLite persistence.
- **Scope**:
  - Project root detection & `.gitignore` awareness.
  - Safe file walker (skips excluded directories, limits file sizes, ignores binaries).
  - SQLite database initialization (`.buildcoach/state.db`) with explicit schema versioning and migration framework.
  - Pydantic domain models (`Project`, `ProjectFile`, `GitState`, `SchemaVersion`).
- **Tests**: Empty project, nested project, non-git dir, permission errors, repeated scans, schema migrations.
- **Gate**: 100% deterministic scan results across repeated executions; scanner executes zero project scripts.

#### Milestone 2 — Project Brain Foundation (Project Graph)
- **Goal**: Construct the deterministic Project Graph representing files, modules, dependencies, and route structures.
- **Scope**:
  - AST / static dependency extraction for Python and JavaScript/TypeScript.
  - Relationship modeling with strict provenance (`file:A` imports `file:B`).
  - Graph persistence in SQLite.
- **Gate**: Developer can inspect the graph and answer "What exists in this project?" purely from static evidence. [Status: COMPLETED - 36 tests passing]

#### Milestone 3 — Git + Development Context
- **Goal**: Capture working-tree changes, staged/unstaged diffs, and classify file mutations.
- **Scope**:
  - Read-only Git integration (`git status --porcelain`, `git diff`).
  - Evidence records for Git observations with freshness and confidence metrics.
  - Working tree precedence over historical commits.
- **Gate**: Accurately classifies file additions, deletions, modifications, and renames without executing mutating Git operations. [Status: COMPLETED - 55 tests passing]

#### Milestone 4 — Context Engine (Pipeline, Secret Redaction, Compression)
- **Goal**: Implement the deterministic context processing pipeline.
- **Scope**:
  - Normalization, relevance filtering, and boilerplate stripping.
  - Deterministic secret detection and redaction engine.
  - Context compression budgeting (targeting 3,000–6,000 tokens).
- **Gate**: Synthetic projects containing API keys, private keys, and `.env` files verify 100% redaction of test fixtures. [Status: COMPLETED - 85 tests passing]

#### Milestone 5 — AI Gateway
- **Goal**: Decoupled AI provider interface and resilient communication layer.
- **Scope**:
  - `AIProviderAdapter` abstract base class.
  - Concrete `GeminiInteractionsAdapter` for Gemini 3.8 Flash via standard-library `urllib.request` (zero SDK dependencies).
  - Explicit user consent management (`ConsentManager`, `ConsentToken` with 6-tuple cryptographic binding).
  - BYOK credential handling (`CredentialStore`) enforcing environment variables and rejecting sensitive keys in `config.json`.
  - Zero-trust prompt fencing with anti-injection fences.
  - Strict evidence validation (`EvidenceValidator`) enforcing canonical `ContextItem.item_id` references without silent inference coercion.
  - Local audit persistence in SQLite table `gateway_runs`.
  - 100% mocked offline tests (zero external network requests).
- **Gate**: Validates consent, keys, request shape, timeout non-retry, 429/503 retry, and grounding metrics. [Status: COMPLETED - 110/110 tests passing]

#### Milestone 6 — Workflow 1: Understand What Changed
- **Goal**: End-to-end implementation of "Understand What Changed".
- **Scope**:
  - Deterministic "What Changed?" (100% physical truth from M3 ChangeSet; file paths, types, and line counts immutable).
  - Epistemically explicit "Why?" (`EXPLICIT` only with genuine comments/docs/commits; `INFERRED` for deductions; `UNKNOWN` for missing rationale).
  - Verbatim local snippet resolution from `ContextItem.content` (never model text).
  - Evidence-backed concept formulation (`WhatShouldIUnderstand`).
  - Interactive recall prompt generator (`CanIExplainThisPrompt` — zero evaluation/scoring in M6).
  - Strict separation of preparation (`prepare_change_explanation`) and execution (`explain_changes`).
  - Safe provider-failure fallback with zero invented AI content.
  - Minimal SQLite audit logging (`understand_change_runs`).
- **Gate**: Physical truth immutability, epistemic separation, local snippet resolution, and fallback verified. [Status: COMPLETED - 122/122 tests passing]

#### Milestone 7 — "Can I Explain This?" Comprehension Loop
- **Goal**: Active recall and evaluation engine.
- **Scope**:
  - Conceptual question generation from changed files.
  - Evaluation of user-submitted explanations against code evidence.
  - Qualitative comprehension classification (`UNDERSTOOD`, `PARTIALLY UNDERSTOOD`, `NEEDS REVIEW`, `UNKNOWN`).
  - Targeted follow-up question generation.
- **Gate**: Evaluates answers objectively against concrete code mechanisms without using uncalibrated percentage scores.

#### Milestone 8 — Workflow 2: Viva Defence Engine
- **Goal**: Project-specific viva preparation engine.
- **Scope**:
  - Knowledge gap identification across modules (`OCR`, `Auth`, `Database`).
  - 4-tier difficulty question generator (`EASY`, `MEDIUM`, `HARD`, `DEEP`).
  - Natural language evaluation, targeted micro-teaching, and re-testing.
- **Gate**: Questions are strictly grounded in project evidence, rejecting generic computer science textbook trivia.

#### Milestone 9 — VS Code Extension Client
- **Goal**: Lightweight developer UI inside VS Code.
- **Scope**:
  - Workspace detection and local engine IPC.
  - Minimal UI rendering "Understand what changed" and "Prepare for viva".
  - Question presentation and user answer submission forms.
- **Gate**: Extension contains zero autonomous code editing, terminal execution, or telemetry bloat.

#### Milestone 10 — V1 Polish, Multi-Repo Testing & Validation
- **Goal**: End-to-end validation across multiple real-world stacks (Python, TypeScript, React, Java).
- **Scope**:
  - Comprehensive integration test suite.
  - Performance benchmarking (local scanning < 1s, AI response < 10s).
  - Systematic testing against validation hypotheses (H1–H7).
- **Gate**: Both V1 workflows proven reliable across all target repo fixtures.

---

### Phase 2: Post-V1 Enhancements (Explicitly Deferred)

| Milestone | Feature Name | Prerequisite |
| :--- | :--- | :--- |
| **Milestone 11** | What Next? (Smallest useful next action) | Successful V1 validation |
| **Milestone 12** | Project Brain History (Build Story, Decision Ledger) | Milestone 11 completion |
| **Milestone 13** | Impact Before Change (Dependency blast radius analysis) | Project Graph maturity |
| **Milestone 14** | Formal Knowledge States (`EXPOSED` → `INDEPENDENT`) | User learning dataset |
| **Milestone 15** | Project Passport & Build Map (Visual architecture) | Validated user demand |
| **Milestone 16** | Chrome Extension Context (Permissioned browser logs) | Core engine stability |
| **Milestone 17** | BCAP (Build Coach Agent Protocol interoperability) | Industry agent adoption |
| **Milestone 18** | Multi-Provider Gateway (OpenAI, Anthropic adapters) | V1 scaling phase |
| **Milestone 19** | Advanced Learning (Spaced repetition, Bloom taxonomy) | Verified learning metrics |
