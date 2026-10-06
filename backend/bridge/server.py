"""Local HTTP bridge server binding exclusively to 127.0.0.1."""

import json
import socket
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from backend.bridge.models import MAX_PAYLOAD_BYTES, BRIDGE_PROTOCOL_V1
from backend.bridge.routes import BridgeRouter
from backend.project_model.db import Database


DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 8765


class BridgeHTTPRequestHandler(BaseHTTPRequestHandler):
    """Custom request handler enforcing localhost isolation and strict routing."""

    router: BridgeRouter

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging to prevent leaking sensitive request data."""
        pass

    def _is_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        # Direct non-browser HTTP requests (curl, python cli, local tools) have no Origin header
        if not origin:
            return True
        # Allow requests originating from chrome-extension:// or local loopback origins
        if (
            origin.startswith("chrome-extension://")
            or origin.startswith("http://127.0.0.1")
            or origin.startswith("http://localhost")
        ):
            return True
        return False

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if self._is_origin_allowed() and origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight requests."""
        if not self._is_origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._is_origin_allowed():
            self._respond_json(403, {
                "protocol": BRIDGE_PROTOCOL_V1,
                "request_id": "req_forbidden",
                "ok": False,
                "error": {
                    "code": "FORBIDDEN_ORIGIN",
                    "message": "Cross-origin requests from untrusted web origins are rejected.",
                },
            })
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/health", "/v1/health"}:
            status_code, response_dict = self.router.handle_health()
        elif path in {"/v1/projects", "/projects"}:
            status_code, response_dict = self.router.handle_list_projects()
        elif path.startswith("/v1/conversations/") and path.endswith("/binding"):
            # Format: /v1/conversations/{conversation_id}/binding
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "conversations" and parts[3] == "binding":
                conv_id = parts[2]
                status_code, response_dict = self.router.handle_get_binding(conv_id)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/projects/") and path.endswith("/timeline"):
            # Format: /v1/projects/{project_id}/timeline
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "timeline":
                proj_id = parts[2]
                status_code, response_dict = self.router.handle_get_timeline(proj_id)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/projects/") and path.endswith("/guidance"):
            # Format: /v1/projects/{project_id}/guidance (M12.7)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "guidance":
                proj_id = parts[2]
                status_code, response_dict = self.router.handle_get_guidance(proj_id)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/projects/") and path.endswith("/session"):
            # Format: /v1/projects/{project_id}/session (M12.8)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "session":
                proj_id = parts[2]
                status_code, response_dict = self.router.handle_get_session(proj_id)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        else:
            status_code, response_dict = self.router.handle_not_found(path)

        self._respond_json(status_code, response_dict)

    def do_POST(self) -> None:
        if not self._is_origin_allowed():
            self._respond_json(403, {
                "protocol": BRIDGE_PROTOCOL_V1,
                "request_id": "req_forbidden",
                "ok": False,
                "error": {
                    "code": "FORBIDDEN_ORIGIN",
                    "message": "Cross-origin requests from untrusted web origins are rejected.",
                },
            })
            return

        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))

        if content_length > MAX_PAYLOAD_BYTES:
            status_code, response_dict = self.router.handle_capture(b"")  # Triggers oversized error
            self._respond_json(413, {
                "protocol": BRIDGE_PROTOCOL_V1,
                "request_id": "req_oversized",
                "ok": False,
                "error": {
                    "code": "OVERSIZED_PAYLOAD",
                    "message": f"Payload size exceeds limit of {MAX_PAYLOAD_BYTES} bytes.",
                },
            })
            return

        body = self.rfile.read(content_length)

        if path in {"/v1/capture", "/capture"}:
            status_code, response_dict = self.router.handle_capture(body)
        elif path in {"/health", "/v1/health"}:
            status_code, response_dict = self.router.handle_health()
        elif path.startswith("/v1/conversations/") and path.endswith("/bind"):
            # Format: /v1/conversations/{conversation_id}/bind
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "conversations" and parts[3] == "bind":
                conv_id = parts[2]
                status_code, response_dict = self.router.handle_bind_conversation(conv_id, body)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/conversations/") and path.endswith("/evidence"):
            # Format: /v1/conversations/{conversation_id}/evidence
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "conversations" and parts[3] == "evidence":
                conv_id = parts[2]
                status_code, response_dict = self.router.handle_analyze_evidence(conv_id, body)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/projects/") and path.endswith("/observations"):
            # Format: /v1/projects/{project_id}/observations
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "observations":
                proj_id = parts[2]
                status_code, response_dict = self.router.handle_record_observation(proj_id, body)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        elif path.startswith("/v1/projects/") and path.endswith("/explain"):
            # Format: /v1/projects/{project_id}/explain (M12.6)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "projects" and parts[3] == "explain":
                proj_id = parts[2]
                status_code, response_dict = self.router.handle_explain_timeline(proj_id, body)
            else:
                status_code, response_dict = self.router.handle_not_found(path)
        else:
            status_code, response_dict = self.router.handle_not_found(path)

        self._respond_json(status_code, response_dict)

    def _respond_json(self, status_code: int, response_dict: Dict[str, Any]) -> None:
        body_bytes = json.dumps(response_dict).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


def create_bridge_server(
    host: str = DEFAULT_BRIDGE_HOST,
    port: int = DEFAULT_BRIDGE_PORT,
    db: Optional[Database] = None,
    default_project_id: Optional[str] = None,
) -> ThreadingHTTPServer:
    """Creates a local-only bridge server instance."""
    # Absolute security rule: ONLY bind to 127.0.0.1 or localhost
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            f"Security violation: Bridge must ONLY bind to 127.0.0.1 or localhost, received '{host}'."
        )

    router = BridgeRouter(db=db, default_project_id=default_project_id)

    class CustomHandler(BridgeHTTPRequestHandler):
        pass

    CustomHandler.router = router

    server = ThreadingHTTPServer((host, port), CustomHandler)
    return server


def check_bridge_status(
    host: str = DEFAULT_BRIDGE_HOST,
    port: int = DEFAULT_BRIDGE_PORT,
    timeout_s: float = 1.0,
) -> Dict[str, Any]:
    """Inspects if the local bridge is currently active on host:port."""
    url = f"http://{host}:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok") and body.get("result", {}).get("bridge"):
                    return {
                        "running": True,
                        "host": host,
                        "port": port,
                        "protocol": BRIDGE_PROTOCOL_V1,
                    }
    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, OSError):
        pass

    return {
        "running": False,
        "host": host,
        "port": port,
        "protocol": BRIDGE_PROTOCOL_V1,
    }
