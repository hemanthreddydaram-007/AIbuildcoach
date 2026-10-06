# AI Build Coach - Provider Conversation Bridge (Chrome/Edge Extension)

Foundational Manifest V3 browser extension for explicit, user-controlled conversation capture from supported AI providers (ChatGPT, Claude, Gemini) into AI Build Coach.

## Architecture

```
User Click ("Capture Conversation")
          ↓
Content Script (ProviderPageAdapter)
          ↓
Client-Side Secret Redaction (secrets.js)
          ↓
In-Memory Preview (Popup UI: bridge.js)
          ↓
User Explicit Confirmation ("Send to Build Coach")
          ↓
Local Bridge (local_bridge.js) -> HTTP POST http://127.0.0.1:8765/v1/capture
          ↓
Python Local Bridge (backend.bridge)
          ↓
Python Secret Re-Redaction & Validation
          ↓
ConversationIngestionService -> SQLite (.buildcoach/state.db)
```

## Security & Privacy Principles

1. **Zero Background Monitoring**: The extension contains no alarms, timers, web navigation listeners, or background scrapers. It runs purely on user demand.
2. **Explicit User Control**: Conversations are captured only when the user opens the popup and clicks **Capture Conversation**. Nothing is sent to Build Coach until the user reviews the preview and explicitly clicks **Send to Build Coach**.
3. **Strict Minimum Permissions**:
   - `activeTab`: Scoped strictly to the user's active tab upon clicking the extension icon.
   - `scripting`: Executes page adapter scripts only when user triggers capture.
   - `optional_host_permissions: ["http://127.0.0.1/*"]`: Scoped strictly to loopback HTTP bridge. Requested dynamically upon user clicking "Send to Build Coach".
   - **NO** persistent `storage` permission (preview staged ephemerally in-memory).
   - **NO** `cookies` permission.
   - **NO** `webRequest` or `webNavigation` monitoring.
   - **NO** `<all_urls>` permission.
4. **Host Permission Boundary**: No static host permissions. Local loopback access is optional and prompt-gated.
5. **Client-Side Secret Redaction**: Secrets (OpenAI, Anthropic, GitHub, AWS, private keys, bearer tokens) are detected and redacted into `[REDACTED:<type>]` in-memory *before* leaving the content script.
6. **Server-Side Re-Redaction & Validation**: The Python local bridge re-validates payload bounds (max 5MB), schema integrity, and runs server-side secret redaction before any storage.
7. **Local-First Boundary**:
   - Bridge binds strictly to `127.0.0.1` (never `0.0.0.0`).
   - Rejects non-loopback connections.
   - Never executes received code or shell commands.
   - Fallback to "Copy JSON" is always available if the bridge is offline.

## Supported Providers

- **ChatGPT**: `https://chatgpt.com`, `https://chat.openai.com`
- **Claude**: `https://claude.ai`
- **Gemini**: `https://gemini.google.com`

## Starting the Local Bridge

To connect the extension to your local Build Coach, start the local bridge server via CLI:

```bash
# Check status
python -m backend.cli bridge status

# Start bridge (default port 8765 on 127.0.0.1)
python -m backend.cli bridge start

# Start bridge on a custom port
python -m backend.cli bridge start --port 9000
```

## Project Management & Explicit Binding (M12.3)

Build Coach manages local projects through a local-first project registry. The browser extension only ever receives and transmits stable **project IDs** (never arbitrary local filesystem paths).

### Registering Local Projects:
```bash
# Register a local project directory
python -m backend.cli project register /path/to/project

# List registered projects
python -m backend.cli project list

# Inspect project status and bound conversations count
python -m backend.cli project status <project_id>
```

### Binding Flow:
1. When capturing in the extension, the popup fetches registered projects via `GET /v1/projects` (exposing only `project_id` and `display_name`).
2. After sending the conversation, the user can explicitly select a project from the dropdown and click **Bind Project**.
3. If preferred, the user can click **Skip for now** — the conversation remains safely stored in SQLite as unbound, and does not contaminate project context until explicitly bound.
4. Rebinding to another project cleanly updates the binding with `1:0..1` cardinality.

## Evidence Analysis for Bound Conversations (M12.4)

Once a conversation is explicitly bound to a project, project-grounded deterministic evidence can be generated:

```bash
# Analyze conversation against bound project via CLI
python -m backend.cli conversation evidence <conversation_id> --json
```

Or programmatically via the local bridge:
```http
POST /v1/conversations/{conversation_id}/evidence
```

Unbound conversations strictly return `PROJECT_BINDING_REQUIRED` (HTTP `422`). Evidence generation requires zero LLM calls and operates 100% deterministically.

## Knowledge Gap & Next Action Guidance (M12.7)

Build Coach determines the single most useful understanding or verification action for the human to perform next:

```bash
# Query prioritized next actions and gaps via CLI
python -m backend.cli guidance show <project_id>
python -m backend.cli guidance show <project_id> --json
```

Or query via the local bridge:
```http
GET /v1/projects/{project_id}/guidance
```

