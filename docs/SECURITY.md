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

## 4. Secret Detection & Redaction Engine (Milestone 4 Implementation)

Before any context packet is assembled or cached for future external provider use, it must pass through the deterministic redaction pipeline:

### 4.1 Detection Rules & Precompiled Patterns:
1. **Private Keys (multiline)**:
   - Matches `-----BEGIN ... PRIVATE KEY-----` through `-----END ... PRIVATE KEY-----`.
   - Category: `PRIVATE_KEY`.
2. **Bearer & Authorization Tokens**:
   - Matches `Bearer <token>` in HTTP headers or source code.
   - Category: `BEARER_TOKEN`.
3. **High-Entropy / Formatted API Keys**:
   - OpenAI / Anthropic / Generic `sk-...`
   - Google `AIza...`
   - AWS access keys `AKIA...`, `ASIA...`
   - GitHub PATs `ghp_...`, `gho_...`, `github_pat_...`
   - Slack tokens `xoxb-...`, `xoxp-...`
   - Category: `API_KEY`.
4. **Password & Credential Assignments**:
   - Identifiers `password`, `passwd`, `pwd`, `pass`, `api_key`, `secret_key`, `auth_token`, `access_token` assigned in code or config.
   - Category: `PASSWORD` / `API_KEY`.
5. **.env Environment Variable Assignments**:
   - Variables ending in `KEY`, `SECRET`, `PASSWORD`, `PASSWD`, `PASS`, `TOKEN`, `CREDENTIAL`, or `PRIVATE`.
   - Category: `ENV_CREDENTIAL`.

### 4.2 Redaction Behavior & Privacy Guarantees:
- **Sanitization Placeholder**: Replaces sensitive values with explicit deterministic markers (`[REDACTED]`, `[REDACTED_PRIVATE_KEY]`) while preserving syntax boundaries and code structures.
- **Zero Raw Secret Persistence**: Discovered raw secret strings are **never logged**, **never included in `redaction_summary`**, **never persisted to SQLite**, and **never present in the `ContextPacket`**.
- **Detection Summary**: The packet metadata tracks only aggregate counts and category counts (e.g. `{"total_secrets_detected": 3, "categories": {"API_KEY": 2, "PASSWORD": 1}}`).
- **Pipeline Precedence**: Secret detection and redaction occur *before* compression and budgeting.

> **CRITICAL DISCLAIMER & LIMITATIONS**:
> This engine provides deterministic regex pattern matching for common credential formats. It is a defense-in-depth detection layer, NOT an infallible scanner. It does not guarantee detection of novel, custom-encoded, or obfuscated secrets. Repository authors remain responsible for not committing credentials to source code.

---

## 5. Privacy, No-AI Boundary & User Consent Model

### 5.1 No-AI & No-Execution Boundary (Milestone 4)
- **Zero AI Calls**: M4 contains no LLM SDKs, no external HTTP requests to OpenAI/Gemini/Anthropic, no prompt templates, and no model inference.
- **Zero Code Execution**: M4 treats all project content as passive data. It never imports project modules, evaluates source code, or executes shell commands based on file contents.
- **Filesystem Boundary**: Context extraction is strictly bounded to the project workspace root; path traversal attempts are halted.
- **Runtime Database Boundary**: Files in `.buildcoach/` (including `state.db`) are excluded from context collection.

### 5.2 Explicit User Consent & Cryptographic Token Binding (Milestone 5)
- **Local-First Default**: By default, all project scanning, Git state parsing, AST mapping, and context packet generation remain 100% local on disk (`.buildcoach/state.db`).
- **Explicit Consent**: Project data is transmitted to an external model provider **only** when an explicit, valid `ConsentToken` is supplied.
- **Cryptographic 6-Tuple Binding**: A `ConsentToken` is cryptographically bound to:
  1. `user_acknowledged`: Boolean flag indicating direct human approval.
  2. `packet_id`: The exact context packet ID.
  3. `packet_hash`: Deterministic SHA-256 digest of the complete canonical context packet.
  4. `provider`: Authorized provider identifier (e.g. `gemini`).
  5. `model`: Authorized model identifier (e.g. `gemini-3.8-flash`).
  6. `expires_at`: ISO UTC expiration timestamp (default: 15 minutes).
- **Tampering & Replay Prevention**: Any post-consent alteration to packet items, token estimate, or metadata invalidates the hash and halts execution before any external call.
- **Transparency**: The developer can inspect the exact `TransmissionPreview` (including files included, token estimate, item count, and redaction summary) prior to consenting.

### 5.3 BYOK Credential Handling & Zero On-Disk Storage (Milestone 5)
- Raw AI API keys are **never stored in plaintext** in `.buildcoach/config.json` or committed to version control.
- Supported storage mechanisms:
  - Process environment variables (`GEMINI_API_KEY`, `BUILDCOACH_GEMINI_API_KEY`).
  - Direct ephemeral in-memory parameters.
- **Active Disk Violation Rejection**: `CredentialStore` actively scans `.buildcoach/config.json` for suspicious key names (`api_key`, `gemini_key`, etc.) and immediately raises `SecurityConfigurationError`, refusing to execute until on-disk secrets are removed.
- **Masking**: Keys are masked (`AIza...cdef` or `***`) across all diagnostic messages and logs.

### 5.4 Zero-Trust Prompt Fencing & Anti-Injection
- Project content and diffs are wrapped inside XML fences: `<untrusted_project_evidence>` and `<context_item id="..." source="..." ...>`.
- Plain-string system instructions explicitly inform the model that all evidence is untrusted data and strictly forbid following instructions found inside user files or diffs.

