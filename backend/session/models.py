"""Domain models and session state definitions for Milestone 12.8 (Unified Build Coach Session)."""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso
from backend.guidance.models import NextAction


class SessionState(str, Enum):
    """Deterministic high-level Build Coach session states."""
    READY = "READY"
    INVESTIGATING = "INVESTIGATING"
    VERIFYING = "VERIFYING"
    LEARNING = "LEARNING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class EvidenceSummary(BaseModel):
    """Deterministic aggregate evidence status (M11.1 / M12.4)."""
    total: int = 0
    supported: int = 0
    partially_supported: int = 0
    unsupported: int = 0
    unknown: int = 0


class VerificationSummary(BaseModel):
    """Deterministic verification status (M12.6)."""
    status: str = "UNKNOWN"  # VERIFIED, RECOVERED, PERSISTING, UNKNOWN
    targeted_test_observed: bool = False
    details: Optional[str] = None


class UnderstandingSummary(BaseModel):
    """Deterministic pedagogical comprehension status (M7)."""
    required: bool = False
    latest_state: Optional[str] = None  # UNDERSTOOD, PARTIALLY_UNDERSTOOD, MISUNDERSTOOD, NONE
    gap_count: int = 0
    action: Optional[str] = None


class GuidanceSummary(BaseModel):
    """Deterministic next action and gap status (M12.7)."""
    status: str = "CURRENT"  # CURRENT, STALE, RECALCULATING
    total_gaps: int = 0
    total_actions: int = 0
    top_action_type: Optional[str] = None
    top_priority: Optional[str] = None


class SessionSummary(BaseModel):
    """Compact project-level metrics and unknown factors."""
    recent_changes: int = 0
    active_incidents: int = 0
    recovered_incidents: int = 0
    total_observations: int = 0
    unknowns: List[str] = Field(default_factory=list)


class BuildCoachSession(BaseModel):
    """Unified project-level Build Coach session orchestrating evidence, observations, explanation, understanding, and guidance."""
    session_id: str
    project_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    state: SessionState
    summary: SessionSummary
    active_incident: Optional[Dict[str, Any]] = None
    recent_incidents: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_summary: Optional[EvidenceSummary] = None
    verification_summary: Optional[VerificationSummary] = None
    understanding_summary: Optional[UnderstandingSummary] = None
    guidance_summary: Optional[GuidanceSummary] = None
    next_action: Optional[NextAction] = None

    def to_api_dict(self) -> Dict[str, Any]:
        """Returns the minimal clean DTO contract for local bridge and clients."""
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "state": self.state.value,
            "summary": self.summary.model_dump(),
            "verification": self.verification_summary.model_dump() if self.verification_summary else None,
            "understanding": self.understanding_summary.model_dump() if self.understanding_summary else None,
            "guidance": self.guidance_summary.model_dump() if self.guidance_summary else None,
            "evidence": self.evidence_summary.model_dump() if self.evidence_summary else None,
            "next_action": self.next_action.model_dump() if self.next_action else None,
            "active_incident": self.active_incident,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
