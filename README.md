# AI Build Coach

> **Core statement**: *AI builds the software. Build Coach builds your understanding.*

AI Build Coach is the human-understanding layer around AI-built software. It works alongside AI coding tools (such as ChatGPT, Gemini, Claude, Cursor, GitHub Copilot, Replit, and Antigravity) without replacing them or becoming another code-generation agent.

---

## What AI Build Coach Is

AI Build Coach is purpose-built to help a human:
1. **Understand what AI built** and what changed across project files.
2. **Understand why changes occurred**, grounded strictly in code and Git evidence.
3. **Verify claims** using explicit provenance (Observation vs. Inference vs. Recommendation vs. Unknown).
4. **Test genuine comprehension** through interactive recall ("Can I Explain This?").
5. **Identify knowledge gaps** and learn directly from the user's real project.
6. **Defend project decisions** via project-specific Viva Defence.
7. **Graduate to independent project control** (a user needing less Build Coach over time is a success state).

## What AI Build Coach Is NOT

- **NOT a code generator**: Does not write, autocomplete, or silently mutate user source files.
- **NOT an autonomous coding agent**: Has no "Fix this code for me" autonomous execution loop.
- **NOT a generic chatbot**: Interface and reasoning are tightly grounded in the local project model.
- **NOT a tutorial platform**: Does not deliver generic curriculum; teaches solely from the user's active codebase.
- **NOT a screen recorder / screen share**: Does not rely on video or invasive tracking.

---

## V1 Scope

V1 focuses strictly on two core workflows:

1. **Workflow 1: Understand the Change**
   - Inspects working-tree changes and Git diffs.
   - Summarizes main changes, before/after behavioral shifts, and affected files.
   - Categorizes statements with evidence tags (`OBSERVATION`, `INFERENCE`, `UNKNOWN`).
   - Powers the "Can I Explain This?" comprehension loop.

2. **Workflow 2: Viva Defence**
   - Scans project codebase for architecture, dependencies, and complex modules.
   - Generates project-specific questions across difficulty levels (`EASY`, `MEDIUM`, `HARD`, `DEEP`).
   - Evaluates human answers against project evidence without arbitrary scoring.
   - Identifies gaps, provides targeted teaching, and prompts re-verification.

---

## Architecture & Technology Strategy

- **Local-First**: Operates directly on the developer's filesystem via `.buildcoach/` using SQLite and local indexing.
- **Foundation Stack**: Python 3.11, Pydantic, SQLite, pytest. No premature heavyweight frameworks (React, Next.js, Redis, Postgres) during foundation milestones.
- **Provider Decoupled**: AI Gateway abstracts model providers (Gemini, OpenAI, Anthropic) behind a strict interface.
- **Security-First**: Treats all repository contents as untrusted input. Performs deterministic secret redaction before sending context packets to external AI providers.

---

## Documentation

- [Product Specification](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/PRODUCT_SPEC.md)
- [System Architecture](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/ARCHITECTURE.md)
- [Security & Privacy Model](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/SECURITY.md)
- [Milestone Roadmap](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/MILESTONES.md)
- [Architecture Decisions (ADRs)](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/DECISIONS.md)
- [System State](file:///c:/Users/DARAM%20%20HEMANTH%20REDDY/Desktop/AIbuildcoach/docs/SYSTEM_STATE.md)
