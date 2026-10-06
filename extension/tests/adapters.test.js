import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { ProviderPageAdapter, CaptureStatus } from "../src/adapters/base.js";
import { ChatGPTPageAdapter } from "../src/adapters/chatgpt.js";
import { ClaudePageAdapter } from "../src/adapters/claude.js";
import { GeminiPageAdapter } from "../src/adapters/gemini.js";
import { ProviderAdapterFactory, defaultAdapterFactory } from "../src/adapters/factory.js";

// Helper to create synthetic DOM elements for testing without a full browser
function createSyntheticDocument(htmlStructure, options = {}) {
  const elements = [];

  return {
    title: options.title || "",
    location: { href: options.url || "https://example.com" },
    querySelector(selector) {
      const match = this.querySelectorAll(selector);
      return match.length > 0 ? match[0] : null;
    },
    querySelectorAll(selector) {
      if (options.selectorMap && options.selectorMap[selector]) {
        return options.selectorMap[selector];
      }
      return [];
    },
  };
}

describe("ProviderPageAdapter Base Contract", () => {
  test("base class throws unimplemented errors", async () => {
    const base = new ProviderPageAdapter();
    assert.throws(() => base.canHandle("https://example.com"), /must be implemented/);
    assert.throws(() => base.getProvider(), /must be implemented/);
    await assert.rejects(() => base.captureConversation(null), /must be implemented/);
  });

  test("normalizeConversation handles CAPTURE_UNAVAILABLE gracefully", () => {
    const adapter = new ChatGPTPageAdapter();
    const raw = {
      status: CaptureStatus.CAPTURE_UNAVAILABLE,
      error: "CAPTURE_UNAVAILABLE",
      reason: "No turns detected",
    };
    const normalized = adapter.normalizeConversation(raw);
    assert.equal(normalized.status, CaptureStatus.CAPTURE_UNAVAILABLE);
    assert.equal(normalized.error, "CAPTURE_UNAVAILABLE");
    assert.equal(normalized.messages.length, 0);
  });

  test("normalizeConversation normalizes valid captured payload", () => {
    const adapter = new ChatGPTPageAdapter();
    const raw = {
      status: CaptureStatus.SUCCESS,
      title: "Test Chat",
      messages: [
        { role: "user", content: "Hi", sequence: 1 },
        { role: "assistant", content: "Hello", sequence: 2 },
      ],
      rawMetadata: { turnCount: 2 },
    };
    const normalized = adapter.normalizeConversation(raw);
    assert.equal(normalized.status, CaptureStatus.SUCCESS);
    assert.equal(normalized.provider, "CHATGPT");
    assert.equal(normalized.messages.length, 2);
    assert.equal(normalized.messages[0].role, "USER");
    assert.equal(normalized.messages[1].role, "ASSISTANT");
  });
});

describe("Real ChatGPT DOM Adapter", () => {
  const adapter = new ChatGPTPageAdapter();

  test("identifies chatgpt.com and chat.openai.com", () => {
    assert.equal(adapter.canHandle("https://chatgpt.com/c/123"), true);
    assert.equal(adapter.canHandle("https://chat.openai.com/"), true);
    assert.equal(adapter.canHandle("https://claude.ai"), false);
  });

  test("returns CAPTURE_UNAVAILABLE on empty page or missing conversation", async () => {
    const emptyDoc = createSyntheticDocument("", { url: "https://chatgpt.com/c/empty" });
    const result = await adapter.captureConversation(emptyDoc);
    assert.equal(result.status, CaptureStatus.CAPTURE_UNAVAILABLE);
    assert.equal(result.error, "CAPTURE_UNAVAILABLE");
    assert.equal(result.messages.length, 0);
  });

  test("extracts messages from ChatGPT-like DOM structure with author roles and title", async () => {
    const turn1 = {
      getAttribute: (attr) => (attr === "data-message-author-role" ? "user" : null),
      querySelector: () => ({ textContent: "How do I configure logging in FastAPI?" }),
      textContent: "How do I configure logging in FastAPI?",
    };
    const turn2 = {
      getAttribute: (attr) => (attr === "data-message-author-role" ? "assistant" : null),
      querySelector: () => ({ textContent: "You can use Python standard logging module." }),
      textContent: "You can use Python standard logging module.",
    };

    const doc = createSyntheticDocument("", {
      title: "FastAPI Logging - ChatGPT",
      url: "https://chatgpt.com/c/fastapi-log",
      selectorMap: {
        "article [data-message-author-role]": [turn1, turn2],
      },
    });

    const result = await adapter.captureConversation(doc);
    assert.equal(result.status, CaptureStatus.SUCCESS);
    assert.equal(result.title, "FastAPI Logging");
    assert.equal(result.messages.length, 2);
    assert.equal(result.messages[0].role, "USER");
    assert.equal(result.messages[0].content, "How do I configure logging in FastAPI?");
    assert.equal(result.messages[1].role, "ASSISTANT");
    assert.equal(result.messages[1].content, "You can use Python standard logging module.");
  });

  test("filters out ChatGPT UI action noise (e.g. Copy, Edit buttons)", async () => {
    const turn1 = {
      getAttribute: (attr) => (attr === "data-message-author-role" ? "user" : null),
      querySelector: () => null,
      textContent: "Explain DNS",
    };
    const noiseTurn = {
      getAttribute: (attr) => (attr === "data-message-author-role" ? "assistant" : null),
      querySelector: () => null,
      textContent: "Copy",
    };

    const doc = createSyntheticDocument("", {
      title: "DNS - ChatGPT",
      url: "https://chatgpt.com/c/dns",
      selectorMap: {
        "article [data-message-author-role]": [turn1, noiseTurn],
      },
    });

    const result = await adapter.captureConversation(doc);
    assert.equal(result.status, CaptureStatus.SUCCESS);
    assert.equal(result.messages.length, 1);
    assert.equal(result.messages[0].content, "Explain DNS");
  });
});

