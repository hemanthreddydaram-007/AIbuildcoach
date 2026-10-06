"""Strict domain and protocol models for the local bridge."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

BRIDGE_PROTOCOL_V1 = "buildcoach-bridge-v1"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MB maximum request size


class BridgeError(BaseModel):
    code: str
    message: str


class HealthResult(BaseModel):
    service: str = "ai-build-coach"
    bridge: bool = True
    protocol: str = BRIDGE_PROTOCOL_V1


class CaptureResult(BaseModel):
    conversation_id: str
    message_count: int
    provider: str
    stored: bool = True


class CaptureMessagePayload(BaseModel):
    message_id: Optional[str] = None
    role: str
    content: str
    timestamp: Optional[str] = None
    sequence: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapturePayload(BaseModel):
    conversation_id: Optional[str] = None
    provider: str
    source: str = "WEB_EXTENSION"
    title: Optional[str] = None
    project_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    messages: List[CaptureMessagePayload] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProjectSummaryDTO(BaseModel):
    project_id: str
    display_name: str


class ProjectListResult(BaseModel):
    projects: List[ProjectSummaryDTO] = Field(default_factory=list)


class BindRequestPayload(BaseModel):
    project_id: str


class BindResult(BaseModel):
    conversation_id: str
    project_id: str
    binding_source: str = "USER_SELECTED"


class BindingStatusResult(BaseModel):
    conversation_id: str
    bound: bool
    project: Optional[ProjectSummaryDTO] = None


class EvidenceAnalysisResult(BaseModel):
    conversation_id: str
    project_id: str
    claims_count: int
    evidence_links_count: int
    summary: Dict[str, Any] = Field(default_factory=dict)
    claims: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_links: List[Dict[str, Any]] = Field(default_factory=list)


class BridgeRequest(BaseModel):
    protocol: str
    request_id: str
    message_type: str
    timestamp: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class BridgeResponse(BaseModel):
    protocol: str = BRIDGE_PROTOCOL_V1
    request_id: str
    ok: bool
    message_type: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[BridgeError] = None

