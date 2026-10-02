# Architecture Decision Records (ADRs)

This document tracks all significant architectural, security, and scoping decisions for AI Build Coach. No major architectural changes may occur without an approved ADR.

---

## ADR-0001: Local-First Python & SQLite Foundation

### Status
Accepted

### Context
AI Build Coach needs a fast, embeddable, deterministic, and highly portable core engine capable of inspecting files, computing hashes, managing local schemas, and constructing project graphs. Many developer tools jump immediately to client-server architectures with Docker, PostgreSQL, Redis, or heavy Node/Next.js stacks, adding severe local operational overhead.

### Options Considered
1. **Full-stack Node/TypeScript/PostgreSQL**: Requires running Docker or a background database server; heavy RAM footprint; complex deployment.
2. **Rust / Go standalone binary**: Highly performant and single-binary, but higher initial iteration friction for rapid AST parsing and evaluation experimentation.
3. **Python 3.11+ with Pydantic & SQLite standard library**: Zero external database dependencies; fast local filesystem access; robust AST parsing libraries; lightweight SQLite embedded storage.

### Chosen Approach
Python 3.11+ using `pydantic` for data modeling, `sqlite3` for local persistence, and `pytest` for testing. The runtime lives locally in `.buildcoach/`.

### Rationale
- Zero installation friction (no external daemon or container).
- Native SQLite file database (`.buildcoach/state.db`) allows atomic transactions, fast indexed queries, and zero background resource consumption.
- Future VS Code extensions can easily bundle or invoke the local Python engine.

### Trade-offs & Consequences
- Requires Python 3.11+ on the developer host machine (or bundled virtualenv).
- Performance on multi-million line mono-repos will require aggressive exclusion filtering, which is handled in the project scanner.

### Date
2026-10-02

---

## ADR-0002: Strict Two-Workflow V1 Scope & Complete Exclusion of Code Generation

### Status
Accepted

### Context
A constant failure mode of developer tools in the AI era is "agent convergence" — attempting to become an end-to-end coding assistant that writes code, runs commands, and modifies projects. Furthermore, feature creep (e.g. adding screen recording, Chrome plugins, full project passport visualizers) dilutes focus from core user comprehension.

### Options Considered
1. **Broad All-in-One AI Assistant**: Include "Fix this code" suggestions, autonomous debugging, browser context recording, and dependency visualizers in V1.
2. **Strict Dual-Workflow Core**: Focus 100% of V1 on "Understand the Change" and "Viva Defence", with zero code generation or modification capabilities.

### Chosen Approach
Strictly limit V1 to the two comprehension workflows. Explicitly prohibit code generation, autonomous debugging, and silent file mutation.

### Rationale
- AI coding assistants (Cursor, Copilot, Gemini) already optimize software creation. Competing with them is counterproductive.
- Human understanding and verification is the true, underserved bottleneck.
- Forbidding code generation maintains clear product identity and prevents destructive bugs on user codebases.

### Trade-offs & Consequences
- Users expecting a "one-click fix" will be instructed to use their primary coding agent.
- Build Coach's role is strictly advisory and educational.

### Date
2026-10-02

---

## ADR-0003: 4-Tier Provenance Classification for Claims

### Status
Accepted

### Context
LLMs frequently hallucinate architectural intentions, assuming reasons for code changes (e.g., claiming a library was chosen for "scalability" when it was simply what the AI picked). Developers cannot build genuine understanding if the coach presents fabricated claims as established facts.

### Options Considered
1. **Unconstrained Natural Language Generation**: Allow the LLM to summarize and explain changes freely.
2. **Explicit 4-Tier Provenance Model**: Enforce structured parsing where every claim is classified into `OBSERVATION`, `INFERENCE`, `RECOMMENDATION`, or `UNKNOWN`.

### Chosen Approach
Adopt the 4-tier claim classification. Every explanation output from the AI Gateway must validate against this schema.

### Rationale
- Forces clear demarcation between what the codebase proves (Observation) vs. what is reasonable deduction (Inference).
- Guarantees that when evidence is missing, the system explicitly reports `UNKNOWN` rather than inventing rationale.

### Trade-offs & Consequences
- Requires structured output parsing and schema validation on LLM responses.
- Malformed model responses must be caught and retried by the AI Gateway.

### Date
2026-10-02

---

## ADR-0004: Passive Static Scanner Boundary (No Untrusted Script Execution)

### Status
Accepted

### Context
Inspecting repositories to understand structure often tempts tools to execute project scripts (e.g., `npm list`, `python setup.py egg_info`, `cargo metadata`). However, user projects must be treated as untrusted input. Malicious repositories can execute arbitrary code during indexing.

### Options Considered
1. **Active Scanning**: Run project package managers and scripts to resolve dependencies.
2. **Passive Static Scanning**: Read configuration files (`package.json`, `requirements.txt`, `pyproject.toml`) and ASTs statically without executing any shell commands.

### Chosen Approach
Passive static scanning only. The scanner reads files, parses ASTs, and inspects Git metadata via read-only flags (`git status --porcelain`, `git diff`). It never executes `npm`, `pip`, `python`, `bash`, or project binaries.

### Rationale
- Eliminates the primary vector for arbitrary code execution and supply-chain attacks.
- Keeps scans deterministic, fast, and safe across unknown codebases.

### Trade-offs & Consequences
- Dynamic runtime imports or monkey-patching cannot be fully inferred statically; these will be accurately tagged as `UNKNOWN` or `INFERENCE`.

### Date
2026-10-02

---

## ADR-0005: Decoupled AI Gateway with Deterministic Secret Redaction

### Status
Accepted

### Context
Sending project context to external LLMs creates significant risks of leaking secrets (API keys, database URLs, `.env` files) and tight coupling to a single model provider's proprietary API.

### Options Considered
1. **Direct Provider SDK Integration**: Hardcode calls to one vendor SDK directly within feature modules.
2. **Decoupled AI Gateway with Pre-Flight Redaction**: All AI calls route through an `AIProvider` interface. Before any network packet is dispatched, it must pass through an entropy and pattern-based redaction engine.

### Chosen Approach
Implement an abstract `AIProvider` gateway with a mandatory pre-flight secret detection and redaction pipeline. Raw API keys are never stored in project config files.

### Rationale
- Protects developers from accidental secret leakage.
- Allows switching or swapping providers (Gemini, OpenAI, Anthropic, local LLMs) without touching core business logic.
- Graceful degradation when the AI provider is offline or rate-limited.

### Trade-offs & Consequences
- Secret redaction adds a small processing overhead before LLM calls (mitigated by targeting 3k–6k token packets).
- Redaction placeholders (`[REDACTED_KEY]`) must be handled gracefully by model prompt templates.

### Date
2026-10-02
