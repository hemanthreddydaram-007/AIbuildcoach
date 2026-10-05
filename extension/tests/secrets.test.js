import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { scanAndRedact, redactMessages } from "../src/secrets.js";

describe("Client-Side Secret Detection & Redaction", () => {
  test("redacts OpenAI API keys", () => {
    const text = "Use sk-abcdef1234567890abcdef123456 for authentication.";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 1);
    assert.deepEqual(result.detectedTypes, ["OPENAI_API_KEY"]);
    assert.ok(!result.sanitizedText.includes("sk-abcdef"));
    assert.ok(result.sanitizedText.includes("[REDACTED:OPENAI_API_KEY]"));
  });

  test("redacts Anthropic API keys", () => {
    const text = "Here is my key: sk-ant-api03-123456789012345678901234567890";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 1);
    assert.deepEqual(result.detectedTypes, ["ANTHROPIC_API_KEY"]);
    assert.ok(!result.sanitizedText.includes("sk-ant-"));
    assert.ok(result.sanitizedText.includes("[REDACTED:ANTHROPIC_API_KEY]"));
  });

  test("redacts GitHub tokens", () => {
    const text = "Clone using ghp_123456789012345678901234567890123456 as credentials.";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 1);
    assert.deepEqual(result.detectedTypes, ["GITHUB_TOKEN"]);
    assert.ok(result.sanitizedText.includes("[REDACTED:GITHUB_TOKEN]"));
  });

  test("redacts AWS access keys", () => {
    const text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 1);
    assert.deepEqual(result.detectedTypes, ["AWS_ACCESS_KEY"]);
    assert.ok(result.sanitizedText.includes("[REDACTED:AWS_ACCESS_KEY]"));
  });

  test("redacts generic bearer tokens", () => {
    const text = "Authorization: Bearer secret_bearer_token_1234567890_long";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 1);
    assert.deepEqual(result.detectedTypes, ["BEARER_TOKEN"]);
    assert.ok(result.sanitizedText.includes("[REDACTED:BEARER_TOKEN]"));
  });

  test("handles text without secrets without alteration", () => {
    const text = "Let's review the refactoring of auth.py and database models.";
    const result = scanAndRedact(text);
    assert.equal(result.detectedCount, 0);
    assert.deepEqual(result.detectedTypes, []);
    assert.equal(result.sanitizedText, text);
  });

  test("redactMessages processes array and records metadata", () => {
    const messages = [
      {
        message_id: "m1",
        role: "USER",
        content: "Here is key sk-1234567890abcdef1234567890",
        sequence: 1,
      },
      {
        message_id: "m2",
        role: "ASSISTANT",
        content: "I acknowledge your safe message.",
        sequence: 2,
      },
    ];

    const result = redactMessages(messages);
    assert.equal(result.totalSecretsRedacted, 1);
    assert.deepEqual(result.detectedTypes, ["OPENAI_API_KEY"]);
    assert.equal(result.sanitizedMessages[0].metadata.redacted, true);
    assert.equal(result.sanitizedMessages[0].metadata.secrets_detected, 1);
    assert.equal(result.sanitizedMessages[1].metadata.redacted, undefined);
  });
});
