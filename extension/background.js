/**
 * Background Service Worker for AI Build Coach Browser Extension (Manifest V3).
 *
 * ARCHITECTURAL PRINCIPLE:
 * Strictly event-driven.
 * NO periodic timers, NO background alarms, NO automatic tab scanning, NO external telemetry.
 */

import { MessageType, createMessage, createErrorMessage } from "./src/messages.js";
import { prepareTransferPayload } from "./src/bridge.js";

chrome.runtime.onInstalled.addListener(() => {
  console.log("[AI Build Coach] Extension installed successfully.");
});

// Message hub
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === MessageType.SEND_TO_BUILD_COACH) {
    handleSendToBuildCoach(message.payload)
      .then((res) => sendResponse(createMessage(MessageType.SEND_RESPONSE, res)))
      .catch((err) =>
        sendResponse(createErrorMessage(MessageType.ERROR, "SEND_FAILED", err.message || String(err)))
      );
    return true; // Keep async channel open
  }
});

/**
 * Handles explicit user request to send conversation to Build Coach.
 * Prepares verified payload and persists/transfers it safely.
 * @param {object} payload
 * @returns {Promise<object>}
 */
async function handleSendToBuildCoach(payload) {
  if (!payload || !payload.conversation) {
    throw new Error("Missing conversation payload.");
  }

  const projectId = payload.projectId || null;
  const verifiedPayload = prepareTransferPayload(payload.conversation, projectId);

  // Store in extension local memory so it is safely accessible
  await chrome.storage.local.set({
    last_captured_conversation: verifiedPayload,
    last_captured_at: new Date().toISOString(),
  });

  return {
    status: "ready_for_transfer",
    conversation_id: verifiedPayload.conversation_id,
    provider: verifiedPayload.provider,
    message_count: verifiedPayload.messages.length,
    payload: verifiedPayload,
  };
}
