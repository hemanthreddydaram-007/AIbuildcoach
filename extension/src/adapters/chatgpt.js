/**
 * ChatGPT Page Adapter for AI Build Coach Browser Extension.
 * Handles chatgpt.com and chat.openai.com pages.
 */

import { ProviderPageAdapter } from "./base.js";

export class ChatGPTPageAdapter extends ProviderPageAdapter {
  canHandle(url, document = null) {
    if (!url || typeof url !== "string") return false;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return host === "chatgpt.com" || host.endsWith(".chatgpt.com") ||
             host === "chat.openai.com" || host.endsWith(".chat.openai.com");
    } catch {
      return false;
    }
  }

  getProvider() {
    return "CHATGPT";
  }

  async captureConversation(document, options = {}) {
    // M12.0 Interface/stub: If synthetic test elements or options are provided, extract them.
    // Do not guess or invent unverified production DOM selectors.
    const meta = this.getCaptureMetadata(document);

    if (options.syntheticMessages && Array.isArray(options.syntheticMessages)) {
      return {
        title: options.title || meta.pageTitle || "ChatGPT Conversation",
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
      const container = document.querySelector("[data-buildcoach-test-conversation='chatgpt']");
      if (container) {
        const titleEl = container.querySelector("[data-test-title]");
        const title = titleEl ? titleEl.textContent.trim() : meta.pageTitle;
        const msgEls = container.querySelectorAll("[data-test-message]");
        const messages = [];
        msgEls.forEach((el, index) => {
          messages.push({
            message_id: el.getAttribute("data-message-id") || `chatgpt_msg_${index + 1}`,
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

    // Baseline stub for real page prior to live DOM selector validation
    return {
      title: meta.pageTitle || "ChatGPT Conversation",
      messages: [],
      rawMetadata: {
        url: meta.url,
        mode: "M12_STUB",
        note: "M12.0 architecture foundation. Live DOM selector binding is decoupled from extension foundation.",
      },
    };
  }
}