Returns:
- **`top_next_action`**: The single highest-ranked concrete human action (`INVESTIGATE_ERROR`, `RUN_TEST`, `CHECK_RUNTIME`, `EXPLAIN_BACK`, `INSPECT_DIFF`, `READ_FILE`).
- **`gaps`**: Evidence-grounded knowledge or verification gaps (`UNRESOLVED_ERROR`, `TEST_COVERAGE`, `VERIFICATION`, `UNDERSTANDING`, `CODE_CHANGE_REVIEW`, `DEPENDENCY`).
- **`status`**: Re-evaluates automatically as new observations arrive.
- **Human-in-the-Loop**: Build Coach only recommends actions; it never autonomously executes them.

## Unified Build Coach Session (M12.8)

Build Coach provides a single unified project session orchestrating runtime observations, timeline explanations, pedagogical comprehension, and next actions:

```bash
# Query unified session via CLI
python -m backend.cli session show <project_id>
python -m backend.cli session show <project_id> --json
```

Or query via the local bridge:
```http
GET /v1/projects/{project_id}/session
```

Returns:
- **`state`**: Deterministic state (`READY`, `INVESTIGATING`, `VERIFYING`, `LEARNING`, `ACTION_REQUIRED`, `STABLE`, `UNKNOWN`).
- **`summary`**: Metrics on recent changes, active incidents, and verified recoveries.
- **`verification`**: Current verification status (`VERIFIED`, `RECOVERED`, `PERSISTING`, `UNKNOWN`).
- **`understanding`**: Whether conceptual understanding is required (`EXPLAIN_BACK`).
- **`next_action`**: The single highest-priority recommended human action.

## Real Developer Activity Capture (M12.9)

Build Coach observes real developer activities through an explicit local command runner:

```bash
# Execute project commands through Build Coach
python -m backend.cli run [project_id] [--timeout SECONDS] [--json] -- <command>

# Examples:
python -m backend.cli run -- pytest
python -m backend.cli run -- python app.py
python -m backend.cli run -- npm test
```

- **Explicit Local Execution**: Zero background surveillance, keylogging, or screen monitoring. Execution is strictly user-initiated.
- **Project Root Pinned**: The command executes strictly within the registered project's root directory.
- **Deterministic Observations**: Generates `COMMAND_STARTED`, `COMMAND_FINISHED`, `RUNTIME_ERROR`, and `TEST_*` observation events automatically.
- **Secret Redaction & Bounding**: Terminal outputs are capped to 64 KB and scrubbed of API keys, bearer tokens, and credentials before persistence.
- **Unified Session Integration**: Automatically refreshes the project session state and updates guidance recommendations without requiring AI provider calls.

## Transparent Terminal Integration (M12.10)

To avoid rewriting commands via `python -m backend.cli run -- <cmd>`, developers can enable explicit terminal integration for PowerShell:

```powershell
# Check terminal integration status
python -m backend.cli terminal status [project_id]

# Enable terminal integration (generates .buildcoach/terminal/buildcoach.ps1)
python -m backend.cli terminal enable [project_id]

# Activate in your current shell
. .buildcoach/terminal/buildcoach.ps1

# Run tracked commands transparently
bc-run pytest
bc-run python app.py

# Disable terminal integration anytime
python -m backend.cli terminal disable [project_id]
```

- **Zero Surveillance**: NO keylogging, NO screen recording, NO hidden monitoring daemons.
- **Single Execution**: Commands run strictly once as standard child processes with real-time terminal output streaming.
- **Pre-Persistence Redaction**: Secrets in commands and output are redacted prior to database storage.
- **Interactive Safety**: Interactive commands (`vim`, `ssh`, Python REPL) are protected from stream capture.

## Build Coach Workflow UI (M12.11)

The extension popup includes a unified **Build Coach** tab providing direct visibility into your development lifecycle:

1. **Tab Navigation**: Toggle between conversation `Capture` and `Build Coach`.
2. **Project Context**: Select any registered project and immediately view its current state.
3. **Core Workflow Answers (5+1 Questions)**:
   - **WHAT HAPPENED?**: Plain-language description of recent activity or failure, with one-click expandable timeline details.
   - **IS IT FIXED?**: Grounded status badges (`VERIFIED`, `RECOVERED`, `PERSISTING`, or `UNKNOWN`).
   - **HOW DO WE KNOW?**: Checkmarked factual evidence list.
   - **WHAT IS STILL UNKNOWN?**: Highlighted epistemic limitations and unknowns.
   - **WHAT SHOULD I DO NEXT?**: Single high-leverage next action.
   - **DO I UNDERSTAND IT?**: Self-verification / Can-I-Explain status with a quick "Test Understanding" trigger.
4. **Recent Activity Timeline**: Chronological log of recent commands, test runs, code modifications, and runtime errors.
5. **Zero Fake Completion**: No synthetic "mark fixed" or "mark understood" buttons. All statuses update purely through observed executions or verified understanding evaluations.

## Running Extension Tests

The extension test suite uses Node's built-in zero-dependency test runner:

```bash
cd extension
npm test
```

## Loading Extension in Chrome / Edge

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode** (toggle in upper right).
3. Click **Load unpacked**.
4. Select the `extension/` directory.

## Troubleshooting

- **"Local bridge is not running"**: Start the bridge via `python -m backend.cli bridge start`.
- **"Permission to connect to local bridge was denied"**: When Chrome/Edge prompts to allow connection to `http://127.0.0.1`, click Allow, or use "Copy JSON" to export the payload manually.
- **Port Conflict**: If port 8765 is occupied, run `python -m backend.cli bridge start --port <port>`.


