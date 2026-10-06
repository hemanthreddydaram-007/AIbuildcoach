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
- **Gate**: Evaluates answers objectively against concrete code mechanisms without using uncalibrated percentage scores. [Status: COMPLETED - 139/139 tests passing]

#### Milestone 8 — Workflow 2: Viva Defence Engine
- **Goal**: Project-specific viva preparation engine.
- **Scope**:
  - Knowledge gap identification across modules (`OCR`, `Auth`, `Database`).
  - 4-tier difficulty question generator (`EASY`, `MEDIUM`, `HARD`, `DEEP`).
  - Natural language evaluation, targeted micro-teaching, and re-testing.
- **Gate**: Questions are strictly grounded in project evidence, rejecting generic computer science textbook trivia. [Status: COMPLETED - 163/163 tests passing]

#### Milestone 9 — Unified Engine CLI & One-Shot Headless JSON Interface
- **Goal**: Unified CLI entrypoint and headless one-shot JSON contract for human terminal usage and external host tool integrations.
- **Scope**:
  - Interactive terminal client and commands (`status`, `scan`, `understand`, `viva`).
  - Headless one-shot JSON interface (`--json` flag) producing strictly schema-compliant envelopes on stdout.
  - Zero persistence of student answers; stdin-based inputs (`--answer-stdin`, interactive prompts).
  - Diagnostic logs directed strictly to stderr.
- **Gate**: Single unified CLI binary/module entrypoint (`ai-build-coach`, `python -m backend.cli`) with clean stdout separation and 188/188 passing tests. [Status: COMPLETED - 188/188 tests passing]

#### Milestone 10 — V1 Polish, Multi-Repo Testing & Validation
- **Goal**: End-to-end validation across multiple real-world stacks (Python, TypeScript, React, Java).
- **Scope**:
  - Comprehensive multi-repo test suite (FastAPI, Node TypeScript, React JSX/TSX, Java Gradle, and Edge Cases).
  - Performance benchmarking validating SLAs: scan < 1.0s, graph construction < 500ms, context assembly < 500ms, CLI status --json < 250ms.
  - Systematic empirical testing against validation hypotheses (H1–H6, H7a, H7b).
- **Gate**: Both V1 workflows proven reliable across all target repo fixtures and 210/210 passing tests. [Status: COMPLETED - 210/210 tests passing]

#### Milestone 11 — AI Ingestion, Grounding & Verification
- **Goal**: Full ingestion, evidence grounding, and cryptographic consent-verification of AI provider sessions.
- **Scope**:
  - M11.0: Canonical `Conversation` and `ConversationMessage` schemas, secret detection, SQLite persistence.
  - M11.1: Evidence attribution and grounding graph linking project diffs/files to conversations.
  - M11.2: Deterministic verification packet construction, cryptographic hashing, and safe error metadata.
- **Gate**: Complete consent verification with zero fabricated timestamps and safe provider error masking. [Status: COMPLETED]

#### Milestone 12 — Browser Provider Bridge
- **Goal**: Manifest V3 browser extension and local loopback bridge for user-directed AI conversation capture and project binding.
- **Scope**:
  - M12.0: Manifest V3 extension foundation (ChatGPT, Claude, Gemini).
  - M12.1: Real provider DOM adapters with zero background monitoring, `activeTab` + `scripting` permissions.
  - M12.2: Local Bridge (`buildcoach-bridge-v1` over `127.0.0.1:8765`), dynamic `optional_host_permissions`, re-redaction, and `python -m backend.cli bridge start|status`.
  - M12.3: Project Binding (`conversation_project_bindings` v10 migration), stable project IDs, project registry (`python -m backend.cli project register|list|status`), `GET /v1/projects`, `POST /v1/conversations/{id}/bind`, `GET /v1/conversations/{id}/binding`, and extension popup project picker with skip support.
  - M12.4: Conversation → Project Evidence: Deterministic evidence generation for bound conversations, `PROJECT_BINDING_REQUIRED` enforcement, `POST /v1/conversations/{id}/evidence`, CLI `conversation evidence <conversation_id>`, and zero LLM calls for evidence creation.
  - M12.5: Runtime Failure & Change Observation: Deterministic event timeline (`GIT_CHANGE`, `COMMAND_*`, `PROCESS_*`, `TEST_*`, `HTTP_*`, `RUNTIME_ERROR`), `observation_events` v11 migration, normalized error signatures, stack trace parsing without root cause guessing, correlation engine (`AFFECTS_SAME_FILE`, `SAME_ERROR`, `ERROR_DISAPPEARED_AFTER_CHANGE`), explanation packets with explicit epistemic unknowns, CLI commands (`observation timeline|record|explanation`), and bridge endpoints (`POST /v1/projects/{id}/observations`, `GET /v1/projects/{id}/timeline`).
  - M12.6: Build Timeline Explanation: Educational explanation pipeline translating observation timelines into evidence-grounded narratives (`IncidentExplanation`), focused incident window filtering, deterministic `FixStatus` (`VERIFIED`, `RECOVERED`, `PERSISTING`, `UNKNOWN`), strict `TimelineExplanationValidator` gating against ungrounded claims or forbidden causality assertions, prompt fencing (`<untrusted_timeline_evidence>`), zero-loss offline deterministic fallback, CLI command `observation explain`, and bridge endpoint `POST /v1/projects/{id}/explain`.
  - M12.7: Knowledge Gap & Next Action Engine: Project-agnostic guidance engine consuming verified observation context and timeline explanations to identify evidence-backed knowledge and verification gaps (`KnowledgeGap`), plan candidate human actions (`NextAction`), deterministically rank the top action (`ActionRanker`), integrate with M7 Can-I-Explain, detect staleness upon new observations, CLI `guidance show|incident`, and bridge endpoint `GET /v1/projects/{project_id}/guidance`.
  - M12.8: Unified Build Coach Session: Project-level session layer orchestrating existing subsystems (M7, M11.1, M11.2, M12.4, M12.5, M12.6, M12.7) into a single coherent developer experience (`BuildCoachSession`), deterministic state precedence (`UNKNOWN`, `INVESTIGATING`, `VERIFYING`, `LEARNING`, `ACTION_REQUIRED`, `STABLE`, `READY`), unified actions (`WHAT HAPPENED?`, `WHAT SHOULD I DO?`, `DO I UNDERSTAND?`), schema migration v12 (`build_coach_sessions`), CLI `session show <project_id> [--json]`, bridge endpoint `GET /v1/projects/{project_id}/session`, and extension client `getSession`.
- **Gate**: Full local ingestion from extension to SQLite via loopback HTTP with explicit project selection, binding persistence, deterministic project evidence, local runtime observation correlation, grounded timeline explanations, deterministic next-action guidance, and unified session orchestration. [Status: COMPLETED]

---

### Phase 2: Post-V1 Enhancements (Explicitly Deferred)

| Milestone | Feature Name | Prerequisite |
| :--- | :--- | :--- |
| **Milestone 13** | Impact Before Change (Dependency blast radius analysis) | Project Graph maturity |
| **Milestone 14** | Formal Knowledge States (`EXPOSED` → `INDEPENDENT`) | User learning dataset |
| **Milestone 15** | Project Passport & Build Map (Visual architecture) | Validated user demand |
| **Milestone 16** | BCAP (Build Coach Agent Protocol interoperability) | Industry agent adoption |
| **Milestone 17** | Multi-Provider Gateway (OpenAI, Anthropic adapters) | V1 scaling phase |
| **Milestone 18** | Advanced Learning (Spaced repetition, Bloom taxonomy) | Verified learning metrics |

