"""Domain models for runtime failure & change observation (Milestone 12.5)."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class ObservationSource(str):
    TERMINAL = "TERMINAL"
    GIT = "GIT"
    PYTEST = "PYTEST"
    HTTP = "HTTP"
    PROCESS = "PROCESS"


class ObservationEventType(str):
    GIT_CHANGE = "GIT_CHANGE"
    COMMAND_STARTED = "COMMAND_STARTED"
    COMMAND_FINISHED = "COMMAND_FINISHED"
    PROCESS_STARTED = "PROCESS_STARTED"
    PROCESS_FINISHED = "PROCESS_FINISHED"
    TEST_STARTED = "TEST_STARTED"
    TEST_FINISHED = "TEST_FINISHED"
    HTTP_REQUEST = "HTTP_REQUEST"
    HTTP_RESPONSE = "HTTP_RESPONSE"
    RUNTIME_ERROR = "RUNTIME_ERROR"


VALID_EVENT_TYPES = {
    ObservationEventType.GIT_CHANGE,
    ObservationEventType.COMMAND_STARTED,
    ObservationEventType.COMMAND_FINISHED,
    ObservationEventType.PROCESS_STARTED,
    ObservationEventType.PROCESS_FINISHED,
    ObservationEventType.TEST_STARTED,
    ObservationEventType.TEST_FINISHED,
    ObservationEventType.HTTP_REQUEST,
    ObservationEventType.HTTP_RESPONSE,
    ObservationEventType.RUNTIME_ERROR,
}


class ObservationEvent(BaseModel):
    event_id: str
    project_id: str
    event_type: str
    timestamp: str
    source: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class NormalizedRuntimeError(BaseModel):
    error_kind: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    function_name: Optional[str] = None
    error_signature: str
    raw_snippet: Optional[str] = None


class CorrelationRelation(str):
    PRECEDES = "PRECEDES"
    AFFECTS_SAME_FILE = "AFFECTS_SAME_FILE"
    SAME_ERROR = "SAME_ERROR"
    ERROR_DISAPPEARED_AFTER_CHANGE = "ERROR_DISAPPEARED_AFTER_CHANGE"
    SAME_COMMAND = "SAME_COMMAND"
    SAME_TEST = "SAME_TEST"


class FixStatus(str):
    VERIFIED = "VERIFIED"
    RECOVERED = "RECOVERED"
    PERSISTING = "PERSISTING"
    UNKNOWN = "UNKNOWN"


class CorrelationLink(BaseModel):
    link_id: str
    source_event_id: str
    target_event_id: str
    relation_type: str
    description: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ExplanationPacket(BaseModel):
    project_id: str
    problem: Dict[str, Any] = Field(default_factory=dict)
    changes: List[Dict[str, Any]] = Field(default_factory=list)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    recovery: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)


class TimelineExplanationPacket(BaseModel):
    """Focused incident window packet for deterministic timeline explanation (M12.6)."""
    packet_version: str = "explanation-v1"
    packet_id: str
    project_id: str
    incident: Dict[str, Any] = Field(default_factory=dict)  # error_signature, error_type, message, location, event_id
    timeline: List[Dict[str, Any]] = Field(default_factory=list)  # focused event sequence with event_ids
    changes: List[Dict[str, Any]] = Field(default_factory=list)   # git changes relevant to the incident
    correlations: List[Dict[str, Any]] = Field(default_factory=list) # deterministic correlation links
    verification: List[Dict[str, Any]] = Field(default_factory=list) # post-change verification events
    fix_status: str = FixStatus.UNKNOWN
    confidence: str = "LOW"
    unknowns: List[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)


class StatementWithEvidence(BaseModel):
    """Factual or inferential statement strictly grounded by evidence_refs."""
    statement: str
    evidence_refs: List[str] = Field(default_factory=list)


class IncidentExplanation(BaseModel):
    """User-facing structured explanation produced from deterministic timeline evidence."""
    explanation_id: str
    packet_id: str
    project_id: str
    fix_status: str  # VERIFIED, RECOVERED, PERSISTING, UNKNOWN
    confidence: str  # HIGH, MEDIUM, LOW
    summary: str
    problem: List[StatementWithEvidence] = Field(default_factory=list)
    observed_sequence: List[StatementWithEvidence] = Field(default_factory=list)
    changes: List[StatementWithEvidence] = Field(default_factory=list)
    verification: List[StatementWithEvidence] = Field(default_factory=list)
    what_to_understand: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    ai_generated: bool = False
    generated_at: str = Field(default_factory=utc_now_iso)

