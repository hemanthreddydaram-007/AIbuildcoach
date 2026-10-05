/**
 * Provider Adapter Factory for AI Build Coach Browser Extension.
 * Resolves the appropriate ProviderPageAdapter for a given URL and document.
 */

import { ChatGPTPageAdapter } from "./chatgpt.js";
import { ClaudePageAdapter } from "./claude.js";
import { GeminiPageAdapter } from "./gemini.js";

export class ProviderAdapterFactory {
  constructor(customAdapters = null) {
    this.adapters = customAdapters || [
      new ChatGPTPageAdapter(),
      new ClaudePageAdapter(),
      new GeminiPageAdapter(),
    ];
  }

  /**
   * Returns the matching adapter for the URL, or null if unsupported.
   * @param {string} url
   * @param {Document|null} document
   * @returns {ProviderPageAdapter|null}
   */
  getAdapter(url, document = null) {
    if (!url || typeof url !== "string") return null;
    for (const adapter of this.adapters) {
      if (adapter.canHandle(url, document)) {
        return adapter;
      }
    }
    return null;
  }

  /**
   * Returns all supported provider names.
   * @returns {string[]}
   */
  getSupportedProviders() {
    return this.adapters.map((a) => a.getProvider());
  }

  /**
   * Checks whether a URL is supported by any registered provider adapter.
   * @param {string} url
   * @param {Document|null} document
   * @returns {boolean}
   */
  isSupported(url, document = null) {
    return this.getAdapter(url, document) !== null;
  }
}

// Default singleton instance
export const defaultAdapterFactory = new ProviderAdapterFactory();
