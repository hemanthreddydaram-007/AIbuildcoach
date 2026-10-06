import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  FIXED_BRIDGE_ORIGIN,
  BRIDGE_PROTOCOL_V1,
  LOCAL_PERMISSION_PATTERN,
  hasLocalPermission,
  requestLocalPermission,
  checkConnection,
  sendCapture,
} from "../src/bridge/local_bridge.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

describe("Local Bridge Client Protocol & Security Boundary", () => {
  let origChrome;
  let origFetch;

  beforeEach(() => {
    origChrome = globalThis.chrome;
    origFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.chrome = origChrome;
    globalThis.fetch = origFetch;
  });

  test("communicates exclusively with fixed localhost origin 127.0.0.1:8765", () => {
    assert.equal(FIXED_BRIDGE_ORIGIN, "http://127.0.0.1:8765");
    assert.equal(LOCAL_PERMISSION_PATTERN, "http://127.0.0.1/*");
  });

  test("rejects arbitrary external or cloud URLs", async () => {
    await assert.rejects(
      async () => checkConnection("https://api.buildcoach.io"),
      /Security violation/
    );
    await assert.rejects(
      async () => checkConnection("http://192.168.1.50:8765"),
      /Security violation/
    );
    await assert.rejects(
      async () => sendCapture({ provider: "CHATGPT" }, "https://cloud.example.com"),
      /Security violation/
    );
  });

  test("permission request calls chrome.permissions.request with strictly local origin", async () => {
    let requestedOrigins = null;
    globalThis.chrome = {
      permissions: {
        contains: (query, cb) => cb(false),
        request: (query, cb) => {
          requestedOrigins = query.origins;
          cb(true);
        },
      },
    };

    const hasPerm = await hasLocalPermission();
    assert.equal(hasPerm, false);

    const granted = await requestLocalPermission();
    assert.equal(granted, true);
    assert.deepEqual(requestedOrigins, ["http://127.0.0.1/*"]);
  });

  test("permission request handles user denial safely", async () => {
    globalThis.chrome = {
      permissions: {
        contains: (query, cb) => cb(false),
        request: (query, cb) => cb(false),
      },
    };

    const granted = await requestLocalPermission();
    assert.equal(granted, false);
  });

  test("checkConnection succeeds when bridge returns healthy v1 protocol response", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/health");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          request_id: "req_health",
          ok: true,
          message_type: "health_result",
          result: {
            service: "ai-build-coach",
            bridge: true,
            protocol: BRIDGE_PROTOCOL_V1,
          },
        }),
      };
    };

    const res = await checkConnection();
    assert.equal(res.connected, true);
    assert.equal(res.protocol, BRIDGE_PROTOCOL_V1);
    assert.equal(res.service, "ai-build-coach");
  });

  test("checkConnection handles bridge unavailable / connection refused gracefully", async () => {
    globalThis.fetch = async () => {
      throw new Error("ECONNREFUSED 127.0.0.1:8765");
    };

    const res = await checkConnection();
    assert.equal(res.connected, false);
    assert.ok(res.error.includes("ECONNREFUSED"));
  });

  test("checkConnection rejects invalid protocol response", async () => {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        protocol: "invalid-proto-v99",
        ok: true,
        result: { bridge: true },
      }),
    });

    const res = await checkConnection();
    assert.equal(res.connected, false);
    assert.equal(res.error, "Invalid bridge protocol response.");
  });

  test("checkConnection handles HTTP error response", async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 503,
      json: async () => ({}),
    });

    const res = await checkConnection();
    assert.equal(res.connected, false);
    assert.ok(res.error.includes("503"));
  });

  test("sendCapture transmits valid envelope and receives capture result", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/capture");
      assert.equal(opts.method, "POST");
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          request_id: capturedBody.request_id,
          ok: true,
          message_type: "capture_result",
          result: {
            conversation_id: "conv_ext_123",
            message_count: 2,
            provider: "chatgpt",
            stored: true,
          },
        }),
      };
    };

    const payload = {
      provider: "CHATGPT",
      title: "Bug discussion",
      messages: [
        { role: "USER", content: "Check bug" },
        { role: "ASSISTANT", content: "Bug resolved" },
      ],
    };

    const res = await sendCapture(payload);
    assert.equal(res.ok, true);
    assert.equal(res.result.conversation_id, "conv_ext_123");
    assert.equal(res.result.message_count, 2);
    assert.equal(res.result.stored, true);

    // Verify envelope fields
    assert.equal(capturedBody.protocol, BRIDGE_PROTOCOL_V1);
    assert.equal(capturedBody.message_type, "capture");
    assert.ok(capturedBody.request_id.startsWith("req_"));
    assert.ok(capturedBody.timestamp);
    assert.deepEqual(capturedBody.payload, payload);
  });

  test("sendCapture handles structured error from local bridge", async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 400,
      json: async () => ({
        protocol: BRIDGE_PROTOCOL_V1,
        ok: false,
        error: {
          code: "EMPTY_CONVERSATION",
          message: "Capture payload must contain at least one message turn.",
        },
      }),
    });

    const res = await sendCapture({ provider: "CLAUDE", messages: [] });
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "EMPTY_CONVERSATION");
    assert.equal(res.error.message, "Capture payload must contain at least one message turn.");
  });

  test("sendCapture rejects missing payload or provider before making network call", async () => {
    let fetchCalled = false;
    globalThis.fetch = async () => {
      fetchCalled = true;
      return { ok: true };
    };

    const res1 = await sendCapture(null);
    assert.equal(res1.ok, false);
    assert.equal(res1.error.code, "INVALID_PAYLOAD");
    assert.equal(fetchCalled, false);

    const res2 = await sendCapture({});
    assert.equal(res2.ok, false);
    assert.equal(res2.error.code, "INVALID_PAYLOAD");
    assert.equal(fetchCalled, false);
  });

  test("privacy invariant: manifest does not contain persistent storage or provider hosts", () => {
    const manifestPath = path.resolve(__dirname, "../manifest.json");
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    const permissions = manifest.permissions || [];
    const hostPermissions = manifest.host_permissions || [];

    assert.ok(!permissions.includes("storage"), "Storage must NOT be requested");
    assert.ok(!hostPermissions.includes("https://chatgpt.com/*"));
    assert.ok(!hostPermissions.includes("https://claude.ai/*"));
    assert.ok(!hostPermissions.includes("https://gemini.google.com/*"));
  });
});
