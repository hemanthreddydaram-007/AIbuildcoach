import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const manifestPath = path.resolve(__dirname, "../manifest.json");

describe("Manifest V3 & Refined Permission Boundary Validation", () => {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  test("manifest_version is 3", () => {
    assert.equal(manifest.manifest_version, 3);
  });

  test("permissions are strictly minimized to activeTab and scripting", () => {
    const permissions = manifest.permissions || [];
    assert.ok(permissions.includes("activeTab"), "Must include activeTab for user-triggered interaction");
    assert.ok(permissions.includes("scripting"), "Must include scripting for on-demand in-page execution");

    // Strictly forbidden permissions
    assert.ok(!permissions.includes("cookies"), "Must NEVER request cookies permission");
    assert.ok(!permissions.includes("webRequest"), "Must NEVER request webRequest monitoring");
    assert.ok(!permissions.includes("webNavigation"), "Must NEVER request webNavigation monitoring");
    assert.ok(!permissions.includes("<all_urls>"), "Must NEVER request all_urls");
    assert.ok(!permissions.includes("tabs"), "Must NOT request broad tabs permission");
  });

  test("unnecessary host_permissions are completely absent", () => {
    assert.ok(
      !manifest.host_permissions || manifest.host_permissions.length === 0,
      "host_permissions must be removed to avoid broad install-time warnings"
    );
  });

  test("static content_scripts are removed in favor of explicit on-demand execution", () => {
    assert.ok(
      !manifest.content_scripts || manifest.content_scripts.length === 0,
      "Static content_scripts must not be registered; capture is purely user-triggered via scripting"
    );
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
