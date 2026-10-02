# Product Specification: AI Build Coach

## 1. Executive Summary & Vision

**Core Statement**: *AI builds the software. Build Coach builds your understanding.*

AI Build Coach is the human-understanding layer wrapped around AI-built software. It works alongside coding assistants (ChatGPT, Gemini, Claude, Cursor, GitHub Copilot, Replit, Antigravity, etc.) without competing with them or generating application code. Its fundamental purpose is to accelerate human comprehension, verify whether claims are backed by code evidence, expose knowledge gaps, and guide the user toward independent control over their project.

A user who needs Build Coach less over time is the ultimate success state.

---

## 2. Core Product Thesis

The rapid adoption of AI coding tools inverted the traditional software engineering feedback loop:

### Traditional Development Loop
$$\text{Human understands} \longrightarrow \text{Human designs} \longrightarrow \text{Human codes} \longrightarrow \text{Human tests} \longrightarrow \text{Human understands}$$

### AI-Assisted Development Loop
$$\text{Human has idea} \longrightarrow \text{AI generates code} \longrightarrow \text{AI modifies code} \longrightarrow \text{AI fixes errors} \longrightarrow \text{Software works} \longrightarrow \mathbf{\text{Human understanding gap}}$$

AI Build Coach bridges this comprehension gap via a closed loop:
```
USER INTENT
    ↓
AI BUILDS / MODIFIES SOFTWARE
    ↓
CODE CHANGE
    ↓
EVIDENCE
    ↓
PROJECT MEANING
    ↓
HUMAN UNDERSTANDING
    ↓
NEXT ACTION
    ↓
INDEPENDENCE
```

---

## 3. Mandatory Product Principles

### Principle 1: Understanding Over Generation
- Build Coach explains, teaches, and verifies.
- It must **never** become a code-writing assistant.
- **Strictly Prohibited**:
  - "Fix this code" buttons
  - Automatic code generation / scaffolding
  - Autonomous feature implementation
  - Autonomous refactoring
  - Autonomous debugging that silently edits source files
  - "Build this for me" workflows
- Build Coach can inspect, recommend actions, illustrate patterns, explain mechanisms, and suggest specific prompts for coding agents, but **user source code is never silently modified**.

### Principle 2: Evidence Before Claims
Every project statement and explanation produced by the system must be explicitly classified into one of four provenance categories:
1. **`OBSERVATION`**: Directly verifiable from static files, Git diffs, test outputs, or runtime diagnostics (e.g., `auth.py contains JWT encoding calls`).
2. **`INFERENCE`**: A reasoned interpretation drawn from multiple observations (e.g., `These 3 files appear to form a session authentication flow`). Inferences must never be masqueraded as verified facts.
3. **`RECOMMENDATION`**: Suggested actionable next step for the human (e.g., `Verify how the middleware handles token expiration`).
4. **`UNKNOWN`**: Explicit acknowledgement when evidence in the repository is absent or inconclusive (e.g., `The repository contains no evidence explaining why JWT was chosen over session cookies`). Never hallucinate missing architectural rationale.

---

## 4. V1 Scope: Exactly Two Primary Workflows

V1 contains strictly **two** primary user workflows. No additional features are permitted into V1.

```
+-------------------------------------------------------------+
|                      V1 PRODUCT SCOPE                       |
+------------------------------+------------------------------+
| Workflow 1:                  | Workflow 2:                  |
| UNDERSTAND THE CHANGE        | VIVA DEFENCE                 |
|                              |                              |
| 1. Working-tree Diff Scan    | 1. Project Codebase Scan     |
| 2. Main Change Identification| 2. Knowledge Gap Detection   |
| 3. Before/After Meaning      | 3. Targeted Viva Questions   |
| 4. Evidence & Provenance     | 4. Student Answers in Words  |
| 5. "Can I Explain This?"     | 5. Evidence-Based Evaluation |
|    Comprehension Loop        | 6. Targeted Teaching & Retest|
+------------------------------+------------------------------+
```

