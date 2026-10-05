/**
 * Standard message types for the AI Build Coach Browser Extension.
 */

export const MessageType = {
  // Popup asks content script about current page support
  CHECK_STATUS: "CHECK_STATUS",
  STATUS_RESPONSE: "STATUS_RESPONSE",

  // Popup requests explicit user capture
  CAPTURE_REQUEST: "CAPTURE_REQUEST",
  CAPTURE_RESPONSE: "CAPTURE_RESPONSE",

  // Popup requests sending conversation to Build Coach bridge
  SEND_TO_BUILD_COACH: "SEND_TO_BUILD_COACH",
  SEND_RESPONSE: "SEND_RESPONSE",

  // Error envelope
  ERROR: "ERROR",
};

/**
 * Creates a standard success message envelope.
 * @param {string} type
 * @param {any} payload
 * @returns {object}
 */
export function createMessage(type, payload = {}) {
  return {
    type,
    success: true,
    timestamp: new Date().toISOString(),
    payload,
  };
}

/**
 * Creates a standard error message envelope.
 * @param {string} type
 * @param {string} errorCode
 * @param {string} errorMessage
 * @returns {object}
 */
export function createErrorMessage(type, errorCode, errorMessage) {
  return {
    type,
    success: false,
    timestamp: new Date().toISOString(),
    error: {
      code: errorCode,
      message: errorMessage,
    },
  };
}
