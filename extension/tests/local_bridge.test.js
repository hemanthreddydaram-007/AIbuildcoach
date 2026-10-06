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
  listProjects,
  bindConversation,
  getBindingStatus,
  analyzeEvidence,
  getGuidance,
  getSession,
  getUnderstanding,
  refreshSession,
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

  test("listProjects retrieves projects and preserves only project_id and display_name", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/projects");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          result: {
            projects: [
              { project_id: "prj_001", display_name: "Web Portal" },
              { project_id: "prj_002", display_name: "Auth Service" },
            ],
          },
        }),
      };
    };

    const res = await listProjects();
    assert.equal(res.ok, true);
    assert.equal(res.projects.length, 2);
    assert.equal(res.projects[0].project_id, "prj_001");
    assert.equal(res.projects[0].display_name, "Web Portal");
    assert.equal(res.projects[0].root_path, undefined);
  });

  test("listProjects handles bridge unavailable safely", async () => {
    globalThis.fetch = async () => {
      throw new Error("fetch failed");
    };

    const res = await listProjects();
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "BRIDGE_UNAVAILABLE");
  });

  test("bindConversation sends strictly project_id and rejects paths", async () => {
    let capturedUrl = null;
    let capturedBody = null;

    globalThis.fetch = async (url, options) => {
      capturedUrl = url;
      capturedBody = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          result: {
            conversation_id: "conv_test_123",
            project_id: "prj_valid_99",
            binding_source: "USER_SELECTED",
          },
        }),
      };
    };

    const res = await bindConversation("conv_test_123", "prj_valid_99");
    assert.equal(res.ok, true);
    assert.equal(capturedUrl, "http://127.0.0.1:8765/v1/conversations/conv_test_123/bind");
    assert.deepEqual(capturedBody, { project_id: "prj_valid_99" });

    // Invariant proof: OUTBOUND payload has project_id and never path/filesystem identifiers
    assert.ok(capturedBody.project_id);
    assert.equal(capturedBody.path, undefined);
    assert.equal(capturedBody.root, undefined);
    assert.equal(capturedBody.filesystem_path, undefined);
    assert.equal(capturedBody.directory, undefined);
    assert.equal(capturedBody.cwd, undefined);
  });

  test("bindConversation rejects missing arguments without making network calls", async () => {
    let called = false;
    globalThis.fetch = async () => {
      called = true;
      return { ok: true };
    };

    const res1 = await bindConversation("", "prj_1");
    assert.equal(res1.ok, false);
    assert.equal(res1.error.code, "INVALID_CONVERSATION_ID");
    assert.equal(called, false);

    const res2 = await bindConversation("conv_1", "");
    assert.equal(res2.ok, false);
    assert.equal(res2.error.code, "INVALID_PROJECT_ID");
    assert.equal(called, false);
  });

  test("getBindingStatus returns bound status and project summary", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/conversations/conv_abc/binding");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          result: {
            conversation_id: "conv_abc",
            bound: true,
            project: {
              project_id: "prj_alpha",
              display_name: "Alpha Project",
            },
          },
        }),
      };
    };

    const res = await getBindingStatus("conv_abc");
    assert.equal(res.ok, true);
    assert.equal(res.result.bound, true);
    assert.equal(res.result.project.project_id, "prj_alpha");
    assert.equal(res.result.project.display_name, "Alpha Project");
  });

  test("analyzeEvidence rejects non-local origins", async () => {
    await assert.rejects(
      async () => analyzeEvidence("conv_123", "http://malicious.evil.com"),
      /Security violation: Bridge client will not connect to non-local origin/
    );
  });

  test("analyzeEvidence rejects empty conversationId", async () => {
    let called = false;
    globalThis.fetch = async () => {
      called = true;
      return { ok: true };
    };

    const res = await analyzeEvidence("");
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "INVALID_CONVERSATION_ID");
    assert.equal(called, false);
  });

  test("analyzeEvidence transmits evidence request and returns structured result", async () => {
    let capturedBody = null;
    globalThis.fetch = async (url, opts) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/conversations/conv_456/evidence");
      assert.equal(opts.method, "POST");
      capturedBody = JSON.parse(opts.body);
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          request_id: capturedBody.request_id,
          ok: true,
          message_type: "evidence_result",
          result: {
            conversation_id: "conv_456",
            project_id: "proj_beta",
            claims_count: 2,
            evidence_links_count: 4,
            summary: { total_claims: 2, total_links: 4 },
            claims: [{ claim_id: "claim_1", status: "SUPPORTED" }],
            evidence_links: [{ link_id: "link_1", relation: "SUPPORTS" }],
          },
        }),
      };
    };

    const res = await analyzeEvidence("conv_456");
    assert.equal(res.ok, true);
    assert.equal(capturedBody.protocol, BRIDGE_PROTOCOL_V1);
    assert.equal(capturedBody.message_type, "evidence");
    assert.equal(res.result.conversation_id, "conv_456");
    assert.equal(res.result.project_id, "proj_beta");
    assert.equal(res.result.claims_count, 2);
    assert.equal(res.result.evidence_links_count, 4);
  });

  test("analyzeEvidence handles PROJECT_BINDING_REQUIRED error from bridge", async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        protocol: BRIDGE_PROTOCOL_V1,
        ok: false,
        error: {
          code: "PROJECT_BINDING_REQUIRED",
          message: "Conversation must be bound to a project before analyzing evidence.",
        },
      }),
    });

    const res = await analyzeEvidence("conv_unbound");
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "PROJECT_BINDING_REQUIRED");
  });

  test("getGuidance rejects non-local origins", async () => {
    await assert.rejects(
      async () => getGuidance("prj_1", "http://evil.com"),
      /Security violation: Bridge client will not connect to non-local origin/
    );
  });

  test("getGuidance rejects empty projectId", async () => {
    const res = await getGuidance("");
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "INVALID_PROJECT_ID");
  });

  test("getGuidance retrieves guidance result successfully", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/projects/prj_123/guidance");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          message_type: "guidance_result",
          result: {
            project_id: "prj_123",
            top_next_action: { action_type: "RUN_TEST", priority: "HIGH" },
            gaps: [{ category: "TEST_COVERAGE" }],
          },
        }),
      };
    };

    const res = await getGuidance("prj_123");
    assert.equal(res.ok, true);
    assert.equal(res.result.project_id, "prj_123");
    assert.equal(res.result.top_next_action.action_type, "RUN_TEST");
  });

  test("getSession rejects non-local origins", async () => {
    await assert.rejects(
      async () => getSession("prj_1", "http://evil.com"),
      /Security violation: Bridge client will not connect to non-local origin/
    );
  });

  test("getSession rejects empty projectId", async () => {
    const res = await getSession("");
    assert.equal(res.ok, false);
    assert.equal(res.error.code, "INVALID_PROJECT_ID");
  });

  test("getSession retrieves unified session result successfully", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/projects/prj_123/session");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          message_type: "session_result",
          result: {
            session_id: "session_prj_123",
            project_id: "prj_123",
            state: "ACTION_REQUIRED",
            summary: { recent_changes: 3, active_incidents: 0, recovered_incidents: 1 },
            verification: { status: "RECOVERED", targeted_test_observed: false },
            understanding: { required: true },
            next_action: { action_type: "RUN_TEST", priority: "HIGH" },
          },
        }),
      };
    };

    const res = await getSession("prj_123");
    assert.equal(res.ok, true);
    assert.equal(res.result.project_id, "prj_123");
    assert.equal(res.result.state, "ACTION_REQUIRED");
    assert.equal(res.result.next_action.action_type, "RUN_TEST");
  });

  test("getUnderstanding rejects non-local origins", async () => {
    await assert.rejects(
      async () => getUnderstanding("prj_1", "http://evil.com"),
      /Security violation: Bridge client will not connect to non-local origin/
    );
  });

  test("getUnderstanding retrieves comprehension status successfully", async () => {
    globalThis.fetch = async (url) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/projects/prj_123/understanding");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          message_type: "understanding_result",
          result: {
            project_id: "prj_123",
            status: "EVALUATED",
            overall_state: "UNDERSTOOD",
            gap_count: 0,
            requires_explanation: false,
          },
        }),
      };
    };

    const res = await getUnderstanding("prj_123");
    assert.equal(res.ok, true);
    assert.equal(res.result.status, "EVALUATED");
    assert.equal(res.result.overall_state, "UNDERSTOOD");
  });

  test("refreshSession sends POST and retrieves updated session", async () => {
    globalThis.fetch = async (url, opts) => {
      assert.equal(url, "http://127.0.0.1:8765/v1/projects/prj_123/refresh");
      assert.equal(opts.method, "POST");
      return {
        ok: true,
        json: async () => ({
          protocol: BRIDGE_PROTOCOL_V1,
          ok: true,
          message_type: "session_result",
          result: {
            session_id: "session_prj_123",
            project_id: "prj_123",
            state: "STABLE",
            fix_status: "VERIFIED",
            what_happened: "All tests passing cleanly.",
          },
        }),
      };
    };

    const res = await refreshSession("prj_123");
    assert.equal(res.ok, true);
    assert.equal(res.result.state, "STABLE");
    assert.equal(res.result.fix_status, "VERIFIED");
  });
});



