/**
 * Base ProviderPageAdapter contract for AI Build Coach Browser Extension.
 *
 * ARCHITECTURAL PRINCIPLE:
 * DOM extraction and provider-specific details are strictly encapsulated in adapters.
 * Never scatter provider selectors or scrape logic outside adapter modules.
 */

export const CaptureStatus = {
  SUCCESS: "SUCCESS",
  CAPTURE_UNAVAILABLE: "CAPTURE_UNAVAILABLE",
};

export class ProviderPageAdapter {
  /**
   * Determines whether this adapter can handle the current web page.
   * @param {string} url
   * @param {Document|null} document
   * @returns {boolean}
   */
  canHandle(url, document = null) {
    throw new Error("canHandle() must be implemented by subclass.");
  }

  /**
   * Returns the normalized provider identifier (e.g. "CHATGPT", "CLAUDE", "GEMINI").
   * @returns {string}
   */
  getProvider() {
    throw new Error("getProvider() must be implemented by subclass.");
  }

  /**
   * Captures raw conversation elements from the document.
   *
   * If conversation cannot be confidently identified, must return:
   * { status: CaptureStatus.CAPTURE_UNAVAILABLE, error: "CAPTURE_UNAVAILABLE", reason: "..." }
   *
   * @param {Document} document
   * @param {object} options
   * @returns {Promise<{ status: string, title: string|null, messages: Array<object>, rawMetadata?: object, error?: string, reason?: string }>}
   */
  async captureConversation(document, options = {}) {
    throw new Error("captureConversation() must be implemented by subclass.");
  }

  /**
   * Normalizes raw captured conversation data into the common browser payload.
   * @param {object} rawCaptured
   * @returns {object}
   */
  normalizeConversation(rawCaptured) {
    if (!rawCaptured || typeof rawCaptured !== "object") {
      throw new Error("Cannot normalize invalid or empty captured conversation payload.");
    }

    if (rawCaptured.status === CaptureStatus.CAPTURE_UNAVAILABLE || rawCaptured.error === "CAPTURE_UNAVAILABLE") {
      return {
        status: CaptureStatus.CAPTURE_UNAVAILABLE,
        error: "CAPTURE_UNAVAILABLE",
        reason: rawCaptured.reason || "Conversation structure could not be identified on page.",
        provider: this.getProvider(),
        messages: [],
      };
    }

    const rawMessages = Array.isArray(rawCaptured.messages) ? rawCaptured.messages : [];
    const normalizedMessages = rawMessages.map((m, index) => {
      let role = String(m.role || "").toUpperCase();
      if (role !== "USER" && role !== "ASSISTANT" && role !== "SYSTEM") {
        role = "USER";
      }

      return {
        message_id: m.message_id || `ext_msg_${index + 1}`,
        role,
        content: typeof m.content === "string" ? m.content : "",
        timestamp: m.timestamp || null,
        sequence: typeof m.sequence === "number" ? m.sequence : index + 1,
        metadata: typeof m.metadata === "object" && m.metadata !== null ? { ...m.metadata } : {},
      };
    });

    return {
      status: CaptureStatus.SUCCESS,
      provider: this.getProvider(),
      source: "WEB_EXTENSION",
      title: rawCaptured.title || null,
      captured_at: new Date().toISOString(),
      messages: normalizedMessages,
      metadata: {
        ...(rawCaptured.rawMetadata || {}),
        adapter: this.constructor.name,
      },
      project_id: rawCaptured.project_id || null,
    };
  }

  /**
   * Extracts safe, non-sensitive page metadata (e.g. document title, URL).
   * @param {Document} document
   * @returns {{ pageTitle: string|null, url: string, captureTime: string }}
   */
  getCaptureMetadata(document) {
    let pageTitle = null;
    let url = "";

    if (document) {
      pageTitle = document.title ? document.title.trim() : null;
      if (document.location && document.location.href) {
        url = document.location.href;
      }
    }

    return {
      pageTitle,
      url,
      captureTime: new Date().toISOString(),
    };
  }
}
