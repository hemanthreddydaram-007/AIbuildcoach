import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const manifestPath = path.resolve(__dirname, "../manifest.json");

describe("Manifest V3 & Security Boundary Validation", () => {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  test("manifest_version is 3", () => {
    assert.equal(manifest.manifest_version, 3);
  });

  test("permissions request minimal surface area", () => {
    const permissions = manifest.permissions || [];
    assert.ok(permissions.includes("activeTab"), "Must include activeTab for user-triggered interaction");
    assert.ok(!permissions.includes("cookies"), "Must NEVER request cookies permission");
    assert.ok(!permissions.includes("webRequest"), "Must NEVER request webRequest monitoring");
    assert.ok(!permissions.includes("webNavigation"), "Must NEVER request webNavigation monitoring");
    assert.ok(!permissions.includes("<all_urls>"), "Must NEVER request all_urls in permissions");
    assert.ok(!permissions.includes("tabs"), "Must NOT request broad tabs permission");
  });

  test("host_permissions strictly limited to supported AI providers", () => {
    const hostPermissions = manifest.host_permissions || [];
    assert.ok(hostPermissions.length > 0, "Must specify host permissions");

    for (const host of hostPermissions) {
      const isChatGPT = host.includes("chatgpt.com") || host.includes("chat.openai.com");
      const isClaude = host.includes("claude.ai");
      const isGemini = host.includes("gemini.google.com");
      assert.ok(
        isChatGPT || isClaude || isGemini,
        `Host permission ${host} must strictly be ChatGPT, Claude, or Gemini`
      );
      assert.notEqual(host, "<all_urls>", "Must never contain <all_urls>");
      assert.notEqual(host, "*://*/*", "Must never contain wildcard host");
    }
  });

  test("background service worker is configured as ES module", () => {
    assert.ok(manifest.background, "Must specify background");
    assert.equal(manifest.background.service_worker, "background.js");
    assert.equal(manifest.background.type, "module");
  });

  test("action defines popup view", () => {
    assert.ok(manifest.action);
    assert.equal(manifest.action.default_popup, "popup/popup.html");
  });
});
