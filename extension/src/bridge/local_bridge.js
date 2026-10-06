/**
 * Local Bridge Client for AI Build Coach Browser Extension.
 *
 * Communicates EXCLUSIVELY with fixed local loopback origin: http://127.0.0.1:8765
 * Uses the frozen protocol: buildcoach-bridge-v1
 * Rejects arbitrary URLs, enforces user-initiated optional permissions, and never persists data.
 */

export const DEFAULT_BRIDGE_PORT = 8765;
export const FIXED_BRIDGE_ORIGIN = `http://127.0.0.1:${DEFAULT_BRIDGE_PORT}`;
export const LOCAL_PERMISSION_PATTERN = "http://127.0.0.1/*";
export const BRIDGE_PROTOCOL_V1 = "buildcoach-bridge-v1";

/**
 * Checks whether the optional host permission for 127.0.0.1 is currently granted.
 * @returns {Promise<boolean>}
 */
export async function hasLocalPermission() {
  if (typeof chrome === "undefined" || !chrome.permissions?.contains) {
    return false;
  }
  return new Promise((resolve) => {
    chrome.permissions.contains({ origins: [LOCAL_PERMISSION_PATTERN] }, (result) => {
      resolve(Boolean(result));
    });
  });
}

/**
 * Explicitly prompts the user for optional permission to access http://127.0.0.1/*.
 * Must only be called in direct response to user gesture (e.g. clicking "Send to Build Coach").
 * @returns {Promise<boolean>}
 */
export async function requestLocalPermission() {
  if (typeof chrome === "undefined" || !chrome.permissions?.request) {
    return false;
  }
  return new Promise((resolve) => {
    chrome.permissions.request({ origins: [LOCAL_PERMISSION_PATTERN] }, (granted) => {
      resolve(Boolean(granted));
    });
  });
}

/**
 * Checks if the local Python Build Coach bridge server is active and healthy.
 * @param {string} [origin=FIXED_BRIDGE_ORIGIN]
 * @returns {Promise<{connected: boolean, protocol?: string, service?: string, error?: string}>}
 */
export async function checkConnection(origin = FIXED_BRIDGE_ORIGIN) {
  // Security guard: Ensure origin is strictly 127.0.0.1
  const parsed = new URL(origin);
  if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "localhost") {
    throw new Error(`Security violation: Bridge client will not connect to non-local origin '${origin}'.`);
  }

  const endpoint = `${origin}/health`;
  try {
    const res = await fetch(endpoint, {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    });

    if (!res.ok) {
      return { connected: false, error: `Bridge returned HTTP ${res.status}` };
    }

    const data = await res.json();
    if (data.ok && data.protocol === BRIDGE_PROTOCOL_V1 && data.result?.bridge) {
      return {
        connected: true,
        protocol: data.protocol,
        service: data.result.service,
      };
    }
    return { connected: false, error: "Invalid bridge protocol response." };
  } catch (err) {
    return { connected: false, error: err.message || "Connection refused" };
  }
}

/**
 * Transmits verified capture payload to local Python bridge.
 * @param {object} conversationPayload
 * @param {string} [origin=FIXED_BRIDGE_ORIGIN]
 * @returns {Promise<{ok: boolean, result?: object, error?: {code: string, message: string}}>}
 */
export async function sendCapture(conversationPayload, origin = FIXED_BRIDGE_ORIGIN) {
  // Security guard: Ensure origin is strictly 127.0.0.1
  const parsed = new URL(origin);
  if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "localhost") {
    throw new Error(`Security violation: Bridge client will not connect to non-local origin '${origin}'.`);
  }

  if (!conversationPayload || !conversationPayload.provider) {
    return {
      ok: false,
      error: {
        code: "INVALID_PAYLOAD",
        message: "Missing conversation payload or provider.",
      },
    };
  }

  const requestId = `req_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
  const envelope = {
    protocol: BRIDGE_PROTOCOL_V1,
    request_id: requestId,
    message_type: "capture",
    timestamp: new Date().toISOString(),
    payload: conversationPayload,
  };

  const endpoint = `${origin}/v1/capture`;
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(envelope),
    });

    const data = await res.json();
    if (!res.ok || !data.ok) {
      return {
        ok: false,
        error: data.error || {
          code: "HTTP_ERROR",
          message: `Bridge returned status ${res.status}`,
        },
      };
    }

    return {
      ok: true,
      result: data.result,
    };
  } catch (err) {
    return {
      ok: false,
      error: {
        code: "BRIDGE_UNAVAILABLE",
        message: `Could not reach local Build Coach bridge: ${err.message}`,
      },
    };
  }
}
