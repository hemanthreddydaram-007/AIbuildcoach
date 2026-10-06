/**
 * Background Service Worker for AI Build Coach Browser Extension (Manifest V3).
 *
 * ARCHITECTURAL PRINCIPLE:
 * Strictly event-driven.
 * NO periodic timers, NO background alarms, NO automatic tab scanning, NO external telemetry.
 * NO persistent browser storage: in-memory / ephemeral only.
 */

import { MessageType, createMessage, createErrorMessage } from "./src/messages.js";
import { prepareTransferPayload } from "./src/bridge.js";

if (typeof chrome !== "undefined" && chrome.runtime?.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => {
    console.log("[AI Build Coach] Extension installed successfully.");
  });
}

// Message hub
if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
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
}

/**
 * Handles explicit user request to send conversation to Build Coach.
 * Prepares verified payload in-memory without persistent browser storage.
 * @param {object} payload
 * @returns {Promise<object>}
 */
export async function handleSendToBuildCoach(payload) {
  if (!payload || !payload.conversation) {
    throw new Error("Missing conversation payload.");
  }

  const projectId = payload.projectId || null;
  const verifiedPayload = prepareTransferPayload(payload.conversation, projectId);

  // In-memory handling:
  // Persistent browser storage is strictly avoided per M12.1 permission minimization.
  // Because the local native messaging host / daemon bridge is not yet connected in this phase,
  // return a structured BRIDGE_NOT_CONNECTED status rather than pretending the payload transferred.
  return {
    status: "BRIDGE_NOT_CONNECTED",
    message: "Local Build Coach bridge is not connected. Use 'Copy JSON' to transfer conversation into Build Coach CLI.",
    conversation_id: verifiedPayload.conversation_id,
    provider: verifiedPayload.provider,
    message_count: verifiedPayload.messages.length,
    payload: verifiedPayload,
  };
}
