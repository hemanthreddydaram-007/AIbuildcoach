/**
 * Popup UI Controller for AI Build Coach Browser Extension.
 *
 * Enforces strictly user-controlled capture and preview-before-transfer invariant:
 * 1. Checks provider compatibility purely via activeTab.url (NO script injection on popup open).
 * 2. Injects capture script ONLY after user clicks "Capture Conversation" via chrome.scripting.executeScript.
 * 3. Previews in-memory; discards payload if popup closes or user cancels.
 * 4. Transfers ONLY after explicit user confirmation ("Send to Build Coach").
 */

import { defaultAdapterFactory } from "../src/adapters/factory.js";
import { MessageType, createMessage } from "../src/messages.js";
import { generatePreview, prepareTransferPayload } from "../src/bridge.js";

// DOM Elements
const providerBadge = document.getElementById("provider-badge");
const pageTitleEl = document.getElementById("page-title");
const btnCapture = document.getElementById("btn-capture");
const btnSend = document.getElementById("btn-send");
const btnCopyJson = document.getElementById("btn-copy-json");
const btnCancel = document.getElementById("btn-cancel");
const btnDone = document.getElementById("btn-done");
const inputProjectId = document.getElementById("input-project-id");

// States
const stateUnsupported = document.getElementById("state-unsupported");
const stateReady = document.getElementById("state-ready");
const stateCapturing = document.getElementById("state-capturing");
const statePreview = document.getElementById("state-preview");
const stateSuccess = document.getElementById("state-success");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");

// Preview Elements
const previewProvider = document.getElementById("preview-provider");
const previewSecrets = document.getElementById("preview-secrets");
const previewTitle = document.getElementById("preview-title");
const previewMsgCount = document.getElementById("preview-msg-count");
const roleFirst = document.getElementById("role-first");
const contentFirst = document.getElementById("content-first");
const roleLast = document.getElementById("role-last");
const contentLast = document.getElementById("content-last");

// Ephemeral in-memory state (NEVER persisted or transferred without confirmation)
let activeTab = null;
let currentPayload = null;

function showState(targetState) {
  [stateUnsupported, stateReady, stateCapturing, statePreview, stateSuccess].forEach((s) => {
    s.classList.add("hidden");
  });
  targetState.classList.remove("hidden");
  errorBanner.classList.add("hidden");
}

function showError(msg) {
  errorText.textContent = msg;
  errorBanner.classList.remove("hidden");
}

function setProviderBadge(provider) {
  providerBadge.className = "badge";
  if (provider === "CHATGPT") {
    providerBadge.classList.add("badge-chatgpt");
    providerBadge.textContent = "ChatGPT";
  } else if (provider === "CLAUDE") {
    providerBadge.classList.add("badge-claude");
    providerBadge.textContent = "Claude";
  } else if (provider === "GEMINI") {
    providerBadge.classList.add("badge-gemini");
    providerBadge.textContent = "Gemini";
  } else {
    providerBadge.classList.add("badge-unsupported");
    providerBadge.textContent = "Unsupported";
  }
}

// 1. Initial status check on popup open (Zero script execution, purely URL based)
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) {
      showState(stateUnsupported);
      return;
    }

    activeTab = tabs[0];
    const url = activeTab.url || "";
    const adapter = defaultAdapterFactory.getAdapter(url);

    if (!adapter) {
      setProviderBadge(null);
      showState(stateUnsupported);
      return;
    }

    const provider = adapter.getProvider();
    setProviderBadge(provider);
    pageTitleEl.textContent = activeTab.title || `${provider} Conversation`;
    showState(stateReady);
  } catch (err) {
    showError("Could not inspect active tab: " + err.message);
    showState(stateUnsupported);
  }
});

// 2. Explicit User Action: Capture Conversation via chrome.scripting.executeScript
btnCapture.addEventListener("click", async () => {
  if (!activeTab || !activeTab.id) return;

  showState(stateCapturing);

  try {
    const [injectionResult] = await chrome.scripting.executeScript({
      target: { tabId: activeTab.id },
      files: ["content_script.js"],
    });

    const response = injectionResult?.result;
    if (!response || !response.success || response.status === "CAPTURE_UNAVAILABLE") {
      const errMsg =
        response?.error?.message ||
        "Conversation capture unavailable: Could not locate active conversation messages on this page.";
      showError(errMsg);
      showState(stateReady);
      return;
    }

    currentPayload = response.payload.conversation;
    renderPreview(currentPayload);
    showState(statePreview);
  } catch (err) {
    showError("Failed to execute in-page capture: " + err.message);
    showState(stateReady);
  }
});

// 3. Render Preview In Memory
function renderPreview(payload) {
  const projectId = inputProjectId.value.trim() || null;
  const preview = generatePreview(payload, projectId);

  previewProvider.textContent = preview.provider;
  previewTitle.textContent = preview.title;
  previewMsgCount.textContent = preview.messageCount;

  if (preview.detectedSecretCount > 0) {
    previewSecrets.textContent = `${preview.detectedSecretCount} Secret(s) Redacted`;
    previewSecrets.classList.remove("hidden");
  } else {
    previewSecrets.textContent = "0 Secrets Detected";
    previewSecrets.classList.add("hidden");
  }

  if (preview.firstMessagePreview) {
    roleFirst.textContent = preview.firstMessagePreview.role;
    contentFirst.textContent = preview.firstMessagePreview.snippet;
  }

  if (preview.lastMessagePreview) {
    roleLast.textContent = preview.lastMessagePreview.role;
    contentLast.textContent = preview.lastMessagePreview.snippet;
  }

  btnSend.disabled = !preview.canTransfer;
}

// 4. Explicit User Confirmation: Send to Build Coach
btnSend.addEventListener("click", () => {
  if (!currentPayload) return;

  const projectId = inputProjectId.value.trim() || null;

  chrome.runtime.sendMessage(
    createMessage(MessageType.SEND_TO_BUILD_COACH, {
      conversation: currentPayload,
      projectId,
    }),
    (response) => {
      if (chrome.runtime.lastError || !response || !response.success) {
        showError(response?.error?.message || chrome.runtime.lastError?.message || "Transfer failed.");
        return;
      }

      if (response.payload?.status === "BRIDGE_NOT_CONNECTED") {
        showError(response.payload.message || "Local Build Coach bridge is not connected. Use 'Copy JSON' to transfer conversation.");
        return;
      }

      showState(stateSuccess);
    }
  );
});

// 5. Copy JSON Action
btnCopyJson.addEventListener("click", async () => {
  if (!currentPayload) return;

  const projectId = inputProjectId.value.trim() || null;
  const finalPayload = prepareTransferPayload(currentPayload, projectId);
  const jsonStr = JSON.stringify(finalPayload, null, 2);

  try {
    await navigator.clipboard.writeText(jsonStr);
    const origText = btnCopyJson.textContent;
    btnCopyJson.textContent = "✅ Copied!";
    setTimeout(() => {
      btnCopyJson.textContent = origText;
    }, 2000);
  } catch (err) {
    showError("Clipboard copy failed: " + err.message);
  }
});

// 6. Cancel Action
btnCancel.addEventListener("click", () => {
  currentPayload = null;
  showState(stateReady);
});

// 7. Done Action
btnDone.addEventListener("click", () => {
  window.close();
});
