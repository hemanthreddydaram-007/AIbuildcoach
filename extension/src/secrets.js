/**
 * Client-side secret scanner and redaction engine for AI Build Coach extension.
 * Performs deterministic in-memory redaction before conversation payloads leave the page.
 */

const SECRET_PATTERNS = [
  {
    type: "ANTHROPIC_API_KEY",
    regex: /\bsk-ant-[A-Za-z0-9_-]{20,}\b/g,
  },
  {
    type: "OPENAI_API_KEY",
    regex: /\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b/g,
  },
  {
    type: "GITHUB_TOKEN",
    regex: /\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,})\b/g,
  },
  {
    type: "AWS_ACCESS_KEY",
    regex: /\bAKIA[0-9A-Z]{16}\b/g,
  },
  {
    type: "PRIVATE_KEY",
    regex: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  },
  {
    type: "BEARER_TOKEN",
    regex: /\bBearer\s+[A-Za-z0-9_\-\.]{24,}\b/gi,
  },
];

/**
 * Redacts secrets from a given text string.
 * @param {string} text
 * @returns {{ sanitizedText: string, detectedCount: number, detectedTypes: string[] }}
 */
export function scanAndRedact(text) {
  if (!text || typeof text !== "string") {
    return {
      sanitizedText: text || "",
      detectedCount: 0,
      detectedTypes: [],
    };
  }

  let sanitized = text;
  let detectedCount = 0;
  const detectedTypes = new Set();

  for (const { type, regex } of SECRET_PATTERNS) {
    const matches = sanitized.match(regex);
    if (matches && matches.length > 0) {
      detectedCount += matches.length;
      detectedTypes.add(type);
      sanitized = sanitized.replace(regex, `[REDACTED:${type}]`);
    }
  }

  return {
    sanitizedText: sanitized,
    detectedCount,
    detectedTypes: Array.from(detectedTypes),
  };
}

/**
 * Redacts secrets from an array of message objects in-place or returning a new array.
 * @param {Array<{ message_id: string, role: string, content: string, sequence: number, metadata?: object }>} messages
 * @returns {{ sanitizedMessages: Array<object>, totalSecretsRedacted: number, detectedTypes: string[] }}
 */
export function redactMessages(messages) {
  if (!Array.isArray(messages)) {
    return { sanitizedMessages: [], totalSecretsRedacted: 0, detectedTypes: [] };
  }

  let totalSecrets = 0;
  const allTypes = new Set();

  const sanitizedMessages = messages.map((msg, index) => {
    const { sanitizedText, detectedCount, detectedTypes } = scanAndRedact(msg.content);
    totalSecrets += detectedCount;
    detectedTypes.forEach((t) => allTypes.add(t));

    const meta = { ...(msg.metadata || {}) };
    if (detectedCount > 0) {
      meta.redacted = true;
      meta.secrets_detected = detectedCount;
      meta.secret_types = detectedTypes;
    }

    return {
      message_id: msg.message_id || `msg_${index + 1}`,
      role: msg.role || "USER",
      content: sanitizedText,
      timestamp: msg.timestamp || null,
      sequence: typeof msg.sequence === "number" ? msg.sequence : index + 1,
      metadata: meta,
    };
  });

  return {
    sanitizedMessages,
    totalSecretsRedacted: totalSecrets,
    detectedTypes: Array.from(allTypes),
  };
}
