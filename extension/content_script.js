/**
 * Content Script for AI Build Coach Browser Extension.
 *
 * Runs passively on supported provider tabs.
 * ZERO background monitoring, ZERO automatic scraping.
 * Actions execute ONLY when explicitly requested by user interaction via popup messages.
 */

(async function initContentScript() {
  // Guard against multiple injections
  if (window.__buildCoachContentScriptLoaded) return;
  window.__buildCoachContentScriptLoaded = true;

  let adapterFactory = null;
  let secretScanner = null;
  let messagesModule = null;

  async function loadModules() {
    if (!adapterFactory) {
      const factoryUrl = chrome.runtime.getURL("src/adapters/factory.js");
      const secretsUrl = chrome.runtime.getURL("src/secrets.js");
      const messagesUrl = chrome.runtime.getURL("src/messages.js");

      const [factoryMod, secretsMod, msgMod] = await Promise.all([
        import(factoryUrl),
        import(secretsUrl),
        import(messagesUrl),
      ]);

      adapterFactory = factoryMod.defaultAdapterFactory;
      secretScanner = secretsMod;
      messagesModule = msgMod;
    }
  }

  // Listen for messages from popup or background
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    // Wrap async response
    handleMessage(request)
      .then(sendResponse)
      .catch((err) => {
        sendResponse({
          type: "ERROR",
          success: false,
          error: {
            code: "CONTENT_SCRIPT_ERROR",
            message: err.message || String(err),
          },
        });
      });

    return true; // Keep message channel open for async response
  });

  async function handleMessage(message) {
    if (!message || !message.type) {
      throw new Error("Invalid message envelope received by content script.");
    }

    await loadModules();

    const currentUrl = window.location.href;
    const adapter = adapterFactory.getAdapter(currentUrl, document);

    switch (message.type) {
      case "CHECK_STATUS": {
        const supported = adapter !== null;
        const provider = adapter ? adapter.getProvider() : null;
        const meta = adapter ? adapter.getCaptureMetadata(document) : { pageTitle: document.title, url: currentUrl };

        return {
          type: "STATUS_RESPONSE",
          success: true,
          payload: {
            supported,
            provider,
            url: currentUrl,
            title: meta.pageTitle,
          },
        };
      }

      case "CAPTURE_REQUEST": {
        if (!adapter) {
          throw new Error(`Current page (${currentUrl}) is not a supported AI provider.`);
        }

        // 1. Capture raw conversation via provider adapter
        const rawCaptured = await adapter.captureConversation(document, message.payload?.options || {});

        // 2. Perform client-side secret detection and redaction
        const { sanitizedMessages, totalSecretsRedacted, detectedTypes } =
          secretScanner.redactMessages(rawCaptured.messages || []);

        rawCaptured.messages = sanitizedMessages;
        rawCaptured.rawMetadata = {
          ...(rawCaptured.rawMetadata || {}),
          secrets_detected: totalSecretsRedacted,
          secret_types: detectedTypes,
        };

        // 3. Normalize into common browser payload
        const normalized = adapter.normalizeConversation(rawCaptured);

        return {
          type: "CAPTURE_RESPONSE",
          success: true,
          payload: {
            conversation: normalized,
            detectedSecretsCount: totalSecretsRedacted,
          },
        };
      }

      default:
        throw new Error(`Unknown message type: ${message.type}`);
    }
  }
})();