### 5.5 Test Suite Network Isolation
- All unit and integration tests strictly mock external HTTP requests via `unittest.mock.patch('urllib.request.urlopen')`.
- Zero live API calls and zero actual API keys are used during automated testing.

### 5.6 Understand What Changed Security Invariants (Milestone 6)
- **Physical Truth Immutability**: The LLM is strictly prohibited from deciding what changed. The file list, change types, and line counts are sourced 100% deterministically from M3 `ChangeSet`.
- **Verbatim Local Snippet Resolution**: Evidence snippets are never sourced from model-generated text. They are resolved locally:
  $$\text{claim.evidence\_refs} \longrightarrow \text{ContextItem.item\_id} \longrightarrow \text{local ContextItem.content} \longrightarrow \text{snippet}$$
  This guarantees that code snippets presented to the developer are verbatim local disk contents already sanitized by M4 secret redaction.
- **Epistemic Integrity**: Inferences are explicitly segregated from observations. Rationale is marked `EXPLICIT` only when verified explanatory text exists in comments or commit messages; otherwise, it is labeled `INFERRED` with explicit `UNKNOWN` markers.
- **Fail-Safe Fallback**: In the event of provider timeouts or errors, the workflow degrades gracefully by presenting deterministic M3 diffs with zero invented AI text.

### 5.7 Local Bridge Security Model (Milestone 12.2)
- **Strict Loopback Binding**: The local bridge server (`backend.bridge.server`) binds exclusively to IPv4 loopback (`127.0.0.1`). Binding to all interfaces (`0.0.0.0`) is explicitly rejected.
- **Client IP Confinement**: Every incoming TCP connection is checked; non-loopback source IPs are rejected immediately with `403 Forbidden`.
- **Origin Access Control Boundary**: To prevent arbitrary web pages visited by the user from accessing the bridge (`http://127.0.0.1:8765`), the bridge inspects the HTTP `Origin` header:
  - Browser requests carrying an `Origin` header must begin with `chrome-extension://` or match local loopback origins (`http://127.0.0.1`, `http://localhost`).
  - Any request carrying an untrusted web `Origin` (e.g. `https://malicious-website.com`) is rejected immediately with `403 Forbidden` (`FORBIDDEN_ORIGIN`) across `OPTIONS`, `GET`, and `POST`.
  - Non-browser direct requests (e.g., local CLI probes or test clients with no `Origin` header) are permitted on loopback.
  - Wildcard `Access-Control-Allow-Origin: *` is strictly prohibited; CORS headers reflect only validated extension or loopback origins.
- **Payload Size Limits**: Incoming HTTP request bodies are capped at 5 MB (`MAX_PAYLOAD_BYTES = 5 * 1024 * 1024`). Excess payloads are rejected with `413 Payload Too Large`.
- **No Command or Code Execution**: The bridge server receives passive conversation JSON. It never passes content to a shell, subprocess, or code evaluation engine.
- **Path Traversal & Injection Prevention**: Conversation IDs, provider names, and titles are validated with strict regex patterns; path traversal characters (`..`, `/`, `\`) and shell metacharacters (`;`, `&`, `|`, `` ` ``, `$`) are rejected.
- **Server-Side Secret Re-Redaction**: Even though the browser extension redacts secrets client-side, the local bridge runs all received message content through `backend.context_engine.secrets.detect_and_redact` before persisting to SQLite.
- **Permission Prompting**: The extension requests `optional_host_permissions: ["http://127.0.0.1/*"]` only when the user explicitly initiates a transfer, ensuring no persistent background access.
- **CORS Scoping**: Preflight `OPTIONS` and response CORS headers allow extension origins and standard local headers with `Cache-Control: no-store`.

### 5.8 Project Binding Security Model (Milestone 12.3)
- **Project IDs Instead of Filesystem Paths**: The browser extension is never allowed to specify filesystem paths (`path`, `root`, `filesystem_path`, `directory`, `cwd`). Only stable, registered `project_id` strings are accepted.
- **Zero Inferred/Automatic Project Matching**: The system never guesses or infers project bindings from conversation contents, browser URLs, git remotes, or page titles. Binding requires explicit human selection.
- **Project Registry Ownership**: Local Build Coach owns project discovery and validation (`python -m backend.cli project register <path>`). The bridge endpoint `GET /v1/projects` returns only `{project_id, display_name}` to avoid leaking local filesystem structure.
- **Cardinality & Conflict Prevention**: Enforces `1:0..1` active binding cardinality (`conversation_project_bindings` table with `conversation_id UNIQUE`). Rebinding is deterministic and replaces the previous project binding atomically.
- **Standalone Unbound Usability**: An unbound conversation is stored safely but never automatically treated as project evidence, ensuring no accidental context contamination.

### 5.9 Conversation → Project Evidence Security Model (Milestone 12.4)
- **Zero LLM Evidence Generation**: Evidence is created 100% deterministically. AI may interpret evidence, but AI may NEVER invent or create evidence. No LLM or external API calls exist in the evidence generation pipeline.
- **Strict Project Binding Prerequisite**: Generating project evidence requires that the conversation is explicitly bound to a registered project (`PROJECT_BINDING_REQUIRED`). Unbound conversations reject evidence analysis with HTTP `422` or `ValueError`.
- **Project Boundary Isolation**: When a conversation is bound to Project A, references to paths or files that belong to Project B or outside Project A's root directory are treated strictly as `MISSING` or `OUTSIDE_PROJECT` with `CONTRADICTS` links. Project roots are resolved exclusively from the trusted local registry, never from conversation text or browser inputs.
- **Passive Data Handling & Sanitization**: Conversation text is treated strictly as passive, untrusted data. Line number suffixes (`path:line`, `#Lline`) are stripped safely without path execution or arbitrary file resolution. All claim text undergoes secret redaction before evidence record generation.

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
