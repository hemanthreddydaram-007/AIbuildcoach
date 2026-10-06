/**
 * Content Script for AI Build Coach Browser Extension.
 *
 * Injected strictly ON-DEMAND via chrome.scripting.executeScript when the user clicks "Capture Conversation".
 * ZERO automatic background injection. ZERO timers. ZERO observers.
 * Performs DOM extraction, secret redaction, and normalization, returning the result to popup.js.
 */

(function executeInPageCapture() {
  const currentUrl = window.location.href;

  // 1. Secret Redaction Engine
  const SECRET_PATTERNS = [
    { type: "ANTHROPIC_API_KEY", regex: /\bsk-ant-[A-Za-z0-9_-]{20,}\b/g },
    { type: "OPENAI_API_KEY", regex: /\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b/g },
    { type: "GITHUB_TOKEN", regex: /\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,})\b/g },
    { type: "AWS_ACCESS_KEY", regex: /\bAKIA[0-9A-Z]{16}\b/g },
    { type: "PRIVATE_KEY", regex: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g },
    { type: "BEARER_TOKEN", regex: /\bBearer\s+[A-Za-z0-9_\-\.]{24,}\b/gi },
  ];

  function redactText(text) {
    if (!text || typeof text !== "string") return { text: text || "", count: 0, types: [] };
    let sanitized = text;
    let count = 0;
    const types = new Set();
    for (const { type, regex } of SECRET_PATTERNS) {
      const matches = sanitized.match(regex);
      if (matches && matches.length > 0) {
        count += matches.length;
        types.add(type);
        sanitized = sanitized.replace(regex, `[REDACTED:${type}]`);
      }
    }
    return { text: sanitized, count, types: Array.from(types) };
  }

  function redactMessages(rawMessages) {
    let totalSecrets = 0;
    const allTypes = new Set();
    const sanitized = rawMessages.map((m, idx) => {
      const { text, count, types } = redactText(m.content);
      totalSecrets += count;
      types.forEach((t) => allTypes.add(t));
      const meta = { ...(m.metadata || {}) };
      if (count > 0) {
        meta.redacted = true;
        meta.secrets_detected = count;
        meta.secret_types = types;
      }
      return {
        message_id: m.message_id || `ext_msg_${idx + 1}`,
        role: m.role || "USER",
        content: text,
        timestamp: m.timestamp || null,
        sequence: m.sequence || idx + 1,
        metadata: meta,
      };
    });
    return { sanitizedMessages: sanitized, totalSecrets, detectedTypes: Array.from(allTypes) };
  }

  // 2. Identify Provider
  function identifyProvider(url) {
    try {
      const host = new URL(url).hostname.toLowerCase();
      if (host === "chatgpt.com" || host.endsWith(".chatgpt.com") || host === "chat.openai.com" || host.endsWith(".chat.openai.com")) {
        return "CHATGPT";
      }
      if (host === "claude.ai" || host.endsWith(".claude.ai")) {
        return "CLAUDE";
      }
      if (host === "gemini.google.com" || host.endsWith(".gemini.google.com")) {
        return "GEMINI";
      }
    } catch {
      return null;
    }
    return null;
  }

  const provider = identifyProvider(currentUrl);
  if (!provider) {
    return {
      success: false,
      status: "CAPTURE_UNAVAILABLE",
      error: {
        code: "UNSUPPORTED_PROVIDER",
        message: `Current page (${currentUrl}) is not a supported AI provider.`,
      },
    };
  }

  // 3. Provider-Specific Extraction
  let title = document.title ? document.title.trim() : null;
  const rawMessages = [];

  if (provider === "CHATGPT") {
    // Title
    const activeSidebar = document.querySelector("nav li[data-active='true'], nav a[class*='active']");
    if (activeSidebar && activeSidebar.textContent) title = activeSidebar.textContent.trim();
    if (title) title = title.replace(/\s*[-|•]\s*ChatGPT.*$/i, "").trim();

    // Turns
    let turnElements = document.querySelectorAll("article [data-message-author-role]");
    if (turnElements.length === 0) turnElements = document.querySelectorAll("[data-message-author-role]");
    if (turnElements.length === 0) turnElements = document.querySelectorAll('div[data-testid^="conversation-turn-"]');

    let seq = 1;
    turnElements.forEach((turnEl) => {
      let role = "USER";
      const authorRole = turnEl.getAttribute("data-message-author-role");
      if (authorRole) {
        role = authorRole.toUpperCase() === "ASSISTANT" ? "ASSISTANT" : "USER";
      } else if (
        turnEl.querySelector('[data-message-author-role="assistant"]') ||
        turnEl.querySelector('svg[aria-label="ChatGPT"]') ||
        turnEl.getAttribute("data-testid")?.includes("assistant")
      ) {
        role = "ASSISTANT";
      }

      const contentEl =
        turnEl.querySelector(".whitespace-pre-wrap") ||
        turnEl.querySelector(".markdown") ||
        turnEl.querySelector("[data-message-id]") ||
        turnEl;

      const text = (contentEl.textContent || "").trim();
      if (text && !/^(Copy|Edit|Read aloud|Regenerate|Bad response|Good response)$/i.test(text)) {
        rawMessages.push({
          message_id: turnEl.getAttribute("data-message-id") || `chatgpt_turn_${seq}`,
          role,
          content: text,
          sequence: seq++,
        });
      }
    });
  } else if (provider === "CLAUDE") {
    // Title
    const headerTitle = document.querySelector('[data-testid="chat-title"], button[data-testid="chat-menu-trigger"]');
    if (headerTitle && headerTitle.textContent) title = headerTitle.textContent.trim();
    if (title) title = title.replace(/\s*[-|•]\s*Claude.*$/i, "").trim();

    // Turns
    const turnElements = document.querySelectorAll(
      '[data-testid="user-message"], .font-user-message, .font-claude-message, [data-is-streaming], div[class*="font-claude-message"]'
    );

    let seq = 1;
    turnElements.forEach((el) => {
      let role = "ASSISTANT";
      const isUser =
        el.getAttribute("data-testid") === "user-message" ||
        el.classList.contains("font-user-message") ||
        el.closest('[data-testid="user-message"]') !== null;

      if (isUser) role = "USER";

      const text = (el.textContent || "").trim();
      if (text && !/^(Copy|Retry|Edit|Retry with artifacts)$/i.test(text)) {
        const isDuplicate = rawMessages.some((m) => m.content === text && m.role === role);
        if (!isDuplicate) {
          rawMessages.push({
            message_id: el.getAttribute("data-message-id") || `claude_turn_${seq}`,
            role,
            content: text,
            sequence: seq++,
          });
        }
      }
    });
  } else if (provider === "GEMINI") {
    // Title
    const convTitleEl = document.querySelector("h1.conversation-title, [data-test-id='conversation-title'], div.conversation-title");
    if (convTitleEl && convTitleEl.textContent) title = convTitleEl.textContent.trim();
    if (title) title = title.replace(/^Gemini\s*[-|•]\s*/i, "").replace(/\s*[-|•]\s*Gemini.*$/i, "").trim();

    // Turns
    const turnElements = document.querySelectorAll(
      "user-query, model-response, .user-query-container, .response-container, div.chat-turn"
    );

    let seq = 1;
    turnElements.forEach((el) => {
      const tagName = el.tagName.toUpperCase();

      if (el.classList.contains("chat-turn")) {
        const uq = el.querySelector("user-query, .user-query-container, .query-text");
        if (uq) {
          const t = (uq.textContent || "").trim();
          if (t) rawMessages.push({ message_id: `gemini_turn_${seq}`, role: "USER", content: t, sequence: seq++ });
        }
        const mr = el.querySelector("model-response, .response-container, .model-response-text");
        if (mr) {
          const t = (mr.textContent || "").trim();
          if (t) rawMessages.push({ message_id: `gemini_turn_${seq}`, role: "ASSISTANT", content: t, sequence: seq++ });
        }
        return;
      }

      let role = (tagName === "MODEL-RESPONSE" || el.classList.contains("response-container")) ? "ASSISTANT" : "USER";
      const contentEl = el.querySelector(".query-text") || el.querySelector(".message-content") || el.querySelector(".model-response-text") || el;
      const text = (contentEl.textContent || "").trim();

      if (text && !/^(Copy|Retry|Share|Modify response)$/i.test(text)) {
        const isDuplicate = rawMessages.some((m) => m.content === text && m.role === role);
        if (!isDuplicate) {
          rawMessages.push({
            message_id: el.getAttribute("data-message-id") || `gemini_turn_${seq}`,
            role,
            content: text,
            sequence: seq++,
          });
        }
      }
    });
  }

  // 4. Provider Failure Safety Check
  if (rawMessages.length === 0) {
    return {
      success: false,
      status: "CAPTURE_UNAVAILABLE",
      error: {
        code: "CAPTURE_UNAVAILABLE",
        message: `Could not identify active conversation message turns on ${provider} page.`,
      },
    };
  }

  // 5. Client-Side Secret Redaction Before Output
  const { sanitizedMessages, totalSecrets, detectedTypes } = redactMessages(rawMessages);

  // 6. Common Normalized Payload
  const conversation = {
    provider,
    source: "WEB_EXTENSION",
    title: title || `${provider} Conversation`,
    captured_at: new Date().toISOString(),
    messages: sanitizedMessages,
    metadata: {
      url: currentUrl,
      adapter: `${provider}PageAdapter`,
      turn_count: sanitizedMessages.length,
      total_secrets_redacted: totalSecrets,
      redacted_secret_types: detectedTypes,
    },
    project_id: null,
  };

  return {
    success: true,
    status: "SUCCESS",
    payload: {
      conversation,
      detectedSecretsCount: totalSecrets,
    },
  };
})();
