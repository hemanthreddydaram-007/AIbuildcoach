# System State: AI Build Coach

This document serves as the project's operational memory across development sessions. It must be updated at the conclusion of every milestone.

---

## Current Milestone
**Milestone 0: Product + Architecture Audit**

## Status
**Completed / Ready for Audit Gate Review**

---

## What Works
- Initial repository established and git initialized (`.git/`).
- Baseline `.gitignore` configured to exclude Python caches, SQLite runtime files (`.buildcoach/`), virtual environments, secrets, and IDE configs.
- High-level project README (`README.md`) established defining core purpose and non-goals.
- Core specifications and architectural contracts authored and verified:
  - `docs/PRODUCT_SPEC.md`: Product thesis, principles, V1 dual workflows, first-use UX, anti-metrics.
  - `docs/ARCHITECTURE.md`: 4-layer model, context priority hierarchy, context pipeline, local-first `.buildcoach/` layout, AI Gateway interface.
  - `docs/SECURITY.md`: Threat model, zero-trust repository scanning, passive scanner boundaries, deterministic secret redaction, privacy consent, and 9-point milestone audit.
  - `docs/MILESTONES.md`: 11-step development loop, Milestones 0–10 (V1), and Milestones 11–19 (post-V1).
  - `docs/DECISIONS.md`: Initial ADRs 0001 through 0005.

---

## What Does Not Work (Intentional Scope Boundaries)
- No user-facing code or backend implementation exists yet (strictly prohibited in Milestone 0).
- Local scanner, SQLite persistence, and AI Gateway are scheduled for Milestones 1–5.
- No code generation, automated refactoring, or autonomous shell execution exists (prohibited across all milestones).

---

## What Was Tested
- Git repository initialization and `.gitignore` rule verification.
- Documentation internal consistency check:
  - Verified no contradictions between V1 scope and milestone deliverables.
  - Verified consistent naming of provenance tiers (`OBSERVATION`, `INFERENCE`, `RECOMMENDATION`, `UNKNOWN`).
  - Verified that technology stack excludes premature frameworks.

---

## Known Issues
- None at this milestone stage.

---

## Known Risks
1. **Model Hallucination Risk**: External AI models may generate speculative explanations when summarizing diffs. (Mitigated by mandatory claim classification and validation in Milestone 6).
2. **Context Blowup Risk**: Large diffs or mono-repos can exceed token limits. (Mitigated by context relevance filtering and 3k–6k token budgeting in Milestone 4).
3. **Secret Exfiltration Risk**: Untrusted projects containing credentials. (Mitigated by pre-flight deterministic redaction in Milestone 4 and zero-trust passive scanning).

---

## Next Milestone
**Milestone 1: Local Project Model**
- Implement project root detection, `.gitignore` filtering, safe file walking, metadata hashing, and SQLite schema migrations in `.buildcoach/state.db`.
