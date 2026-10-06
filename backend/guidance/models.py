"""Domain models for Milestone 12.7: Knowledge Gap & Next Action Engine."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class GapCategory(str, Enum):
    """Normalized categories for knowledge and verification gaps."""
    UNDERSTANDING = "UNDERSTANDING"
    VERIFICATION = "VERIFICATION"
    UNRESOLVED_ERROR = "UNRESOLVED_ERROR"
    CODE_CHANGE_REVIEW = "CODE_CHANGE_REVIEW"
    TEST_COVERAGE = "TEST_COVERAGE"
    ARCHITECTURE = "ARCHITECTURE"
    DEPENDENCY = "DEPENDENCY"
    RUNTIME_BEHAVIOR = "RUNTIME_BEHAVIOR"
    UNKNOWN = "UNKNOWN"


class ActionType(str, Enum):
    """Project-agnostic next action types."""
    READ_FILE = "READ_FILE"
    INSPECT_DIFF = "INSPECT_DIFF"
    RUN_TEST = "RUN_TEST"
    RUN_COMMAND = "RUN_COMMAND"
    CHECK_RUNTIME = "CHECK_RUNTIME"
    REVIEW_EVIDENCE = "REVIEW_EVIDENCE"
    EXPLAIN_BACK = "EXPLAIN_BACK"
    COMPARE_CHANGES = "COMPARE_CHANGES"
    INVESTIGATE_ERROR = "INVESTIGATE_ERROR"
    DOCUMENT_DECISION = "DOCUMENT_DECISION"


class ActionPriority(str, Enum):
    """Deterministic action priority rankings."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ActionStatus(str, Enum):
    """Action execution lifecycle states."""
    PROPOSED = "PROPOSED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class GuidanceStatus(str, Enum):
    """Status of the overall guidance plan."""
    CURRENT = "CURRENT"
    STALE = "STALE"
    RECALCULATING = "RECALCULATING"


class KnowledgeGap(BaseModel):
    """Evidence-grounded knowledge or verification gap."""
    gap_id: str
    project_id: str
    incident_id: Optional[str] = None
    category: GapCategory
    description: str
    reason: str
    priority: ActionPriority
    evidence_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class NextAction(BaseModel):
    """Single concrete, non-autonomous human action recommendation."""
    action_id: str
    project_id: str
    incident_id: Optional[str] = None
    action_type: ActionType
    title: str
    description: str
    priority: ActionPriority
    gap_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    completion_condition: str
    status: ActionStatus = ActionStatus.PROPOSED
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class GuidancePlan(BaseModel):
    """Deterministic guidance plan containing ranked knowledge gaps and candidate actions."""
    plan_id: str
    project_id: str
    incident_id: Optional[str] = None
    status: GuidanceStatus = GuidanceStatus.CURRENT
    state_fingerprint: str  # Hash of relevant events used to evaluate staleness
    top_next_action: Optional[NextAction] = None
    secondary_actions: List[NextAction] = Field(default_factory=list)
    gaps: List[KnowledgeGap] = Field(default_factory=list)
    actions: List[NextAction] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)
