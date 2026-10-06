"""Strict validation and security guardrails for the local bridge protocol."""

import json
import re
from typing import Dict, Any, Tuple
from pydantic import ValidationError

from backend.bridge.models import (
    BRIDGE_PROTOCOL_V1,
    MAX_PAYLOAD_BYTES,
    BridgeRequest,
    CapturePayload,
)
from backend.context_engine.secrets import detect_and_redact


class BridgeValidationError(Exception):
    """Structured validation error mapping to protocol error envelopes."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# Allowed providers
SUPPORTED_PROVIDERS = {"CHATGPT", "CLAUDE", "GEMINI", "OTHER"}

# Path traversal and injection patterns
RE_UNSAFE_IDENTIFIER = re.compile(r"[/\\:;|<>&$\`\*\?]")
RE_REQUEST_ID = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def validate_raw_body(body_bytes: bytes) -> Dict[str, Any]:
    """Validates raw request body size and JSON format."""
    if len(body_bytes) > MAX_PAYLOAD_BYTES:
        raise BridgeValidationError(
            code="OVERSIZED_PAYLOAD",
            message=f"Payload size ({len(body_bytes)} bytes) exceeds maximum limit ({MAX_PAYLOAD_BYTES} bytes).",
            status_code=413,
        )

    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BridgeValidationError(
            code="MALFORMED_JSON",
            message="Request body must be valid UTF-8 JSON.",
            status_code=400,
        )

    if not isinstance(data, dict):
        raise BridgeValidationError(
            code="MALFORMED_JSON",
            message="Request envelope must be a JSON object.",
            status_code=400,
        )

    return data


def validate_request_envelope(data: Dict[str, Any]) -> BridgeRequest:
    """Validates protocol envelope fields, message types, and request ID."""
    allowed_keys = {"protocol", "request_id", "message_type", "timestamp", "payload"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        raise BridgeValidationError(
            code="UNEXPECTED_FIELDS",
            message=f"Envelope contains unrecognized fields: {sorted(list(extra_keys))}.",
            status_code=400,
        )

    protocol = data.get("protocol")
    if protocol != BRIDGE_PROTOCOL_V1:
        raise BridgeValidationError(
            code="UNSUPPORTED_PROTOCOL",
            message=f"Unsupported protocol '{protocol}'. Supported: '{BRIDGE_PROTOCOL_V1}'.",
            status_code=400,
        )

    request_id = data.get("request_id")
    if not request_id or not isinstance(request_id, str) or not RE_REQUEST_ID.match(request_id):
        raise BridgeValidationError(
            code="INVALID_REQUEST_ID",
            message="request_id must be a non-empty alphanumeric string (max 128 chars).",
            status_code=400,
        )

    message_type = data.get("message_type")
    if message_type not in {"health", "capture", "bind"}:
        raise BridgeValidationError(
            code="UNKNOWN_MESSAGE_TYPE",
            message=f"Unknown message_type '{message_type}'. Supported: 'health', 'capture', 'bind'.",
            status_code=400,
        )

    try:
        return BridgeRequest(**data)
    except ValidationError:
        raise BridgeValidationError(
            code="INVALID_ENVELOPE",
            message="Request envelope failed schema validation.",
            status_code=400,
        )


def validate_project_id(project_id: Any) -> str:
    """Validates that project_id is safe, non-empty, and free of path traversal / shell characters."""
    if not project_id or not isinstance(project_id, str):
        raise BridgeValidationError(
            code="INVALID_PROJECT_ID",
            message="project_id must be a non-empty string.",
            status_code=400,
        )
    if len(project_id) > 128:
        raise BridgeValidationError(
            code="INVALID_PROJECT_ID",
            message="project_id exceeds maximum length of 128 characters.",
            status_code=400,
        )
    if RE_UNSAFE_IDENTIFIER.search(project_id) or ".." in project_id:
        raise BridgeValidationError(
            code="UNTRUSTED_PROJECT_PATH",
            message="project_id must not contain path traversal, slashes, or shell metacharacters.",
            status_code=400,
        )
    return project_id.strip()


def validate_conversation_id(conversation_id: Any) -> str:
    """Validates that conversation_id is safe, non-empty, and free of path traversal / shell characters."""
    if not conversation_id or not isinstance(conversation_id, str):
        raise BridgeValidationError(
            code="INVALID_CONVERSATION_ID",
            message="conversation_id must be a non-empty string.",
            status_code=400,
        )
    if len(conversation_id) > 128:
        raise BridgeValidationError(
            code="INVALID_CONVERSATION_ID",
            message="conversation_id exceeds maximum length of 128 characters.",
            status_code=400,
        )
    if RE_UNSAFE_IDENTIFIER.search(conversation_id) or ".." in conversation_id:
        raise BridgeValidationError(
            code="INVALID_CONVERSATION_ID",
            message="conversation_id must not contain path traversal, slashes, or shell metacharacters.",
            status_code=400,
        )
    return conversation_id.strip()



def validate_and_sanitize_capture(payload_raw: Any) -> Tuple[CapturePayload, int]:
    """Validates capture payload, enforces path security, and re-redacts secrets.
    
    Returns:
        (sanitized_payload, total_redacted_secrets)
    """
    if not isinstance(payload_raw, dict):
        raise BridgeValidationError(
            code="INVALID_PAYLOAD",
            message="Capture payload must be a JSON dictionary.",
            status_code=400,
        )

    try:
        capture = CapturePayload(**payload_raw)
    except ValidationError:
        raise BridgeValidationError(
            code="INVALID_PAYLOAD",
            message="Capture payload failed schema validation.",
            status_code=400,
        )

    # 1. Provider validation
    provider_clean = capture.provider.upper()
    if provider_clean not in SUPPORTED_PROVIDERS:
        raise BridgeValidationError(
            code="UNSUPPORTED_PROVIDER",
            message=f"Provider '{capture.provider}' is unsupported. Supported: {sorted(list(SUPPORTED_PROVIDERS))}.",
            status_code=400,
        )
    capture.provider = provider_clean

    # 2. Project ID security: never trust arbitrary or path-like project identifiers
    if capture.project_id:
        if RE_UNSAFE_IDENTIFIER.search(capture.project_id) or ".." in capture.project_id:
            raise BridgeValidationError(
                code="UNTRUSTED_PROJECT_PATH",
                message="project_id must not contain path traversal, slashes, or shell metacharacters.",
                status_code=400,
            )

    # 3. Title security
    if capture.title:
        # Strip control characters, keep bounded
        capture.title = capture.title[:256].strip()

    # 4. Messages validation
    if not capture.messages or len(capture.messages) == 0:
        raise BridgeValidationError(
            code="EMPTY_CONVERSATION",
            message="Capture payload must contain at least one message turn.",
            status_code=400,
        )

    total_redacted = 0
    for idx, msg in enumerate(capture.messages):
        role_clean = msg.role.upper()
        if role_clean not in {"USER", "ASSISTANT", "SYSTEM"}:
            msg.role = "USER"
        else:
            msg.role = role_clean

        if msg.sequence is None:
            msg.sequence = idx + 1

        # Re-redact on Python side: extension redaction is not trusted input
        redacted_content, was_redacted, summary = detect_and_redact(msg.content or "")
        msg.content = redacted_content
        if was_redacted:
            count = summary.get("total_secrets_detected", 0)
            total_redacted += count
            msg.metadata["bridge_redacted"] = True
            msg.metadata["bridge_secrets_detected"] = count

    return capture, total_redacted
