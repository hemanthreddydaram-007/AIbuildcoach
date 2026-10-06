"""Milestone 12.8: Unified Build Coach Session package."""

from backend.session.models import (
    BuildCoachSession,
    SessionState,
    SessionSummary,
    EvidenceSummary,
    VerificationSummary,
    UnderstandingSummary,
    GuidanceSummary,
)
from backend.session.service import SessionService
from backend.session.state import compute_session_state
from backend.session.summary import (
    build_session_summary,
    format_human_session,
)

__all__ = [
    "BuildCoachSession",
    "SessionState",
    "SessionSummary",
    "EvidenceSummary",
    "VerificationSummary",
    "UnderstandingSummary",
    "GuidanceSummary",
    "SessionService",
    "compute_session_state",
    "build_session_summary",
    "format_human_session",
]
