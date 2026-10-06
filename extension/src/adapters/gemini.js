/**
 * Real Gemini Page Adapter for AI Build Coach Browser Extension.
 *
 * Scoped strictly to gemini.google.com pages.
 * Uses safe textContent/DOM text extraction without code execution.
 * Returns CAPTURE_UNAVAILABLE if conversation structure cannot be identified.
 */

import { ProviderPageAdapter, CaptureStatus } from "./base.js";

export class GeminiPageAdapter extends ProviderPageAdapter {
  canHandle(url, document = null) {
    if (!url || typeof url !== "string") return false;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return host === "gemini.google.com" || host.endsWith(".gemini.google.com");
    } catch {
      return false;
    }
  }

  getProvider() {
    return "GEMINI";
  }

  async captureConversation(document, options = {}) {
    const meta = this.getCaptureMetadata(document);

    // 1. Synthetic test support
    if (options.syntheticMessages && Array.isArray(options.syntheticMessages)) {
      return {
        status: CaptureStatus.SUCCESS,
        title: options.title || meta.pageTitle || "Gemini Conversation",
        messages: options.syntheticMessages,
        rawMetadata: {
          url: meta.url,
          synthetic: true,
        },
      };
    }

    if (!document) {
      return {
        status: CaptureStatus.CAPTURE_UNAVAILABLE,
        error: "CAPTURE_UNAVAILABLE",
        reason: "Document object is not available.",
        title: null,
        messages: [],
      };
    }

    // 2. Extract Title safely
    let title = null;
    const conversationTitleEl = document.querySelector("h1.conversation-title, [data-test-id='conversation-title'], div.conversation-title");
    if (conversationTitleEl && conversationTitleEl.textContent) {
      title = conversationTitleEl.textContent.trim();
    }
    if (!title && meta.pageTitle) {
      // Strip "Gemini - " or " - Gemini"
      title = meta.pageTitle.replace(/^Gemini\s*[-|•]\s*/i, "").replace(/\s*[-|•]\s*Gemini.*$/i, "").trim();
    }
    if (!title || title.toLowerCase() === "gemini") {
      title = "Gemini Conversation";
    }

    // 3. Extract Message Turns using verified Gemini DOM selectors
    const messages = [];

    // Synthetic test fixture container
    const testContainer = document.querySelector("[data-buildcoach-test-conversation='gemini']");
    if (testContainer) {
      const titleEl = testContainer.querySelector("[data-test-title]");
      if (titleEl) title = titleEl.textContent.trim();

      const msgEls = testContainer.querySelectorAll("[data-test-message]");
      msgEls.forEach((el, index) => {
        const text = el.textContent ? el.textContent.trim() : "";
        if (text) {
          messages.push({
            message_id: el.getAttribute("data-message-id") || `gemini_msg_${index + 1}`,
            role: (el.getAttribute("data-role") || "USER").toUpperCase(),
            content: text,
            sequence: index + 1,
          });
        }
      });

      if (messages.length > 0) {
        return {
          status: CaptureStatus.SUCCESS,
          title,
          messages,
          rawMetadata: { url: meta.url, source_type: "synthetic_fixture" },
        };
      }
    }

    // Primary Production Selector Strategy for Gemini:
    // Gemini renders custom web components: <user-query> and <model-response>
    // Or containers with .user-query-container and .response-container
    const turnElements = document.querySelectorAll(
      "user-query, model-response, .user-query-container, .response-container, div.chat-turn"
    );

    let sequence = 1;
    turnElements.forEach((el) => {
      const tagName = el.tagName.toUpperCase();

      // If it's a full chat-turn containing both, process children
      if (el.classList.contains("chat-turn")) {
        const uq = el.querySelector("user-query, .user-query-container, .query-text");
        if (uq) {
          const t = uq.textContent ? uq.textContent.trim() : "";
          if (t) {
            messages.push({
              message_id: uq.getAttribute("data-message-id") || `gemini_turn_${sequence}`,
              role: "USER",
              content: t,
              sequence: sequence++,
            });
          }
        }
        const mr = el.querySelector("model-response, .response-container, .model-response-text");
        if (mr) {
          const t = mr.textContent ? mr.textContent.trim() : "";
          if (t) {
            messages.push({
              message_id: mr.getAttribute("data-message-id") || `gemini_turn_${sequence}`,
              role: "ASSISTANT",
              content: t,
              sequence: sequence++,
            });
          }
        }
        return;
      }

      // Individual tag handling
      let role = "USER";
      if (tagName === "MODEL-RESPONSE" || el.classList.contains("response-container")) {
        role = "ASSISTANT";
      }

      // Target text content inside custom component
      const contentEl =
        el.querySelector(".query-text") ||
        el.querySelector(".message-content") ||
        el.querySelector(".model-response-text") ||
        el;

      const rawText = contentEl.textContent || "";
      const text = rawText.trim();

      if (text && text.length > 0) {
        const isUiNoise = /^(Copy|Retry|Share|Modify response)$/i.test(text);
        if (!isUiNoise) {
          const isDuplicate = messages.some((m) => m.content === text && m.role === role);
          if (!isDuplicate) {
            messages.push({
              message_id: el.getAttribute("data-message-id") || `gemini_turn_${sequence}`,
              role,
              content: text,
              sequence,
            });
            sequence++;
          }
        }
      }
    });

    // 4. Provider Failure Safety
    if (messages.length === 0) {
      return {
        status: CaptureStatus.CAPTURE_UNAVAILABLE,
        error: "CAPTURE_UNAVAILABLE",
        reason: "Could not identify active conversation message turns on Gemini page.",
        title: null,
        messages: [],
      };
    }

    return {
      status: CaptureStatus.SUCCESS,
      title,
      messages,
      rawMetadata: {
        url: meta.url,
        turnCount: messages.length,
      },
    };
  }
}