describe("Real Claude DOM Adapter", () => {
  const adapter = new ClaudePageAdapter();

  test("identifies claude.ai domains", () => {
    assert.equal(adapter.canHandle("https://claude.ai/chat/123"), true);
    assert.equal(adapter.canHandle("https://chatgpt.com"), false);
  });

  test("returns CAPTURE_UNAVAILABLE on empty page or missing messages", async () => {
    const emptyDoc = createSyntheticDocument("", { url: "https://claude.ai/chat/empty" });
    const result = await adapter.captureConversation(emptyDoc);
    assert.equal(result.status, CaptureStatus.CAPTURE_UNAVAILABLE);
    assert.equal(result.error, "CAPTURE_UNAVAILABLE");
  });

  test("extracts Claude user and assistant turns preserving ordering", async () => {
    const userTurn = {
      getAttribute: (attr) => (attr === "data-testid" ? "user-message" : null),
      classList: { contains: (cls) => cls === "font-user-message" },
      closest: () => null,
      textContent: "What is an idempotent API?",
    };
    const asstTurn = {
      getAttribute: () => null,
      classList: { contains: (cls) => cls === "font-claude-message" },
      closest: () => null,
      textContent: "An idempotent API produces the same outcome when invoked repeatedly.",
    };

    const doc = createSyntheticDocument("", {
      title: "API Idempotence | Claude",
      url: "https://claude.ai/chat/api-idem",
      selectorMap: {
        '[data-testid="user-message"], .font-user-message, .font-claude-message, [data-is-streaming], div[class*="font-claude-message"]': [
          userTurn,
          asstTurn,
        ],
      },
    });

    const result = await adapter.captureConversation(doc);
    assert.equal(result.status, CaptureStatus.SUCCESS);
    assert.equal(result.title, "API Idempotence");
    assert.equal(result.messages.length, 2);
    assert.equal(result.messages[0].role, "USER");
    assert.equal(result.messages[1].role, "ASSISTANT");
  });
});

describe("Real Gemini DOM Adapter", () => {
  const adapter = new GeminiPageAdapter();

  test("identifies gemini.google.com domains", () => {
    assert.equal(adapter.canHandle("https://gemini.google.com/app"), true);
    assert.equal(adapter.canHandle("https://google.com"), false);
  });

  test("returns CAPTURE_UNAVAILABLE on empty page or missing elements", async () => {
    const emptyDoc = createSyntheticDocument("", { url: "https://gemini.google.com/app" });
    const result = await adapter.captureConversation(emptyDoc);
    assert.equal(result.status, CaptureStatus.CAPTURE_UNAVAILABLE);
  });

  test("extracts Gemini <user-query> and <model-response> custom elements", async () => {
    const uq = {
      tagName: "USER-QUERY",
      classList: { contains: () => false },
      querySelector: () => null,
      textContent: "Summarize OAuth 2.0 grant types",
      getAttribute: () => "gemini_msg_1",
    };
    const mr = {
      tagName: "MODEL-RESPONSE",
      classList: { contains: () => false },
      querySelector: () => null,
      textContent: "OAuth 2.0 defines Authorization Code, Client Credentials, and others.",
      getAttribute: () => "gemini_msg_2",
    };

    const doc = createSyntheticDocument("", {
      title: "Gemini - OAuth Summary",
      url: "https://gemini.google.com/app/oauth",
      selectorMap: {
        "user-query, model-response, .user-query-container, .response-container, div.chat-turn": [uq, mr],
      },
    });

    const result = await adapter.captureConversation(doc);
    assert.equal(result.status, CaptureStatus.SUCCESS);
    assert.equal(result.title, "OAuth Summary");
    assert.equal(result.messages.length, 2);
    assert.equal(result.messages[0].role, "USER");
    assert.equal(result.messages[1].role, "ASSISTANT");
  });
});
