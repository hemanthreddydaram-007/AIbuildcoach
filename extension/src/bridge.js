/**
 * Build Coach Bridge module.
 * Bridges captured provider data to the standard Build Coach schema with
 * preview generation and explicit user-controlled transmission.
 */

import { redactMessages } from "./secrets.js";

/**
 * Builds a preview representation of a captured conversation without transmitting anything.
 * @param {object} payload Common normalized conversation payload
 * @param {string|null} projectId Optional selected project ID
 * @returns {object} Preview metadata for UI display
 */
export function generatePreview(payload, projectId = null) {
  if (!payload || !Array.isArray(payload.messages)) {
    return {
      provider: payload?.provider || "UNKNOWN",
      title: payload?.title || "Empty Conversation",
      messageCount: 0,
      firstMessagePreview: null,
      lastMessagePreview: null,
      detectedSecretCount: 0,
      projectId: projectId || null,
      canTransfer: false,
    };
  }

  const messages = payload.messages;
  const count = messages.length;

  const truncate = (str, len = 120) => {
    if (!str) return "";
    return str.length > len ? str.slice(0, len) + "…" : str;
  };

  const firstMsg = count > 0 ? {
    role: messages[0].role,
    snippet: truncate(messages[0].content),
  } : null;

  const lastMsg = count > 0 ? {
    role: messages[count - 1].role,
    snippet: truncate(messages[count - 1].content),
  } : null;

  // Calculate secrets detected across all messages
  let secretsCount = 0;
  for (const m of messages) {
    if (m.metadata && typeof m.metadata.secrets_detected === "number") {
      secretsCount += m.metadata.secrets_detected;
    }
  }

  return {
    provider: payload.provider,
    title: payload.title || "Untitled Conversation",
    messageCount: count,
    firstMessagePreview: firstMsg,
    lastMessagePreview: lastMsg,
    detectedSecretCount: secretsCount,
    projectId: projectId || null,
    canTransfer: count > 0,
  };
}

/**
 * Prepares the final verified conversation payload to send to Build Coach.
 * Redacts secrets client-side and validates schema requirements.
 *
 * @param {object} rawPayload
 * @param {string|null} projectId
 * @returns {object} Standard M11.0 compatible Conversation payload
 */
export function prepareTransferPayload(rawPayload, projectId = null) {
  if (!rawPayload || !rawPayload.provider) {
    throw new Error("Invalid payload: missing provider.");
  }

  const { sanitizedMessages, totalSecretsRedacted, detectedTypes } = redactMessages(rawPayload.messages || []);

  const conversationId = `conv_ext_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`;
  const now = new Date().toISOString();

  return {
    conversation_id: conversationId,
    provider: rawPayload.provider,
    source: "WEB_EXTENSION",
    title: rawPayload.title || "Imported Browser Conversation",
    project_id: projectId || null,
    created_at: now,
    updated_at: now,
    messages: sanitizedMessages,
    metadata: {
      ...(rawPayload.metadata || {}),
      source: "WEB_EXTENSION",
      extension_version: "1.0.0",
      total_secrets_redacted: totalSecretsRedacted,
      redacted_secret_types: detectedTypes,
      exported_at: now,
    },
  };
}
