import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { generatePreview, prepareTransferPayload } from "../src/bridge.js";

describe("Preview Generation Before Transfer", () => {
  test("generates preview with correct message count and snippets", () => {
    const payload = {
      provider: "CHATGPT",
      title: "Refactor Discussion",
      messages: [
        { role: "USER", content: "Can you help refactor auth.py?" },
        { role: "ASSISTANT", content: "Sure, let's break down the changes into smaller steps." },
      ],
    };

    const preview = generatePreview(payload, "prj_test_123");
    assert.equal(preview.provider, "CHATGPT");
    assert.equal(preview.title, "Refactor Discussion");
    assert.equal(preview.messageCount, 2);
    assert.equal(preview.projectId, "prj_test_123");
    assert.equal(preview.canTransfer, true);
    assert.equal(preview.firstMessagePreview.role, "USER");
    assert.ok(preview.firstMessagePreview.snippet.includes("refactor auth.py"));
    assert.equal(preview.lastMessagePreview.role, "ASSISTANT");
    assert.ok(preview.lastMessagePreview.snippet.includes("smaller steps"));
  });

  test("empty conversation preview sets canTransfer to false", () => {
    const preview = generatePreview({ provider: "CLAUDE", title: "Empty", messages: [] });
    assert.equal(preview.messageCount, 0);
    assert.equal(preview.canTransfer, false);
    assert.equal(preview.firstMessagePreview, null);
    assert.equal(preview.lastMessagePreview, null);
  });

  test("reports detected secret count in preview", () => {
    const payload = {
      provider: "GEMINI",
      title: "Secret discussion",
      messages: [
        {
          role: "USER",
          content: "Here is key [REDACTED:OPENAI_API_KEY]",
          metadata: { secrets_detected: 1 },
        },
        {
          role: "ASSISTANT",
          content: "Here is token [REDACTED:GITHUB_TOKEN]",
          metadata: { secrets_detected: 2 },
        },
      ],
    };

    const preview = generatePreview(payload);
    assert.equal(preview.detectedSecretCount, 3);
  });
});

describe("Transfer Payload Schema Compatibility with M11.0", () => {
  test("prepareTransferPayload produces M11.0 compatible Conversation model schema", () => {
    const raw = {
      provider: "CLAUDE",
      title: "API Design Review",
      messages: [
        { role: "USER", content: "Let's review the API key sk-ant-12345678901234567890123456", sequence: 1 },
        { role: "ASSISTANT", content: "The key was redacted safely.", sequence: 2 },
      ],
      metadata: { originalUrl: "https://claude.ai/chat/1" },
    };

    const transfer = prepareTransferPayload(raw, "prj_sample_1");

    // Required M11.0 fields
    assert.ok(transfer.conversation_id.startsWith("conv_ext_"));
    assert.equal(transfer.provider, "CLAUDE");
    assert.equal(transfer.source, "WEB_EXTENSION");
    assert.equal(transfer.title, "API Design Review");
    assert.equal(transfer.project_id, "prj_sample_1");
    assert.ok(transfer.created_at);
    assert.ok(transfer.updated_at);
    assert.equal(transfer.messages.length, 2);

    // Messages schema
    assert.equal(transfer.messages[0].role, "USER");
    assert.equal(transfer.messages[0].sequence, 1);
    assert.ok(transfer.messages[0].content.includes("[REDACTED:ANTHROPIC_API_KEY]"));
    assert.ok(!transfer.messages[0].content.includes("sk-ant-12345678901234567890123456"));
    assert.equal(transfer.messages[0].metadata.redacted, true);

    // Metadata schema
    assert.equal(transfer.metadata.source, "WEB_EXTENSION");
    assert.equal(transfer.metadata.extension_version, "1.0.0");
    assert.equal(transfer.metadata.total_secrets_redacted, 1);
    assert.deepEqual(transfer.metadata.redacted_secret_types, ["ANTHROPIC_API_KEY"]);
  });

  test("rejects payload without provider", () => {
    assert.throws(() => prepareTransferPayload({}), /missing provider/);
    assert.throws(() => prepareTransferPayload(null), /missing provider/);
  });
});
