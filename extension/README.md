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

