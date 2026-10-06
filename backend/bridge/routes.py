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
    ProjectSummaryDTO,
    ProjectListResult,
    BindResult,
    BindingStatusResult,
)
from backend.bridge.validator import (
    BridgeValidationError,
    validate_raw_body,
    validate_request_envelope,
    validate_and_sanitize_capture,
    validate_project_id,
    validate_conversation_id,
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

    def handle_list_projects(self, request_id: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /v1/projects - returns only project_id and display_name (no filesystem paths)."""
        req_id = request_id or "req_projects_list"
        projects = []
        if self.db is not None:
            db_projects = self.db.list_projects()
            projects = [
                ProjectSummaryDTO(project_id=p.id, display_name=p.name)
                for p in db_projects
            ]
        elif self.default_project_id:
            projects = [
                ProjectSummaryDTO(project_id=self.default_project_id, display_name=self.default_project_id)
            ]

        result = ProjectListResult(projects=projects)
        response = BridgeResponse(
            protocol=BRIDGE_PROTOCOL_V1,
            request_id=req_id,
            ok=True,
            message_type="project_list_result",
            result=result.model_dump(),
        )
        return 200, response.model_dump()

    def handle_bind_conversation(self, conversation_id: str, raw_body: bytes) -> Tuple[int, Dict[str, Any]]:
        """Handles POST /v1/conversations/{conversation_id}/bind."""
        req_id = f"req_bind_{conversation_id[:12]}"
        try:
            # 1. Validate conversation_id
            clean_conv_id = validate_conversation_id(conversation_id)

            # 2. Parse and validate JSON body
            data = validate_raw_body(raw_body)

            # Support both wrapped envelope ({protocol, request_id, message_type: "bind", payload: {project_id}})
            # and direct payload ({project_id: "..."})
            if "protocol" in data:
                req = validate_request_envelope(data)
                req_id = req.request_id
                if req.message_type != "bind":
                    raise BridgeValidationError(
                        code="UNKNOWN_MESSAGE_TYPE",
                        message=f"Message type '{req.message_type}' cannot be processed on /bind.",
                        status_code=400,
                    )
                payload_dict = req.payload or {}
            else:
                payload_dict = data

            # Reject path keys
            for forbidden_key in ("path", "root", "filesystem_path", "directory", "cwd"):
                if forbidden_key in payload_dict:
                    raise BridgeValidationError(
                        code="UNTRUSTED_PROJECT_PATH",
                        message=f"Field '{forbidden_key}' is forbidden. Bridge accepts project_id only.",
                        status_code=400,
                    )

            raw_proj_id = payload_dict.get("project_id")
            clean_proj_id = validate_project_id(raw_proj_id)

            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            # 3. Check conversation exists
            conv = self.db.get_conversation(clean_conv_id)
            if not conv:
                raise BridgeValidationError(
                    code="CONVERSATION_NOT_FOUND",
                    message=f"Conversation '{clean_conv_id}' not found.",
                    status_code=404,
                )

            # 4. Check project exists in registry
            proj = self.db.get_project_by_id(clean_proj_id)
            if not proj:
                raise BridgeValidationError(
                    code="PROJECT_NOT_FOUND",
                    message=f"Project '{clean_proj_id}' is not registered locally.",
                    status_code=404,
                )

            # 5. Atomic binding
            binding = self.db.bind_conversation_to_project(
                conversation_id=clean_conv_id,
                project_id=clean_proj_id,
            )

            result = BindResult(
                conversation_id=binding.conversation_id,
                project_id=binding.project_id,
                binding_source=binding.binding_source,
            )

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="bind_result",
                result=result.model_dump(),
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
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error processing binding.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_get_binding(self, conversation_id: str) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /v1/conversations/{conversation_id}/binding."""
        req_id = f"req_status_{conversation_id[:12]}"
        try:
            clean_conv_id = validate_conversation_id(conversation_id)

            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            # Check conversation exists
            conv = self.db.get_conversation(clean_conv_id)
            if not conv:
                raise BridgeValidationError(
                    code="CONVERSATION_NOT_FOUND",
                    message=f"Conversation '{clean_conv_id}' not found.",
                    status_code=404,
                )

            binding = self.db.get_conversation_binding(clean_conv_id)
            if binding:
                proj = self.db.get_project_by_id(binding.project_id)
                proj_dto = (
                    ProjectSummaryDTO(project_id=proj.id, display_name=proj.name)
                    if proj
                    else ProjectSummaryDTO(project_id=binding.project_id, display_name=binding.project_id)
                )
                result = BindingStatusResult(
                    conversation_id=clean_conv_id,
                    bound=True,
                    project=proj_dto,
                )
            else:
                result = BindingStatusResult(
                    conversation_id=clean_conv_id,
                    bound=False,
                    project=None,
                )

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="binding_status_result",
                result=result.model_dump(),
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
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error retrieving binding status.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_analyze_evidence(self, conversation_id: str, raw_body: bytes = b"") -> Tuple[int, Dict[str, Any]]:
        """Handles POST /v1/conversations/{conversation_id}/evidence."""
        req_id = f"req_evidence_{conversation_id[:12]}"
        try:
            clean_conv_id = validate_conversation_id(conversation_id)

            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            # Optional body envelope check (can be empty body or envelope with message_type: evidence)
            if raw_body and raw_body.strip():
                try:
                    data = validate_raw_body(raw_body)
                    if "protocol" in data:
                        req = validate_request_envelope(data)
                        req_id = req.request_id
                        if req.message_type not in ("evidence", "analyze"):
                            raise BridgeValidationError(
                                code="UNKNOWN_MESSAGE_TYPE",
                                message=f"Message type '{req.message_type}' cannot be processed on /evidence.",
                                status_code=400,
                            )
                except BridgeValidationError:
                    raise
                except Exception:
                    pass

            # Check conversation exists
            conv = self.db.get_conversation(clean_conv_id)
            if not conv:
                raise BridgeValidationError(
                    code="CONVERSATION_NOT_FOUND",
                    message=f"Conversation '{clean_conv_id}' not found.",
                    status_code=404,
                )

            # Check binding exists
            binding = self.db.get_conversation_binding(clean_conv_id)
            if not binding:
                raise BridgeValidationError(
                    code="PROJECT_BINDING_REQUIRED",
                    message=f"Conversation '{clean_conv_id}' must be bound to a project before analyzing evidence.",
                    status_code=422,
                )

            from backend.conversation.evidence_service import ConversationEvidenceService
            service = ConversationEvidenceService(self.db)
            analysis_result = service.analyze_conversation(
                conversation_id=clean_conv_id,
                project_id=binding.project_id,
            )

            result_dto = {
                "conversation_id": analysis_result.conversation_id,
                "project_id": analysis_result.project_id,
                "claims_count": len(analysis_result.claims),
                "evidence_links_count": len(analysis_result.evidence_links),
                "summary": analysis_result.summary,
                "claims": [c.model_dump() for c in analysis_result.claims],
                "evidence_links": [l.model_dump() for l in analysis_result.evidence_links],
            }

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="evidence_result",
                result=result_dto,
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
        except ValueError as ve:
            err_msg = str(ve)
            code = "PROJECT_BINDING_REQUIRED" if "PROJECT_BINDING_REQUIRED" in err_msg else "INVALID_ARGUMENTS"
            status_code = 422 if "PROJECT_BINDING_REQUIRED" in err_msg else 400
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code=code, message=err_msg),
            )
            return status_code, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error analyzing evidence.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_record_observation(self, project_id: str, raw_body: bytes) -> Tuple[int, Dict[str, Any]]:
        """Handles POST /v1/projects/{project_id}/observations."""
        req_id = f"req_obs_{project_id[:12]}"
        try:
            clean_proj_id = validate_project_id(project_id)
            data = validate_raw_body(raw_body)

            if "protocol" in data:
                req = validate_request_envelope(data)
                req_id = req.request_id
                payload_dict = req.payload or {}
            else:
                payload_dict = data

            event_type = payload_dict.get("event_type")
            if not event_type:
                raise BridgeValidationError(
                    code="MISSING_EVENT_TYPE",
                    message="Field 'event_type' is required.",
                    status_code=400,
                )

            source = payload_dict.get("source", "TERMINAL")
            event_payload = payload_dict.get("payload", {})
            provenance = payload_dict.get("provenance", {"source": source})
            timestamp = payload_dict.get("timestamp")

            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            from backend.observation.service import ObservationService
            service = ObservationService(self.db)
            event = service.record_event(
                project_id=clean_proj_id,
                event_type=event_type,
                source=source,
                payload=event_payload,
                provenance=provenance,
                timestamp=timestamp,
            )

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="observation_result",
                result={"event": event.model_dump()},
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
        except ValueError as ve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code="INVALID_ARGUMENTS", message=str(ve)),
            )
            return 400, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error recording observation.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_get_timeline(self, project_id: str) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /v1/projects/{project_id}/timeline."""
        req_id = f"req_tl_{project_id[:12]}"
        try:
            clean_proj_id = validate_project_id(project_id)
            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            from backend.observation.service import ObservationService
            service = ObservationService(self.db)
            events = service.get_timeline(clean_proj_id)

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="timeline_result",
                result={
                    "project_id": clean_proj_id,
                    "total_events": len(events),
                    "events": [e.model_dump() for e in events],
                },
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
        except ValueError as ve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code="INVALID_ARGUMENTS", message=str(ve)),
            )
            return 400, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error retrieving timeline.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_explain_timeline(
        self,
        project_id: str,
        raw_body: bytes = b"",
    ) -> Tuple[int, Dict[str, Any]]:
        """Handles POST /v1/projects/{project_id}/explain (M12.6)."""
        req_id = f"req_expl_{project_id[:12]}"
        try:
            clean_proj_id = validate_project_id(project_id)
            incident_id = None
            has_consent = False
            explicit_api_key = None

            if raw_body and raw_body.strip():
                data = validate_raw_body(raw_body)
                if "protocol" in data:
                    req = validate_request_envelope(data)
                    req_id = req.request_id
                    payload_dict = req.payload or {}
                else:
                    payload_dict = data
                incident_id = payload_dict.get("incident_id")
                has_consent = bool(payload_dict.get("has_consent") or payload_dict.get("consent"))
                explicit_api_key = payload_dict.get("api_key")

            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            from backend.observation.service import ObservationService
            service = ObservationService(self.db)
            explanation = service.explain_incident(
                project_id=clean_proj_id,
                incident_id=incident_id,
                has_consent=has_consent,
                explicit_api_key=explicit_api_key,
            )

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="explanation_result",
                result={"explanation": explanation.model_dump()},
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
        except ValueError as ve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code="INVALID_ARGUMENTS", message=str(ve)),
            )
            return 400, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error explaining timeline incident.",
                ),
            )
    def handle_get_guidance(
        self,
        project_id: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /v1/projects/{project_id}/guidance (M12.7)."""
        req_id = f"req_guide_{project_id[:12]}"
        try:
            clean_proj_id = validate_project_id(project_id)
            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            from backend.guidance.service import GuidanceService
            service = GuidanceService(self.db)
            plan = service.generate_plan(project_id=clean_proj_id)

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="guidance_result",
                result={
                    "project_id": clean_proj_id,
                    "plan_id": plan.plan_id,
                    "status": plan.status.value,
                    "top_next_action": plan.top_next_action.model_dump() if plan.top_next_action else None,
                    "secondary_actions": [a.model_dump() for a in plan.secondary_actions],
                    "total_gaps": len(plan.gaps),
                    "gaps": [
                        {
                            "gap_id": g.gap_id,
                            "category": g.category.value,
                            "description": g.description,
                            "priority": g.priority.value,
                            "reason": g.reason,
                            "evidence_ids": g.evidence_ids,
                        }
                        for g in plan.gaps
                    ],
                    "generated_at": plan.generated_at,
                },
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
        except ValueError as ve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code="INVALID_ARGUMENTS", message=str(ve)),
            )
            return 400, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error generating guidance.",
                ),
            )
            return 500, error_resp.model_dump()

    def handle_get_session(
        self,
        project_id: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Handles GET /v1/projects/{project_id}/session (M12.8)."""
        req_id = f"req_sess_{project_id[:12]}"
        try:
            clean_proj_id = validate_project_id(project_id)
            if self.db is None:
                raise BridgeValidationError(
                    code="DATABASE_UNAVAILABLE",
                    message="Database is not available on this bridge instance.",
                    status_code=503,
                )

            from backend.session.service import SessionService
            service = SessionService(self.db)
            session = service.get_or_create_session(project_id=clean_proj_id)

            response = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=True,
                message_type="session_result",
                result=session.to_api_dict(),
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
        except ValueError as ve:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(code="INVALID_ARGUMENTS", message=str(ve)),
            )
            return 400, error_resp.model_dump()
        except Exception:
            error_resp = BridgeResponse(
                protocol=BRIDGE_PROTOCOL_V1,
                request_id=req_id,
                ok=False,
                error=BridgeError(
                    code="INTERNAL_ERROR",
                    message="Bridge encountered an internal error retrieving session.",
                ),
            )
            return 500, error_resp.model_dump()

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
            # M12.3: Only bind to a project if the user explicitly provided project_id in payload.
            # Never default or infer project binding; unbound conversations remain unbound.
            target_project_id = capture_payload.project_id

            # Pass validated and re-redacted payload to canonical service
            saved_conv = service.ingest(
                provider=capture_payload.provider,
                raw_payload=capture_payload.model_dump(),
                consent=consent,
                source=ConversationSource.WEB_EXTENSION,
                project_id=target_project_id,
                title=capture_payload.title,
            )

            # If project_id was provided and valid, create binding in conversation_project_bindings
            if self.db is not None and target_project_id:
                try:
                    self.db.bind_conversation_to_project(
                        conversation_id=saved_conv.conversation_id,
                        project_id=target_project_id,
                    )
                except Exception:
                    pass  # Non-fatal if project wasn't pre-registered

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

