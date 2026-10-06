"""Route dispatch and handling for the local bridge."""

import json
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

from backend.bridge.models import (
    BRIDGE_PROTOCOL_V1,
    BridgeResponse,
    BridgeError,
    HealthResult,
    CaptureResult,
)
from backend.bridge.validator import (
    BridgeValidationError,
    validate_raw_body,
    validate_request_envelope,
    validate_and_sanitize_capture,
)
from backend.domain.models import ConversationConsent, ConversationSource
from backend.project_model.db import Database
from backend.conversation.service import ConversationIngestionService


class BridgeRouter:
    """Dispatches HTTP requests to bridge route handlers."""

    def __init__(self, db: Optional[Database] = None, default_project_id: Optional[str] = None):
        self.db = db
        self.default_project_id = default_project_id

    def handle_health(self, request_id: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /health or message_type=health."""
        req_id = request_id or "health_check"
        response = BridgeResponse(
            protocol=BRIDGE_PROTOCOL_V1,
            request_id=req_id,
            ok=True,
            message_type="health_result",
            result=HealthResult().model_dump(),
        )
        return 200, response.model_dump()

    def handle_capture(self, raw_body: bytes) -> Tuple[int, Dict[str, Any]]:
        """Handles POST /v1/capture with strict envelope and payload validation."""
        req_id = "unknown_request"
        try:
            # 1. Parse and validate raw JSON body
            data = validate_raw_body(raw_body)

            # 2. Validate envelope
            req = validate_request_envelope(data)
            req_id = req.request_id

            if req.message_type == "health":
                return self.handle_health(req_id)

            if req.message_type != "capture":
                raise BridgeValidationError(
                    code="UNKNOWN_MESSAGE_TYPE",
                    message=f"Message type '{req.message_type}' cannot be processed on /v1/capture.",
                    status_code=400,
                )

            # 3. Validate capture payload
            capture_payload, redacted_count = validate_and_sanitize_capture(req.payload)

            # 4. Integrate with existing ingestion service
            consent = ConversationConsent(
                consent_id=f"cst_bridge_{req.request_id}",
                approved=True,
                scope="BRIDGE_INGESTION",
            )

            service = ConversationIngestionService(db=self.db)
            target_project_id = capture_payload.project_id or self.default_project_id

            # Pass validated and re-redacted payload to canonical service
            saved_conv = service.ingest(
                provider=capture_payload.provider,
                raw_payload=capture_payload.model_dump(),
                consent=consent,
                source=ConversationSource.WEB_EXTENSION,
                project_id=target_project_id,
                title=capture_payload.title,
            )

            capture_result = CaptureResult(
                conversation_id=saved_conv.conversation_id,
                message_count=len(saved_conv.messages),
                provider=saved_conv.provider.lower(),
                stored=self.db is not None,
            )

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="capture_result",
                result=capture_result.model_dump(),
            )
            return 200, response.model_dump()

        except BridgeValidationError as bve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code=bve.code, message=bve.message),
            )
            return bve.status_code, error_resp.model_dump()

        except Exception:
            # Mask internal error, no stack trace leak
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error processing capture.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_not_found(self, path: str) -> Tuple[int, Dict[str, Any]]:
        """Handles unregistered paths with a structured 404 envelope."""
        error_resp = BridgeResponse(
            protocol=BRIDGE_PROTOCOL_V1,
            request_id="req_not_found",
            ok=False,
            error=BridgeError(
                code="NOT_FOUND",
                message=f"Path '{path}' not found on bridge.",
            ),
        )
        return 404, error_resp.model_dump()
