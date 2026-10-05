"""Base contract and utility helpers for provider-neutral conversation adapters."""

import json
import uuid
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Union, Dict, Any, List, Optional, Tuple

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    utc_now_iso,
)


def format_timestamp(ts: Any) -> Optional[str]:
    """Converts a Unix timestamp or datetime string into a canonical ISO 8601 UTC string.
    
    Returns None if missing or invalid, never inventing timestamps.
    """
    if ts is None or ts == "":
        return None

    if isinstance(ts, (int, float)):
        try:
            # Handles unix seconds (or milliseconds if > 1e11)
            seconds = ts / 1000.0 if ts > 1e11 else float(ts)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    if isinstance(ts, str):
        clean_ts = ts.strip()
        if not clean_ts:
            return None
        # Try parsing ISO formats or standard strings
        try:
            # Replace trailing Z with +00:00 for fromisoformat compatibility
            iso_cand = clean_ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            # If string is a stringified float/int
            try:
                num = float(clean_ts)
                return format_timestamp(num)
            except ValueError:
                # Return string as explicit recorded timestamp
                return clean_ts

    return None


def generate_deterministic_id(prefix: str, content: str) -> str:
    """Generates a reproducible 16-char hex ID from content."""
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


class ConversationAdapter(ABC):
    """Abstract interface for provider-specific conversation normalization."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """The canonical provider name (e.g. ConversationProvider.CHATGPT)."""
        pass

    @abstractmethod
    def normalize(
        self,
        raw_input: Union[str, Dict[str, Any], list],
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        """Normalizes provider-specific conversation payload into provider-neutral Conversation model."""
        pass

    def _parse_raw(self, raw_input: Union[str, Dict[str, Any], list]) -> Any:
        """Safely parses raw input if provided as a JSON string, or returns data structure as-is."""
        if isinstance(raw_input, str):
            clean = raw_input.strip()
            if not clean:
                return {}
            try:
                return json.loads(clean)
            except (json.JSONDecodeError, ValueError):
                return clean
        return raw_input
