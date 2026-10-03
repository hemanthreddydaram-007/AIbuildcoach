"""Context Engine module exports."""

from backend.context_engine.engine import ContextEngine
from backend.context_engine.secrets import detect_and_redact, SecretCategory
from backend.context_engine.relevance import (
    extract_relevance_candidates,
    SCORE_DIRECTLY_CHANGED_FILE,
    SCORE_CHANGED_DIFF_HUNK,
    SCORE_EXPLICITLY_REQUESTED,
    SCORE_DIRECT_DEPENDENCY,
    SCORE_DIRECT_DEPENDENT,
    SCORE_RELATED_TEST,
    SCORE_RELATED_CONFIG,
    SCORE_CHANGE_EVIDENCE,
    SCORE_SUPPORTING_GRAPH,
    SCORE_UNRELATED_FILE,
)
from backend.context_engine.compression import (
    deduplicate_and_compress,
    apply_budget,
    estimate_tokens,
    DEFAULT_BUDGET_TOKENS,
)

__all__ = [
    "ContextEngine",
    "detect_and_redact",
    "SecretCategory",
    "extract_relevance_candidates",
    "deduplicate_and_compress",
    "apply_budget",
    "estimate_tokens",
    "DEFAULT_BUDGET_TOKENS",
    "SCORE_DIRECTLY_CHANGED_FILE",
    "SCORE_CHANGED_DIFF_HUNK",
    "SCORE_EXPLICITLY_REQUESTED",
    "SCORE_DIRECT_DEPENDENCY",
    "SCORE_DIRECT_DEPENDENT",
    "SCORE_RELATED_TEST",
    "SCORE_RELATED_CONFIG",
    "SCORE_CHANGE_EVIDENCE",
    "SCORE_SUPPORTING_GRAPH",
    "SCORE_UNRELATED_FILE",
]
