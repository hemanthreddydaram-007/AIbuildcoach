# Security & Privacy Model: AI Build Coach

## 1. Threat Model & Guiding Principles

AI Build Coach adopts a zero-trust model toward target projects:
**Every inspected project repository must be treated as untrusted, potentially hostile input.**

### Potential Threat Vectors:
1. **Prompt Injection inside Source Files & Readmes**: Malicious comments, documentation strings, or instructions designed to hijack LLM behavior when packaged into context packets.
2. **Secrets & Sensitive Data Leakage**: Accidental inclusion of `.env` files, API keys, private certificates, or database credentials in context packets dispatched to external model providers.
3. **Arbitrary Code Execution**: Build scripts (`setup.py`, `package.json` scripts, `Makefile`) executing automatically during indexing or dependency resolution.
4. **Path Traversal & Filesystem Probing**: Malicious symlinks or relative path references targeting system files outside the designated workspace root.
5. **Denial of Service**: Extremely large binary files, infinite symlink loops, or giant generated directories (e.g. nested `node_modules`).

---

## 2. V1 Execution Boundary

To prevent arbitrary code execution:
- **Build Coach NEVER automatically runs project scripts or shell commands.**
- It must **NOT** invoke:
  ```bash
  npm install / npm run ...
  pip install / python ...
  bash ... / powershell ... / sh ...
  docker ... / make ...
  ```
- The local scanner is strictly a passive, static observer. It inspects file metadata and text content through sandboxed file reads.

### Future Command Execution Framework (Post-V1)
If command execution is introduced in later phases, all commands must be categorized:
- **`SAFE`**: Read-only, deterministic commands (e.g., `git status --porcelain`, `git diff`).
- **`REVIEW REQUIRED`**: Commands modifying state or running user-controlled tests (e.g., `pytest`, `npm test`). Requires explicit user confirmation in the UI.
- **`DESTRUCTIVE`**: State-mutating commands (e.g., `git reset --hard`, `rm -rf`). Strictly prohibited from autonomous execution.

---

## 3. Safe File Scanning & Path Traversal Mitigations

1. **Root Confinement**: All file paths inspected by the scanner must be validated using `Path.resolve()`. Any path resolving outside the workspace root is rejected.
2. **Symlink Resolution**: Symlinks pointing outside the project boundary are ignored. Symlink recursion loops are detected and halted.
3. **Size Limits & Binary Detection**:
   - Files larger than 1 MB are skipped from full content scanning by default (metadata only).
   - Binary files (detected via null-byte inspection in the first 8 KB) are never sent to external LLMs.
4. **Configurable Default Exclusions**:
   ```
   .git, node_modules, dist, build, .next, coverage,
   venv, .venv, __pycache__, .pytest_cache, .cache, generated, tmp
   ```

---

## 4. Secret Detection & Redaction Engine

Before any context packet leaves the local environment for an external AI provider, it must pass through the deterministic redaction pipeline:

### Detection Rules:
- **High-Entropy Tokens**: Shannon entropy analysis on alphanumeric string literals.
- **Pattern Matchers**:
  - AWS access keys (`AKIA[0-9A-Z]{16}`)
  - GitHub Personal Access Tokens (`ghp_[0-9a-zA-Z]{36}`)
  - OpenAI / Anthropic / Gemini API keys (`sk-[0-9a-zA-Z]{20,}`, `AIzaSy[0-9a-zA-Z-_]{33}`)
  - Private Keys (`-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----`)
  - Connection Strings (`postgres://`, `mongodb://`, `mysql://`)
- **Strict File Type Exclusions**: Files matching `.env*`, `*.pem`, `*.key`, `*.cert`, `id_rsa` are strictly blocked from external transmission regardless of content.

### Redaction Replacement:
Detected secrets are sanitized with explicit placeholder markers (e.g. `[REDACTED_API_KEY]`, `[REDACTED_PRIVATE_KEY]`).

> **CRITICAL DISCLAIMER**: The secret detector provides defense-in-depth but does not guarantee 100% discovery of novel or obfuscated secrets. The user is always shown which files are included in the transmission bundle.

---

## 5. Privacy & User Consent Model

### Explicit Data Transmission
- **Local-First Default**: By default, all project scanning, Git state parsing, AST mapping, and knowledge tracking remain 100% local on disk (`.buildcoach/`).
- **Explicit Consent**: Project data is transmitted to an external model provider **only** when the user explicitly triggers an AI action (e.g., clicking *"Understand what changed"* or *"Submit Viva Answer"*).
- **Transparency**: The UI must clearly indicate:
  1. What provider is receiving the request.
  2. The compressed context size (token count / file list).
  3. Confirmation that all detected secrets have been redacted.

### Credential Handling
- Raw AI API keys are **never stored in plaintext** in `.buildcoach/config.json` or committed to version control.
- Supported storage mechanisms:
  - Process environment variables (`GEMINI_API_KEY`, etc.)
  - OS-native credential storage (Windows Credential Manager, macOS Keychain, Linux Secret Service).

---

## 6. Milestone Security Review Protocol

At the completion of every single development milestone, the following 9-point security audit must be performed and recorded:

1. **Prompt Injection**: Can project content manipulate model instructions or bypass system constraints?
2. **Malicious Repository**: Can a crafted repository trigger path traversal, infinite loops, or crash the scanner?
3. **Secret Exposure**: Are any secrets leaked into logs, SQLite databases, terminal outputs, or Git commits?
4. **Arbitrary Commands**: Does any component trigger un-sandboxed shell commands or project scripts?
5. **Data Exfiltration**: Are context packets strictly filtered and redacted before dispatch to AI providers?
6. **User Consent**: Can external provider calls be triggered without explicit user action?
7. **Provenance Spoofing**: Can generated model output be mistaken for verified local project truth?
8. **Stale Evidence**: Can outdated file hashes or cached diffs produce misleading claims?
9. **Fake Evidence Injection**: Can an adversary inject synthetic evidence records into `.buildcoach/state.db`?
