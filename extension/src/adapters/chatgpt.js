/**
 * Real ChatGPT Page Adapter for AI Build Coach Browser Extension.
 *
 * Scoped strictly to chatgpt.com and chat.openai.com pages.
 * Uses safe textContent/DOM text extraction without code execution.
 * Returns CAPTURE_UNAVAILABLE if conversation structure cannot be identified.
 */

import { ProviderPageAdapter, CaptureStatus } from "./base.js";

export class ChatGPTPageAdapter extends ProviderPageAdapter {
  canHandle(url, document = null) {
    if (!url || typeof url !== "string") return false;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return (
        host === "chatgpt.com" ||
        host.endsWith(".chatgpt.com") ||
        host === "chat.openai.com" ||
        host.endsWith(".chat.openai.com")
      );
    } catch {
      return false;
    }
  }

  getProvider() {
    return "CHATGPT";
  }

  async captureConversation(document, options = {}) {
    const meta = this.getCaptureMetadata(document);

    // 1. Synthetic test support
    if (options.syntheticMessages && Array.isArray(options.syntheticMessages)) {
      return {
        status: CaptureStatus.SUCCESS,
        title: options.title || meta.pageTitle || "ChatGPT Conversation",
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
    const activeSidebarItem = document.querySelector("nav li[data-active='true'], nav a[class*='active']");
    if (activeSidebarItem && activeSidebarItem.textContent) {
      title = activeSidebarItem.textContent.trim();
    }
    if (!title && meta.pageTitle) {
      // Strip trailing " - ChatGPT" or " | ChatGPT"
      title = meta.pageTitle.replace(/\s*[-|•]\s*ChatGPT.*$/i, "").trim();
    }
    if (!title || title.toLowerCase() === "chatgpt") {
      title = "ChatGPT Conversation";
    }

    // 3. Extract Message Turns using verified ChatGPT DOM selectors
    // Priority 1: Articles with data-message-author-role (Standard ChatGPT structure)
    // Priority 2: Turns with data-testid="conversation-turn-*"
    // Priority 3: Synthetic test container
    const messages = [];

    const testContainer = document.querySelector("[data-buildcoach-test-conversation='chatgpt']");
    if (testContainer) {
      const titleEl = testContainer.querySelector("[data-test-title]");
      if (titleEl) title = titleEl.textContent.trim();

      const msgEls = testContainer.querySelectorAll("[data-test-message]");
      msgEls.forEach((el, index) => {
        const text = el.textContent ? el.textContent.trim() : "";
        if (text) {
          messages.push({
            message_id: el.getAttribute("data-message-id") || `chatgpt_msg_${index + 1}`,
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

    // Primary Production Selector Strategy:
    // ChatGPT renders each turn as an <article> or div with data-message-author-role
    let turnElements = document.querySelectorAll("article [data-message-author-role]");
    if (turnElements.length === 0) {
      turnElements = document.querySelectorAll("[data-message-author-role]");
    }
    if (turnElements.length === 0) {
      turnElements = document.querySelectorAll('div[data-testid^="conversation-turn-"]');
    }

    let sequence = 1;
    turnElements.forEach((turnEl) => {
      // Determine Role
      let role = "USER";
      const authorRole = turnEl.getAttribute("data-message-author-role");
      if (authorRole) {
        role = authorRole.toUpperCase() === "ASSISTANT" ? "ASSISTANT" : "USER";
      } else {
        // Fallback checks inside turn container
        if (
          turnEl.querySelector('[data-message-author-role="assistant"]') ||
          turnEl.querySelector('svg[aria-label="ChatGPT"]') ||
          turnEl.getAttribute("data-testid")?.includes("assistant")
        ) {
          role = "ASSISTANT";
        } else if (
          turnEl.querySelector('[data-message-author-role="user"]') ||
          turnEl.querySelector('svg[aria-label="User"]') ||
          turnEl.getAttribute("data-testid")?.includes("user")
        ) {
          role = "USER";
        }
      }

      // Content text extraction
      // Look for main message container inside the turn
      const contentEl =
        turnEl.querySelector(".whitespace-pre-wrap") ||
        turnEl.querySelector(".markdown") ||
        turnEl.querySelector('[data-message-id]') ||
        turnEl;

      // Extract textContent safely
      const rawText = contentEl.textContent || "";
      const text = rawText.trim();

      // Filter out non-message noise (e.g. action buttons or regenerate bar)
      if (text && text.length > 0) {
        // Discard UI action text if solely containing "Copy", "Edit", etc.
        const isUiNoise = /^(Copy|Edit|Read aloud|Regenerate|Bad response|Good response)$/i.test(text);
        if (!isUiNoise) {
          messages.push({
            message_id: turnEl.getAttribute("data-message-id") || `chatgpt_turn_${sequence}`,
            role,
            content: text,
            sequence,
          });
          sequence++;
        }
      }
    });

    // 4. Provider Failure Safety
    if (messages.length === 0) {
      return {
        status: CaptureStatus.CAPTURE_UNAVAILABLE,
        error: "CAPTURE_UNAVAILABLE",
        reason: "Could not identify active conversation message turns on ChatGPT page.",
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
