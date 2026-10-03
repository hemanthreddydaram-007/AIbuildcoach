"""Deterministic compression, budget enforcement, and token estimation for AI Build Coach."""

import re
from typing import List, Tuple, Dict, Any, Optional
from backend.domain.models import ContextItem, ContextSourceType

# Default engineering target token budget
DEFAULT_BUDGET_TOKENS = 4000
CHARS_PER_TOKEN = 4
MAX_FILE_LINES_IN_CONTEXT = 300


def estimate_tokens(text: str) -> int:
    """Estimates tokens deterministically using character heuristic (4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def normalize_whitespace(text: str) -> str:
    """Deterministically normalizes whitespace without altering code semantics.
    
    - Strips trailing whitespace per line
    - Collapses 3 or more consecutive newlines into 2 newlines
    - Strips leading and trailing blank lines
    """
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    joined = "\n".join(lines)
    # Collapse 3 or more newlines into 2
    collapsed = re.sub(r"\n{3,}", "\n\n", joined)
    return collapsed.strip()


def compress_item_content(content: str, is_binary: bool = False, file_size: Optional[int] = None) -> Tuple[str, bool]:
    """Compresses item content deterministically.
    
    Returns: (compressed_content, was_truncated)
    """
    if is_binary:
        size_str = f"{file_size} bytes" if file_size is not None else "binary"
        return f"[Binary file content omitted: {size_str}]", False

    norm = normalize_whitespace(content)
    lines = norm.splitlines()

    # If the file is very large in line count, retain first 150 and last 50 lines with a clear truncation marker
    if len(lines) > MAX_FILE_LINES_IN_CONTEXT:
        head = lines[:150]
        tail = lines[-50:]
        omitted = len(lines) - 200
        pruned = head + [f"\n... [Context Engine: {omitted} lines pruned for budget] ...\n"] + tail
        return "\n".join(pruned), True

    return norm, False


def deduplicate_and_compress(
    items: List[ContextItem],
) -> List[ContextItem]:
    """Removes duplicate context items and normalizes content."""
    seen_signatures = set()
    compressed_items: List[ContextItem] = []

    for item in items:
        # Create deterministic signature for deduplication
        sig = (item.source_type, item.file_path, item.line_start, item.line_end, item.content.strip())
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        cleaned_content, _ = compress_item_content(item.content)
        item.content = cleaned_content
        compressed_items.append(item)

    return compressed_items


def apply_budget(
    items: List[ContextItem],
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> Tuple[List[ContextItem], int, str]:
    """Enforces context budget deterministically.
    
    Preserves highest relevance items first.
    If items exceed budget, lower-relevance items are dropped.
    
    Returns:
        (budgeted_items, total_token_estimate, truncation_status)
    """
    total_tokens = 0
    selected_items: List[ContextItem] = []
    truncated = False

    # Items should be ordered by priority (highest relevance score first)
    for item in items:
        item_tokens = estimate_tokens(item.content)
        
        # High-relevance items (score >= 90) are critical and always included
        if item.relevance_score >= 90.0:
            selected_items.append(item)
            total_tokens += item_tokens
        else:
            # Check if this item fits in remaining budget
            if total_tokens + item_tokens <= budget_tokens:
                selected_items.append(item)
                total_tokens += item_tokens
            else:
                # Exceeds budget, drop lower relevance item
                truncated = True

    truncation_status = "TRUNCATED" if truncated else "NONE"
    return selected_items, total_tokens, truncation_status
