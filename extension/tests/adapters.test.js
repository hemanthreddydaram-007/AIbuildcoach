import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { ProviderPageAdapter } from "../src/adapters/base.js";
import { ChatGPTPageAdapter } from "../src/adapters/chatgpt.js";
import { ClaudePageAdapter } from "../src/adapters/claude.js";
import { GeminiPageAdapter } from "../src/adapters/gemini.js";
import { ProviderAdapterFactory, defaultAdapterFactory } from "../src/adapters/factory.js";

describe("ProviderPageAdapter Base Contract", () => {
  test("base class throws unimplemented errors", async () => {
    const base = new ProviderPageAdapter();
    assert.throws(() => base.canHandle("https://example.com"), /must be implemented/);
    assert.throws(() => base.getProvider(), /must be implemented/);
    await assert.rejects(() => base.captureConversation(null), /must be implemented/);
  });

  test("normalizeConversation validates input and normalizes roles and sequences", () => {
    const adapter = new ChatGPTPageAdapter();
    assert.throws(() => adapter.normalizeConversation(null), /invalid or empty/);
    assert.throws(() => adapter.normalizeConversation("string"), /invalid or empty/);

    const raw = {
      title: "Test Conversation",
      messages: [
        { role: "user", content: "Hello AI", sequence: 1 },
        { role: "assistant", content: "Hello human", sequence: 2 },
        { role: "invalid_role", content: "Fallback test", sequence: 3 },
      ],
      rawMetadata: { sampleKey: "sampleVal" },
    };

    const normalized = adapter.normalizeConversation(raw);
    assert.equal(normalized.provider, "CHATGPT");
    assert.equal(normalized.source, "WEB_EXTENSION");
    assert.equal(normalized.title, "Test Conversation");
    assert.equal(normalized.messages.length, 3);
    assert.equal(normalized.messages[0].role, "USER");
    assert.equal(normalized.messages[1].role, "ASSISTANT");
    assert.equal(normalized.messages[2].role, "USER"); // Fallback for invalid role
    assert.equal(normalized.messages[0].sequence, 1);
    assert.equal(normalized.messages[1].sequence, 2);
    assert.equal(normalized.messages[2].sequence, 3);
    assert.equal(normalized.metadata.sampleKey, "sampleVal");
  });

  test("empty conversation normalization produces valid empty array", () => {
    const adapter = new ClaudePageAdapter();
    const normalized = adapter.normalizeConversation({ title: "Empty", messages: [] });
    assert.equal(normalized.messages.length, 0);
    assert.equal(normalized.provider, "CLAUDE");
  });

  test("malformed messages are defensively sanitized", () => {
    const adapter = new GeminiPageAdapter();
    const raw = {
      messages: [
        { role: null, content: null },
        { role: 123, content: 456 },
      ],
    };
    const normalized = adapter.normalizeConversation(raw);
    assert.equal(normalized.messages.length, 2);
    assert.equal(normalized.messages[0].role, "USER");
    assert.equal(normalized.messages[0].content, "");
    assert.equal(normalized.messages[1].content, "");
  });
});

describe("Provider Identification", () => {
  test("ChatGPT adapter identifies valid domains and subdomains", () => {
    const adapter = new ChatGPTPageAdapter();
    assert.equal(adapter.canHandle("https://chatgpt.com/c/123-abc"), true);
    assert.equal(adapter.canHandle("https://chat.openai.com/chat"), true);
    assert.equal(adapter.canHandle("https://subdomain.chatgpt.com/"), true);
    assert.equal(adapter.canHandle("https://claude.ai/chat"), false);
    assert.equal(adapter.canHandle("https://google.com"), false);
    assert.equal(adapter.canHandle("not-a-url"), false);
    assert.equal(adapter.canHandle(null), false);
    assert.equal(adapter.getProvider(), "CHATGPT");
  });

  test("Claude adapter identifies valid domains", () => {
    const adapter = new ClaudePageAdapter();
    assert.equal(adapter.canHandle("https://claude.ai/chat/abc-123"), true);
    assert.equal(adapter.canHandle("https://sub.claude.ai/"), true);
    assert.equal(adapter.canHandle("https://chatgpt.com/"), false);
    assert.equal(adapter.canHandle("https://gemini.google.com/"), false);
    assert.equal(adapter.getProvider(), "CLAUDE");
  });

  test("Gemini adapter identifies valid domains", () => {
    const adapter = new GeminiPageAdapter();
    assert.equal(adapter.canHandle("https://gemini.google.com/app"), true);
    assert.equal(adapter.canHandle("https://gemini.google.com/u/1/app"), true);
    assert.equal(adapter.canHandle("https://google.com/search"), false);
    assert.equal(adapter.canHandle("https://claude.ai"), false);
    assert.equal(adapter.getProvider(), "GEMINI");
  });
});

describe("ProviderAdapterFactory", () => {
  test("resolves correct adapter for each supported provider URL", () => {
    const factory = defaultAdapterFactory;

    const chatgpt = factory.getAdapter("https://chatgpt.com/c/123");
    assert.ok(chatgpt instanceof ChatGPTPageAdapter);
    assert.equal(chatgpt.getProvider(), "CHATGPT");

    const claude = factory.getAdapter("https://claude.ai/chat/456");
    assert.ok(claude instanceof ClaudePageAdapter);
    assert.equal(claude.getProvider(), "CLAUDE");

    const gemini = factory.getAdapter("https://gemini.google.com/app/789");
    assert.ok(gemini instanceof GeminiPageAdapter);
    assert.equal(gemini.getProvider(), "GEMINI");
  });

  test("returns null for unsupported URLs and providers", () => {
    const factory = defaultAdapterFactory;
    assert.equal(factory.getAdapter("https://github.com"), null);
    assert.equal(factory.getAdapter("https://stackoverflow.com"), null);
    assert.equal(factory.getAdapter("https://bing.com"), null);
    assert.equal(factory.getAdapter(""), null);
    assert.equal(factory.getAdapter(null), null);
    assert.equal(factory.isSupported("https://random-site.com"), false);
  });

  test("reports all supported providers list", () => {
    const factory = defaultAdapterFactory;
    const providers = factory.getSupportedProviders();
    assert.deepEqual(providers, ["CHATGPT", "CLAUDE", "GEMINI"]);
  });
});

describe("Synthetic DOM Extraction", () => {
  test("ChatGPT adapter extracts synthetic conversation correctly", async () => {
    const adapter = new ChatGPTPageAdapter();
    const syntheticMessages = [
      { role: "USER", content: "How do I fix bug #42?", sequence: 1 },
      { role: "ASSISTANT", content: "You should modify auth.py to fix bug #42.", sequence: 2 },
    ];

    const captured = await adapter.captureConversation(null, {
      title: "Bug 42 Discussion",
      syntheticMessages,
    });

    assert.equal(captured.title, "Bug 42 Discussion");
    assert.equal(captured.messages.length, 2);
    assert.equal(captured.messages[0].content, "How do I fix bug #42?");
    assert.equal(captured.messages[1].content, "You should modify auth.py to fix bug #42.");
    assert.equal(captured.rawMetadata.synthetic, true);
  });
});
