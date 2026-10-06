"""Deterministic stdout/stderr capture with bounding and secret redaction (Milestone 12.9)."""

from typing import Tuple, Dict, Any, List
from backend.context_engine.secrets import detect_and_redact

# Sensible bounded limits for terminal output persistence to prevent unbound memory or DB bloat
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB per stream
DEFAULT_SNIPPET_BYTES = 1000          # 1000 characters for payload snippets


class CapturedOutput:
    """Bounded, redacted process output."""

    def __init__(
        self,
        raw_stdout: str,
        raw_stderr: str,
        max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ):
        self.max_bytes = max_bytes
        self.raw_stdout_len = len(raw_stdout)
        self.raw_stderr_len = len(raw_stderr)

        # 1. Truncate if exceeding max_bytes
        truncated_stdout, stdout_was_truncated = self._truncate(raw_stdout, max_bytes)
        truncated_stderr, stderr_was_truncated = self._truncate(raw_stderr, max_bytes)

        self.stdout_truncated = stdout_was_truncated
        self.stderr_truncated = stderr_was_truncated
        self.output_truncated = stdout_was_truncated or stderr_was_truncated

        # 2. Redact secrets deterministically
        self.stdout, stdout_redacted, stdout_sec_summary = detect_and_redact(truncated_stdout)
        self.stderr, stderr_redacted, stderr_sec_summary = detect_and_redact(truncated_stderr)

        self.secrets_redacted = stdout_redacted or stderr_redacted
        self.secrets_count = (
            stdout_sec_summary.get("total_secrets_detected", 0) +
            stderr_sec_summary.get("total_secrets_detected", 0)
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> Tuple[str, bool]:
        """Truncates string to max_chars preserving the tail end (most informative for errors)."""
        if len(text) <= max_chars:
            return text, False
        # Keep head (first 20%) and tail (last 80%) with explicit truncation marker
        head_len = int(max_chars * 0.2)
        tail_len = max_chars - head_len - 60
        head = text[:head_len]
        tail = text[-tail_len:] if tail_len > 0 else ""
        truncated_text = f"{head}\n\n[... OUTPUT TRUNCATED BY BUILD COACH ({len(text) - max_chars} bytes omitted) ...]\n\n{tail}"
        return truncated_text, True

    def get_stdout_snippet(self, length: int = DEFAULT_SNIPPET_BYTES) -> str:
        """Returns the tail end of the stdout snippet for event payloads."""
        if not self.stdout:
            return ""
        return self.stdout[-length:].strip()

    def get_stderr_snippet(self, length: int = DEFAULT_SNIPPET_BYTES) -> str:
        """Returns the tail end of the stderr snippet for event payloads."""
        if not self.stderr:
            return ""
        return self.stderr[-length:].strip()

    def to_metadata(self) -> Dict[str, Any]:
        """Returns summary metadata for audit and event provenance."""
        return {
            "stdout_bytes": self.raw_stdout_len,
            "stderr_bytes": self.raw_stderr_len,
            "output_truncated": self.output_truncated,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "secrets_redacted": self.secrets_redacted,
            "secrets_detected_count": self.secrets_count,
        }
