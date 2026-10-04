"""Headless one-shot JSON formatting and stdout purity enforcement."""

import sys
import json
from typing import Any, Dict, Optional


def emit_json_response(
    command: str,
    action: str,
    data: Any,
    error: Optional[Dict[str, Any]] = None,
    status: str = "success",
) -> None:
    """Emits strictly one valid JSON document on stdout and flushes."""
    payload = {
        "status": status,
        "command": command,
        "action": action,
        "data": data,
        "error": error,
    }
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    sys.stdout.flush()


def emit_json_error(
    command: str,
    action: str,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Emits a standardized error envelope on stdout."""
    error_dict = {
        "code": code,
        "message": message,
        "details": details or {},
    }
    emit_json_response(
        command=command,
        action=action,
        data=None,
        error=error_dict,
        status="error",
    )


def log_diagnostic(message: str) -> None:
    """Logs diagnostics strictly to sys.stderr to protect stdout purity."""
    sys.stderr.write(str(message) + "\n")
    sys.stderr.flush()
