"""Deterministic normalization for commands, stack traces, errors, Git and HTTP observations."""

import re
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from backend.context_engine.secrets import detect_and_redact
from backend.observation.models import (
    ObservationEvent,
    ObservationEventType,
    ObservationSource,
    NormalizedRuntimeError,
)

# Common runtime error exception names
KNOWN_EXCEPTION_TYPES = {
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
    "TypeError",
    "NameError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "ValueError",
    "FileNotFoundError",
    "ZeroDivisionError",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
    "RuntimeError",
    "AssertionError",
}

# Regex to capture Python stack trace frames: File "path/to/file.py", line 42, in func_name
RE_PYTHON_FRAME = re.compile(
    r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)(?:,\s+in\s+([a-zA-Z0-9_<>\.]+))?'
)

# Regex to capture Python exception line: ExceptionName: error message
RE_PYTHON_EXCEPTION = re.compile(
    r"^([A-Z][a-zA-Z0-9_]*(?:Error|Exception|Exit|Interrupt)):\s*(.*)$",
    re.MULTILINE,
)

# Regex to capture HTTP response in logs or terminal: e.g. "GET /api/icecreams" 200 or HTTP 500
RE_HTTP_STATUS_LOG = re.compile(
    r'\"?([A-Z]+)\s+([^\s\"\?#]+)[^\"]*\"?\s+(\d{3})\b'
)


def compute_deterministic_id(prefix: str, *parts: Any) -> str:
    """Computes a deterministic hex ID from parts."""
    content = ":".join(str(p) for p in parts)
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


def compute_error_signature(error_kind: str, message: str, file_path: Optional[str] = None) -> str:
    """Computes a stable, normalized signature for an error.
    
    Ignores dynamic timestamps, memory addresses (0x7f...), line variations if appropriate.
    """
    clean_msg = re.sub(r"0x[0-9a-fA-F]+", "0x...", message.strip())
    clean_msg = re.sub(r"\s+", " ", clean_msg)
    parts = [error_kind.strip(), clean_msg]
    if file_path:
        norm_path = file_path.replace("\\", "/").strip()
        parts.append(norm_path)
    return compute_deterministic_id("errsig", *parts)


def parse_stack_trace_and_error(stderr_or_output: str) -> Optional[NormalizedRuntimeError]:
    """Deterministically parses runtime errors and stack traces without root cause guessing."""
    if not stderr_or_output or not stderr_or_output.strip():
        return None

    # Search for Python exception line
    exc_match = RE_PYTHON_EXCEPTION.search(stderr_or_output)
    if not exc_match:
        # Check for pytest assertion failure
        if "FAILED" in stderr_or_output and "AssertionError" in stderr_or_output:
            return NormalizedRuntimeError(
                error_kind="AssertionError",
                message="Test assertion failed",
                error_signature=compute_error_signature("AssertionError", "Test assertion failed"),
                raw_snippet=stderr_or_output[-500:].strip(),
            )
        return None

    error_kind = exc_match.group(1).strip()
    error_msg = exc_match.group(2).strip()

    # Search for frames in stack trace
    frames = list(RE_PYTHON_FRAME.finditer(stderr_or_output))
    file_path = None
    line_number = None
    function_name = None

    if frames:
        # Last frame in stack trace typically points to the site of failure
        last_frame = frames[-1]
        file_path = last_frame.group(1).replace("\\", "/").strip()
        try:
            line_number = int(last_frame.group(2))
        except (ValueError, TypeError):
            line_number = None
        if last_frame.group(3):
            function_name = last_frame.group(3).strip()

    signature = compute_error_signature(error_kind, error_msg, file_path)

    return NormalizedRuntimeError(
        error_kind=error_kind,
        message=error_msg,
        file_path=file_path,
        line_number=line_number,
        function_name=function_name,
        error_signature=signature,
        raw_snippet=stderr_or_output[-500:].strip(),
    )


def sanitize_and_redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively redacts secrets in dict/list payload."""
    sanitized: Dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str):
            redacted_str, _, _ = detect_and_redact(v)
            sanitized[k] = redacted_str
        elif isinstance(v, dict):
            sanitized[k] = sanitize_and_redact_payload(v)
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, str):
                    redacted_item, _, _ = detect_and_redact(item)
                    new_list.append(redacted_item)
                elif isinstance(item, dict):
                    new_list.append(sanitize_and_redact_payload(item))
                else:
                    new_list.append(item)
            sanitized[k] = new_list
        else:
            sanitized[k] = v
    return sanitized