### Workflow 1: Understand the Change
- **Trigger**: Developer or AI modifies code in the working tree.
- **Inputs**: Uncommitted working tree files, Git diff, Git status, recent commit context.
- **Outputs**:
  - High-level summary of the main change.
  - Before vs. After functional shift.
  - Important files modified and their specific roles.
  - Evidence section categorizing claims into `OBSERVATION`, `INFERENCE`, and `UNKNOWN`.
  - Core architectural concepts the human must understand.
- **Interactive Loop: "Can I Explain This?"**:
  - The system poses a targeted conceptual question (e.g., *"Why does the authentication middleware exist here?"*).
  - The user provides an explanation in their own words.
  - Build Coach evaluates the explanation against project evidence and expected conceptual components:
    - *Purpose* (Why it exists)
    - *Mechanism* (How it operates)
    - *Failure Modes* (Edge cases, errors, expirations)
    - *Dependencies* (Upstream/downstream impacts)
  - Qualitative scoring only: `UNDERSTOOD`, `PARTIALLY UNDERSTOOD`, `NEEDS REVIEW`, `UNKNOWN`. (No fake percentage scores like "74% understood").
  - System offers targeted follow-up questions to fill detected gaps.

### Workflow 2: Viva Defence
- **Trigger**: Student/developer preparing for a thesis defence, technical interview, or code review.
- **Inputs**: Project graph, dependency structure, key modules, API routes, database schemas.
- **Outputs**:
  - Knowledge gap mapping across project modules (e.g., OCR pipeline: `WEAK`, Auth: `PARTIAL`, DB: `STRONG`).
  - Project-specific questions across four difficulty tiers:
    - `EASY`: Direct component identification (e.g., *"What does Dashboard.jsx render?"*)
    - `MEDIUM`: Architectural purpose (e.g., *"Why does this service use an asynchronous task queue?"*)
    - `HARD`: Edge cases and failure modes (e.g., *"What happens if the queue worker crashes mid-task?"*)
    - `DEEP`: Trade-offs and security implications (e.g., *"How is idempotency maintained during retries?"*)
  - Student answers in natural language.
  - Rigorous evaluation against repository evidence.
  - Targeted micro-lessons directly referencing project source files.
  - Re-verification to confirm understanding.

---

## 5. First-Use User Experience

Upon launch, the user is greeted with a minimal, uncluttered view centered on their active project:

```
+--------------------------------------------------------------+
| AI BUILD COACH                                               |
| Project: my-app [14 files modified]                          |
+--------------------------------------------------------------+
| What do you want to do?                                      |
|                                                              |
|   [ Understand what changed ]       [ Prepare for viva ]     |
+--------------------------------------------------------------+
```

- **Excluded from V1 Initial UI**:
  - No "Check if safe to modify"
  - No "Impact Before Change"
  - No "Build Story" / "Decision Ledger" / "Project Passport"
  - No "Chrome context / Browser recording"
  - No "BCAP configuration"
  - No generic multi-turn open chat window.

---

## 6. What Build Coach Is NOT

| Feature / Behavior | Included in Build Coach? | Rationale |
| :--- | :--- | :--- |
| **Code Generation / Autocomplete** | **NO** | Replaces the coding agent; violates Principle 1. |
| **Silent File Editing** | **NO** | Strips human agency; introduces untested side-effects. |
| **Generic Chatbot** | **NO** | Chat loops encourage aimless queries instead of project-grounded comprehension. |
| **Screen Recorder / Video Streaming** | **NO** | Heavyweight, privacy-invasive, and poor signal-to-noise ratio. |
| **Generic Tutorial Platform** | **NO** | Generic tutorials do not teach why *this specific project* was structured this way. |

---

## 7. Metrics & Success Definition

### North-Star Metric: Verified Independence
A concept or module previously requiring Build Coach guidance is now independently explained, defended, and modified by the human without intervention or regression across multiple revisions.

### Supporting Metric: Understanding Moments
Measurable, evidence-backed transitions from `NEEDS REVIEW` or `PARTIALLY UNDERSTOOD` to `UNDERSTOOD`.

### Anti-Metrics (Do NOT Optimize For):
- Number of messages sent or chat sessions started.
- Time spent inside the application.
- Number of model tokens consumed.
