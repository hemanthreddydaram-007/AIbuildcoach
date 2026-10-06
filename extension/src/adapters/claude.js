/**
 * Real Claude Page Adapter for AI Build Coach Browser Extension.
 *
 * Scoped strictly to claude.ai pages.
 * Uses safe textContent/DOM text extraction without code execution.
 * Returns CAPTURE_UNAVAILABLE if conversation structure cannot be identified.
 */

import { ProviderPageAdapter, CaptureStatus } from "./base.js";

export class ClaudePageAdapter extends ProviderPageAdapter {
  canHandle(url, document = null) {
    if (!url || typeof url !== "string") return false;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return host === "claude.ai" || host.endsWith(".claude.ai");
    } catch {
      return false;
    }
  }

  getProvider() {
    return "CLAUDE";
  }

  async captureConversation(document, options = {}) {
    const meta = this.getCaptureMetadata(document);

    // 1. Synthetic test support
    if (options.syntheticMessages && Array.isArray(options.syntheticMessages)) {
      return {
        status: CaptureStatus.SUCCESS,
        title: options.title || meta.pageTitle || "Claude Conversation",
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
    const headerTitleEl = document.querySelector('[data-testid="chat-title"], button[data-testid="chat-menu-trigger"]');
    if (headerTitleEl && headerTitleEl.textContent) {
      title = headerTitleEl.textContent.trim();
    }
    if (!title && meta.pageTitle) {
      // Strip trailing " \ Claude"
      title = meta.pageTitle.replace(/\s*[-|•]\s*Claude.*$/i, "").trim();
    }
    if (!title || title.toLowerCase() === "claude") {
      title = "Claude Conversation";
    }

    // 3. Extract Message Turns using verified Claude DOM selectors
    const messages = [];

    // Synthetic test fixture container
    const testContainer = document.querySelector("[data-buildcoach-test-conversation='claude']");
    if (testContainer) {
      const titleEl = testContainer.querySelector("[data-test-title]");
      if (titleEl) title = titleEl.textContent.trim();

      const msgEls = testContainer.querySelectorAll("[data-test-message]");
      msgEls.forEach((el, index) => {
        const text = el.textContent ? el.textContent.trim() : "";
        if (text) {
          messages.push({
            message_id: el.getAttribute("data-message-id") || `claude_msg_${index + 1}`,
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

    // Primary Production Selector Strategy for Claude:
    // User messages use data-testid="user-message" or .font-user-message
    // Assistant messages use .font-claude-message or [data-testid*="claude-response"] or .grid-cols-1 message wrappers
    const turnElements = document.querySelectorAll(
      '[data-testid="user-message"], .font-user-message, .font-claude-message, [data-is-streaming], div[class*="font-claude-message"]'
    );

    let sequence = 1;
    turnElements.forEach((el) => {
      // Determine Role
      let role = "ASSISTANT";
      const isUser =
        el.getAttribute("data-testid") === "user-message" ||
        el.classList.contains("font-user-message") ||
        el.closest('[data-testid="user-message"]') !== null;

      if (isUser) {
        role = "USER";
      }

      // Extract text content safely
      const rawText = el.textContent || "";
      const text = rawText.trim();

      if (text && text.length > 0) {
        // Discard UI artifacts like retry/copy button labels
        const isUiNoise = /^(Copy|Retry|Edit|Retry with artifacts)$/i.test(text);
        if (!isUiNoise) {
          // Avoid duplicate messages if parent and child both matched
          const isDuplicate = messages.some((m) => m.content === text && m.role === role);
          if (!isDuplicate) {
            messages.push({
              message_id: el.getAttribute("data-message-id") || `claude_turn_${sequence}`,
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
        reason: "Could not identify active conversation message turns on Claude page.",
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
