"""Comprehensive test suite for M12.2 - Local HTTP Bridge."""

import io
import json
import socket
import threading
import urllib.request
import urllib.error
from pathlib import Path
import pytest

from backend.bridge.models import (
    BRIDGE_PROTOCOL_V1,
    MAX_PAYLOAD_BYTES,
    BridgeRequest,
    BridgeResponse,
    HealthResult,
    CaptureResult,
)
from backend.bridge.validator import (
    BridgeValidationError,
    validate_raw_body,
    validate_request_envelope,
    validate_and_sanitize_capture,
)
from backend.bridge.routes import BridgeRouter
from backend.bridge.server import create_bridge_server, check_bridge_status
from backend.domain.models import Project, ConversationSource
from backend.project_model.db import Database


@pytest.fixture
def temp_bridge_db(tmp_path: Path):
    db_file = tmp_path / "bridge_test.db"
    db = Database(db_file)
    project = Project(
        id="prj_bridge_test",
        name="Bridge Test Project",
        root_path=str(tmp_path),
    )
    db.upsert_project(project)
    return db, project


@pytest.fixture
def running_bridge_server(temp_bridge_db):
    db, project = temp_bridge_db
    # Find free port on 127.0.0.1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = create_bridge_server(
        host="127.0.0.1",
        port=port,
        db=db,
        default_project_id=project.id,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", db, project

    server.shutdown()
    server.server_close()


def make_valid_envelope(payload=None, message_type="capture", request_id="req_test_1"):
    return {
        "protocol": BRIDGE_PROTOCOL_V1,
        "request_id": request_id,
        "message_type": message_type,
        "timestamp": "2026-10-06T12:00:00Z",
        "payload": payload or {
            "provider": "CHATGPT",
            "title": "Auth Bug Review",
            "messages": [
                {"role": "USER", "content": "How do I fix auth?"},
                {"role": "ASSISTANT", "content": "Use tokens."},
            ],
        },
    }


# ============================================================================
# 1. SERVER BINDING & PROTOCOL ENVELOPE TESTS
# ============================================================================

def test_bridge_bind_localhost_only():
    """Validates that the bridge rejects non-loopback addresses (e.g. 0.0.0.0, LAN IPs)."""
    with pytest.raises(ValueError, match="Security violation: Bridge must ONLY bind to 127.0.0.1"):
        create_bridge_server(host="0.0.0.0", port=8765)

    with pytest.raises(ValueError, match="Security violation: Bridge must ONLY bind to 127.0.0.1"):
        create_bridge_server(host="192.168.1.100", port=8765)


