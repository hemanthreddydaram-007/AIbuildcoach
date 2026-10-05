/**
 * Gemini Page Adapter for AI Build Coach Browser Extension.
 * Handles gemini.google.com pages.
 */

import { ProviderPageAdapter } from "./base.js";

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

    if (options.syntheticMessages && Array.isArray(options.syntheticMessages)) {
      return {
        title: options.title || meta.pageTitle || "Gemini Conversation",
        messages: options.syntheticMessages,
        rawMetadata: {
          url: meta.url,
          synthetic: true,
          mode: "M12_STUB",
        },
      };
    }

    // Check for synthetic test container in document if available
    if (document) {
      const container = document.querySelector("[data-buildcoach-test-conversation='gemini']");
      if (container) {
        const titleEl = container.querySelector("[data-test-title]");
        const title = titleEl ? titleEl.textContent.trim() : meta.pageTitle;
        const msgEls = container.querySelectorAll("[data-test-message]");
        const messages = [];
        msgEls.forEach((el, index) => {
          messages.push({
            message_id: el.getAttribute("data-message-id") || `gemini_msg_${index + 1}`,
            role: el.getAttribute("data-role") || "USER",
            content: el.textContent || "",
            sequence: index + 1,
          });
        });
        return {
          title,
          messages,
          rawMetadata: {
            url: meta.url,
            fixture: true,
            mode: "M12_STUB",
          },
        };
      }
    }

    return {
      title: meta.pageTitle || "Gemini Conversation",
      messages: [],
      rawMetadata: {
        url: meta.url,
        mode: "M12_STUB",
        note: "M12.0 architecture foundation. Live DOM selector binding is decoupled from extension foundation.",
      },
    };
  }
}
