import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { handleSendToBuildCoach } from "../background.js";
import { prepareTransferPayload } from "../src/bridge.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

describe("In-Memory Bridge Transfer & Storage-Free Invariant", () => {
  test("background.js source code contains zero references to chrome.storage", () => {
    const backgroundPath = path.resolve(__dirname, "../background.js");
    const content = fs.readFileSync(backgroundPath, "utf-8");
    assert.ok(
      !content.includes("chrome.storage"),
      "Invariant violated: background.js must not reference chrome.storage"
    );
    assert.ok(
      !content.includes("storage.local"),
      "Invariant violated: background.js must not reference storage.local"
    );
  });

  test("handleSendToBuildCoach executes safely when chrome.storage is undefined", async () => {
    // Ensure global chrome has no storage property
    const origChrome = globalThis.chrome;
    globalThis.chrome = {};

    try {
      const sampleConversation = {
        provider: "CHATGPT",
        title: "Test Conversation",
        messages: [
          { role: "USER", content: "Check auth function", sequence: 1 },
          { role: "ASSISTANT", content: "Auth function is fixed", sequence: 2 },
        ],
      };

      const result = await handleSendToBuildCoach({
        conversation: sampleConversation,
        projectId: "prj_test",
      });

      assert.equal(result.status, "BRIDGE_NOT_CONNECTED");
      assert.ok(result.message.includes("Copy JSON"));
      assert.equal(result.provider, "CHATGPT");
      assert.equal(result.message_count, 2);
      assert.ok(result.conversation_id.startsWith("conv_ext_"));
      assert.equal(result.payload.project_id, "prj_test");
      assert.equal(result.payload.source, "WEB_EXTENSION");
    } finally {
      globalThis.chrome = origChrome;
    }
  });

  test("handleSendToBuildCoach never writes to persistent storage even if storage mock is provided", async () => {
    let storageWritten = false;
    const origChrome = globalThis.chrome;
    globalThis.chrome = {
      storage: {
        local: {
          set: async () => {
            storageWritten = true;
          },
        },
      },
    };

    try {
      const sampleConversation = {
        provider: "CLAUDE",
        title: "Claude Review",
        messages: [
          { role: "USER", content: "Hello Claude", sequence: 1 },
        ],
      };

      const result = await handleSendToBuildCoach({
        conversation: sampleConversation,
      });

      assert.equal(result.status, "BRIDGE_NOT_CONNECTED");
      assert.equal(storageWritten, false, "Invariant violated: handleSendToBuildCoach must not write to chrome.storage");
    } finally {
      globalThis.chrome = origChrome;
    }
  });

  test("handleSendToBuildCoach rejects missing conversation payload", async () => {
    await assert.rejects(
      async () => handleSendToBuildCoach(null),
      /Missing conversation payload/
    );
    await assert.rejects(
      async () => handleSendToBuildCoach({}),
      /Missing conversation payload/
    );
  });

  test("Copy JSON payload formatting produces valid M11.0 JSON string without persistence", () => {
    const sampleConversation = {
      provider: "GEMINI",
      title: "Gemini Analysis",
      messages: [
        { role: "USER", content: "Analyze memory leak", sequence: 1 },
        { role: "ASSISTANT", content: "Found circular reference", sequence: 2 },
      ],
    };

    const finalPayload = prepareTransferPayload(sampleConversation, "prj_gemini_test");
    const jsonString = JSON.stringify(finalPayload, null, 2);

    assert.ok(typeof jsonString === "string");
    const parsed = JSON.parse(jsonString);
    assert.equal(parsed.provider, "GEMINI");
    assert.equal(parsed.source, "WEB_EXTENSION");
    assert.equal(parsed.project_id, "prj_gemini_test");
    assert.equal(parsed.messages.length, 2);
  });
});
