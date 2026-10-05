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
Background Service Worker (M11.0 Standard Payload)
```

## Security & Privacy Principles

1. **Zero Background Monitoring**: The extension contains no alarms, timers, web navigation listeners, or background scrapers. It runs purely on user demand.
2. **Explicit User Control**: Conversations are captured only when the user opens the popup and clicks **Capture Conversation**. Nothing is sent to Build Coach until the user reviews the preview and clicks **Send to Build Coach**.
3. **Strict Minimum Permissions**:
   - `activeTab`: Scoped strictly to the user's active tab upon clicking the extension icon.
   - `storage`: Temporary in-memory staging for reviewed conversations.
   - **NO** `cookies` permission.
   - **NO** `webRequest` or `webNavigation` monitoring.
   - **NO** `<all_urls>` permission.
4. **Host Permission Boundary**: Limited strictly to:
   - `https://chatgpt.com/*`
   - `https://chat.openai.com/*`
   - `https://claude.ai/*`
   - `https://gemini.google.com/*`
5. **Client-Side Secret Redaction**: Secrets (OpenAI, Anthropic, GitHub, AWS, private keys, bearer tokens) are detected and redacted into `[REDACTED:<type>]` in-memory *before* leaving the content script.
6. **Untrusted Data Isolation**: Text content is treated as untrusted data, never executed as code.

## Supported Providers

- **ChatGPT**: `https://chatgpt.com`, `https://chat.openai.com`
- **Claude**: `https://claude.ai`
- **Gemini**: `https://gemini.google.com`

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