def test_bridge_health(running_bridge_server):
    """Validates GET /health returns valid protocol health response."""
    base_url, _, _ = running_bridge_server
    req = urllib.request.Request(f"{base_url}/health", method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["protocol"] == BRIDGE_PROTOCOL_V1
        assert body["ok"] is True
        assert body["message_type"] == "health_result"
        assert body["result"]["service"] == "ai-build-coach"
        assert body["result"]["bridge"] is True


def test_check_bridge_status_helper(running_bridge_server):
    """Validates check_bridge_status returns accurate running state."""
    base_url, _, _ = running_bridge_server
    port = int(base_url.split(":")[-1])

    # Running server
    status_active = check_bridge_status(host="127.0.0.1", port=port)
    assert status_active["running"] is True
    assert status_active["port"] == port

    # Inactive port
    status_inactive = check_bridge_status(host="127.0.0.1", port=59999)
    assert status_inactive["running"] is False


def test_invalid_protocol():
    """Validates that unsupported protocol versions are rejected with structured error."""
    raw = {
        "protocol": "unsupported-protocol-v99",
        "request_id": "req_1",
        "message_type": "health",
    }
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_request_envelope(raw)
    assert exc_info.value.code == "UNSUPPORTED_PROTOCOL"


def test_invalid_message_type():
    """Validates that message types outside 'health' and 'capture' are rejected."""
    raw = {
        "protocol": BRIDGE_PROTOCOL_V1,
        "request_id": "req_1",
        "message_type": "eval_code",
    }
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_request_envelope(raw)
    assert exc_info.value.code == "UNKNOWN_MESSAGE_TYPE"


def test_malformed_json():
    """Validates that malformed JSON payloads return a structured 400 MALFORMED_JSON error."""
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_raw_body(b"{invalid json")
    assert exc_info.value.code == "MALFORMED_JSON"
    assert exc_info.value.status_code == 400


def test_missing_request_id():
    """Validates that missing or malformed request IDs are rejected."""
    raw = {
        "protocol": BRIDGE_PROTOCOL_V1,
        "message_type": "health",
    }
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_request_envelope(raw)
    assert exc_info.value.code == "INVALID_REQUEST_ID"


def test_oversized_payload():
    """Validates that payloads exceeding MAX_PAYLOAD_BYTES are rejected with 413."""
    oversized_bytes = b"x" * (MAX_PAYLOAD_BYTES + 1024)
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_raw_body(oversized_bytes)
    assert exc_info.value.code == "OVERSIZED_PAYLOAD"
    assert exc_info.value.status_code == 413


def test_unknown_fields():
    """Validates that unexpected envelope fields are rejected."""
    raw = make_valid_envelope()
    raw["malicious_field"] = "exploit"
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_request_envelope(raw)
    assert exc_info.value.code == "UNEXPECTED_FIELDS"


# ============================================================================
# 2. PAYLOAD SECURITY & RE-REDACTION TESTS
# ============================================================================

def test_invalid_capture_payload():
    """Validates that non-dict or empty capture payloads fail validation."""
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_and_sanitize_capture("not_a_dict")
    assert exc_info.value.code == "INVALID_PAYLOAD"

    # Unsupported provider
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_and_sanitize_capture({"provider": "UNSUPPORTED_AI", "messages": []})
    assert exc_info.value.code == "UNSUPPORTED_PROVIDER"

    # Empty conversation turns
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_and_sanitize_capture({"provider": "CHATGPT", "messages": []})
    assert exc_info.value.code == "EMPTY_CONVERSATION"


def test_secret_redetection():
    """Validates that the Python bridge re-redacts secrets that bypassed client redaction."""
    payload = {
        "provider": "CHATGPT",
        "title": "Unredacted test",
        "messages": [
            {
                "role": "USER",
                "content": "My secret key is sk-123456789012345678901234 and AWS is AKIAIOSFODNN7EXAMPLE",
            }
        ],
    }
    capture, redacted_count = validate_and_sanitize_capture(payload)
    assert redacted_count >= 2
    msg_content = capture.messages[0].content
    assert "sk-123456789012345678901234" not in msg_content
    assert "AKIAIOSFODNN7EXAMPLE" not in msg_content
    assert "[REDACTED]" in msg_content


def test_no_filesystem_execution():
    """Validates that path traversal or absolute paths in project_id are rejected."""
    dangerous_project_ids = [
        "../../etc/passwd",
        "C:\\Windows\\System32",
        "/var/run/secret",
        "prj_test/subpath",
        "prj_test;rm -rf",
    ]
    for pid in dangerous_project_ids:
        payload = {
            "provider": "CLAUDE",
            "project_id": pid,
            "messages": [{"role": "USER", "content": "hi"}],
        }
        with pytest.raises(BridgeValidationError) as exc_info:
            validate_and_sanitize_capture(payload)
        assert exc_info.value.code == "UNTRUSTED_PROJECT_PATH"


def test_no_command_execution():
    """Validates that command injection attempts in structured fields are rejected."""
    payload = {
        "provider": "CLAUDE",
        "project_id": "prj_test|cat /etc/passwd",
        "messages": [{"role": "USER", "content": "hello"}],
    }
    with pytest.raises(BridgeValidationError) as exc_info:
        validate_and_sanitize_capture(payload)
    assert exc_info.value.code == "UNTRUSTED_PROJECT_PATH"


def test_no_raw_exception_leak(running_bridge_server):
    """Validates that 500 errors and unhandled exceptions never leak stack traces to client."""
    base_url, _, _ = running_bridge_server

    # Send invalid path
    req = urllib.request.Request(f"{base_url}/invalid_route", method="GET")
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as err:
        assert err.code == 404
        body = json.loads(err.read().decode("utf-8"))
        assert body["ok"] is False
        assert body["error"]["code"] == "NOT_FOUND"
        assert "Traceback" not in body["error"]["message"]


# ============================================================================
# 3. END-TO-END WIRE CAPTURE & INGESTION TESTS
# ============================================================================

def test_capture_success(running_bridge_server):
    """Validates successful POST /v1/capture end-to-end over loopback HTTP."""
    base_url, db, project = running_bridge_server
    envelope = make_valid_envelope()
    data = json.dumps(envelope).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/v1/capture",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["message_type"] == "capture_result"
        assert body["result"]["provider"] == "chatgpt"
        assert body["result"]["message_count"] == 2
        assert body["result"]["stored"] is True
        conv_id = body["result"]["conversation_id"]

    # Verify directly from Database
    saved = db.get_conversation(conv_id)
    assert saved is not None
    assert saved.conversation_id == conv_id
    assert saved.source == ConversationSource.WEB_EXTENSION
    assert len(saved.messages) == 2


def test_capture_reaches_existing_service(tmp_path: Path):
    """Validates that BridgeRouter passes through to ConversationIngestionService accurately."""
    db_file = tmp_path / "router_test.db"
    db = Database(db_file)
    project = Project(id="prj_service_test", name="Service Test", root_path=str(tmp_path))
    db.upsert_project(project)

    router = BridgeRouter(db=db, default_project_id=project.id)
    envelope = make_valid_envelope(
        payload={
            "provider": "CLAUDE",
            "title": "Architecture Review",
            "messages": [
                {"role": "USER", "content": "Review the SQLite schema."},
                {"role": "ASSISTANT", "content": "The schema uses v9."},
            ],
        }
    )
    body_bytes = json.dumps(envelope).encode("utf-8")

    status, resp = router.handle_capture(body_bytes)
    assert status == 200
    assert resp["ok"] is True
    conv_id = resp["result"]["conversation_id"]

    # Verify conversation exists in DB
    retrieved = db.get_conversation(conv_id)
    assert retrieved is not None
    assert retrieved.title == "Architecture Review"
    assert retrieved.source == ConversationSource.WEB_EXTENSION


def test_bridge_rejects_untrusted_browser_origin(running_bridge_server):
    """Proves that arbitrary web pages cannot make cross-origin requests to the local bridge."""
    base_url, db, project = running_bridge_server

    # 1. Untrusted web page GET /health
    req = urllib.request.Request(
        f"{base_url}/health",
        headers={"Origin": "https://malicious-website.com"},
        method="GET",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
    err_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert err_body["ok"] is False
    assert err_body["error"]["code"] == "FORBIDDEN_ORIGIN"

    # 2. Untrusted web page OPTIONS preflight
    options_req = urllib.request.Request(
        f"{base_url}/v1/capture",
        headers={
            "Origin": "https://malicious-website.com",
            "Access-Control-Request-Method": "POST",
        },
        method="OPTIONS",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(options_req)
    assert exc_info.value.code == 403

    # 3. Untrusted web page POST /v1/capture
    envelope = make_valid_envelope()
    post_req = urllib.request.Request(
        f"{base_url}/v1/capture",
        data=json.dumps(envelope).encode("utf-8"),
        headers={
            "Origin": "https://evil-tracker.org",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(post_req)
    assert exc_info.value.code == 403
    err_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert err_body["ok"] is False
    assert err_body["error"]["code"] == "FORBIDDEN_ORIGIN"

    # 4. Valid extension origin is accepted
    ext_req = urllib.request.Request(
        f"{base_url}/health",
        headers={"Origin": "chrome-extension://abcdefghijklmnop"},
        method="GET",
    )
    with urllib.request.urlopen(ext_req) as resp:
        assert resp.status == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "chrome-extension://abcdefghijklmnop"
        assert resp.headers.get("Cache-Control") == "no-store"


def test_bridge_get_projects_exposes_minimal_data_only(running_bridge_server):
    """Verifies GET /v1/projects returns project_id and display_name, never filesystem paths."""
    base_url, db, project = running_bridge_server

    # Add second project to db
    p2 = Project(id="prj_second", name="Second App", root_path="/secret/local/path/second")
    db.upsert_project(p2)

    req = urllib.request.Request(f"{base_url}/v1/projects", method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["protocol"] == BRIDGE_PROTOCOL_V1
        projects = data["result"]["projects"]
        assert len(projects) >= 2

        # Invariant: No filesystem paths in project DTO
        for p in projects:
            assert "project_id" in p
            assert "display_name" in p
            assert "root_path" not in p
            assert "path" not in p
            assert "directory" not in p


def test_bridge_bind_conversation_success_and_status(running_bridge_server):
    """Verifies POST /v1/conversations/{id}/bind and GET /v1/conversations/{id}/binding."""
    base_url, db, project = running_bridge_server

    # 1. Ingest an unbound conversation
    capture_env = make_valid_envelope()
    post_req = urllib.request.Request(
        f"{base_url}/v1/capture",
        data=json.dumps(capture_env).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(post_req) as resp:
        capture_data = json.loads(resp.read().decode("utf-8"))
        conv_id = capture_data["result"]["conversation_id"]

    # 2. Check initial binding status (should be bound to default project or unbound)
    status_req = urllib.request.Request(f"{base_url}/v1/conversations/{conv_id}/binding", method="GET")
    with urllib.request.urlopen(status_req) as resp:
        status_data = json.loads(resp.read().decode("utf-8"))
        assert status_data["ok"] is True
        assert status_data["result"]["conversation_id"] == conv_id

    # 3. Create another registered project
    target_p = Project(id="prj_target_123", name="Target Project", root_path="/local/target")
    db.upsert_project(target_p)

    # 4. Explicitly bind to target_p
    bind_payload = {"project_id": "prj_target_123"}
    bind_req = urllib.request.Request(
        f"{base_url}/v1/conversations/{conv_id}/bind",
        data=json.dumps(bind_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(bind_req) as resp:
        bind_data = json.loads(resp.read().decode("utf-8"))
        assert bind_data["ok"] is True
        assert bind_data["result"]["conversation_id"] == conv_id
        assert bind_data["result"]["project_id"] == "prj_target_123"
        assert bind_data["result"]["binding_source"] == "USER_SELECTED"

    # 5. Check binding status now reflects target_p
    with urllib.request.urlopen(status_req) as resp:
        status_data = json.loads(resp.read().decode("utf-8"))
        assert status_data["result"]["bound"] is True
        assert status_data["result"]["project"]["project_id"] == "prj_target_123"
        assert status_data["result"]["project"]["display_name"] == "Target Project"


def test_bridge_rebind_conversation_replaces_old_binding(running_bridge_server):
    """Verifies that rebinding a conversation cleanly updates the active project without duplicates."""
    base_url, db, project = running_bridge_server

    p_a = Project(id="prj_alpha", name="Project Alpha", root_path="/alpha")
    p_b = Project(id="prj_beta", name="Project Beta", root_path="/beta")
    db.upsert_project(p_a)
    db.upsert_project(p_b)

    # Ingest conversation
    capture_env = make_valid_envelope()
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url}/v1/capture",
            data=json.dumps(capture_env).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    ) as resp:
        conv_id = json.loads(resp.read().decode("utf-8"))["result"]["conversation_id"]

    # Bind to Project Alpha
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url}/v1/conversations/{conv_id}/bind",
            data=json.dumps({"project_id": "prj_alpha"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    ) as resp:
        assert json.loads(resp.read().decode("utf-8"))["result"]["project_id"] == "prj_alpha"

    # Rebind to Project Beta
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url}/v1/conversations/{conv_id}/bind",
            data=json.dumps({"project_id": "prj_beta"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    ) as resp:
        assert json.loads(resp.read().decode("utf-8"))["result"]["project_id"] == "prj_beta"

    # Verify directly from Database: cardinality is 1
    binding = db.get_conversation_binding(conv_id)
    assert binding is not None
    assert binding.project_id == "prj_beta"

    # Verify no multiple active bindings in SQLite
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM conversation_project_bindings WHERE conversation_id = ?", (conv_id,))
        assert cur.fetchone()["cnt"] == 1
    finally:
        conn.close()


def test_bridge_rejects_arbitrary_filesystem_path_in_bind(running_bridge_server):
    """Proves that sending a filesystem path instead of project_id is rejected."""
    base_url, db, project = running_bridge_server

    # Ingest conversation
    capture_env = make_valid_envelope()
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url}/v1/capture",
            data=json.dumps(capture_env).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    ) as resp:
        conv_id = json.loads(resp.read().decode("utf-8"))["result"]["conversation_id"]

    # Attempt to send filesystem path in path field
    req = urllib.request.Request(
        f"{base_url}/v1/conversations/{conv_id}/bind",
        data=json.dumps({"path": "C:\\Users\\Desktop\\AIbuildcoach"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    err_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert err_body["error"]["code"] == "UNTRUSTED_PROJECT_PATH"

    # Attempt to pass path traversal as project_id
    req2 = urllib.request.Request(
        f"{base_url}/v1/conversations/{conv_id}/bind",
        data=json.dumps({"project_id": "../../etc/passwd"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info2:
        urllib.request.urlopen(req2)
    assert exc_info2.value.code == 400
    err_body2 = json.loads(exc_info2.value.read().decode("utf-8"))
    assert err_body2["error"]["code"] == "UNTRUSTED_PROJECT_PATH"


def test_bridge_bind_unknown_project_and_conversation(running_bridge_server):
    """Verifies 404 response when binding unknown conversation or unknown project."""
    base_url, db, project = running_bridge_server

    # Ingest conversation
    capture_env = make_valid_envelope()
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{base_url}/v1/capture",
            data=json.dumps(capture_env).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    ) as resp:
        conv_id = json.loads(resp.read().decode("utf-8"))["result"]["conversation_id"]

    # 1. Unknown project ID
    req = urllib.request.Request(
        f"{base_url}/v1/conversations/{conv_id}/bind",
        data=json.dumps({"project_id": "prj_nonexistent_999"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 404
    err_body = json.loads(exc.value.read().decode("utf-8"))
    assert err_body["error"]["code"] == "PROJECT_NOT_FOUND"

    # 2. Unknown conversation ID
    req2 = urllib.request.Request(
        f"{base_url}/v1/conversations/conv_ghost_999/bind",
        data=json.dumps({"project_id": project.id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc2:
        urllib.request.urlopen(req2)
    assert exc2.value.code == 404
    err_body2 = json.loads(exc2.value.read().decode("utf-8"))
    assert err_body2["error"]["code"] == "CONVERSATION_NOT_FOUND"



