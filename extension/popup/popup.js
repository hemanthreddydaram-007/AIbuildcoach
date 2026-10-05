/**
 * Popup UI Logic for AI Build Coach Browser Extension.
 *
 * Enforces strictly user-controlled capture and preview-before-transfer invariant:
 * - Nothing is extracted until user clicks "Capture Conversation".
 * - Nothing is transferred until user clicks "Send to Build Coach".
 */

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

// In-memory state (NEVER transmitted without explicit user action)
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

// 1. Initial status check on popup open
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) {
      showState(stateUnsupported);
      return;
    }

    activeTab = tabs[0];
    const url = activeTab.url || "";

    // Send status check to active tab's content script
    chrome.tabs.sendMessage(
      activeTab.id,
      createMessage(MessageType.CHECK_STATUS),
      (response) => {
        if (chrome.runtime.lastError || !response || !response.payload?.supported) {
          setProviderBadge(null);
          showState(stateUnsupported);
          return;
        }

        const { provider, title } = response.payload;
        setProviderBadge(provider);
        pageTitleEl.textContent = title || activeTab.title || "Provider Conversation";
        showState(stateReady);
      }
    );
  } catch (err) {
    showError("Could not inspect active tab: " + err.message);
    showState(stateUnsupported);
  }
});

// 2. Explicit User Action: Capture Conversation
btnCapture.addEventListener("click", async () => {
  if (!activeTab) return;

  showState(stateCapturing);

  chrome.tabs.sendMessage(
    activeTab.id,
    createMessage(MessageType.CAPTURE_REQUEST),
    (response) => {
      if (chrome.runtime.lastError || !response || !response.success) {
        showError(response?.error?.message || chrome.runtime.lastError?.message || "Capture failed.");
        showState(stateReady);
        return;
      }

      currentPayload = response.payload.conversation;
      renderPreview(currentPayload);
      showState(statePreview);
    }
  );
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
