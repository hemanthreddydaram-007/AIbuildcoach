"""Pydantic domain models for AI Gateway."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class ClaimType(str, Enum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    RECOMMENDATION = "RECOMMENDATION"
    UNKNOWN = "UNKNOWN"


class StructuredClaim(BaseModel):
    statement: str
    claim_type: ClaimType
    evidence_refs: List[str] = Field(default_factory=list)
    file_path: Optional[str] = None
    confidence: str = "HIGH"
    grounded: bool = False
    rejected: bool = False
    validation_notes: Optional[str] = None


class ExplanationResponse(BaseModel):
    summary: str
    claims: List[StructuredClaim] = Field(default_factory=list)
    unresolved_questions: List[str] = Field(default_factory=list)


class ConsentToken(BaseModel):
    token_id: str
    packet_id: str
    packet_hash: str
    provider: str = "gemini"
    model: str = "gemini-3.8-flash"
    approved_at: str = Field(default_factory=utc_now_iso)
    expires_at: str
    user_acknowledged: bool = True


class TransmissionPreview(BaseModel):
    packet_id: str
    packet_hash: str
    provider: str
    model: str
    token_estimate: int
    files_included: List[str]
    redaction_summary: Dict[str, Any]
    item_count: int


class RawInteractionResponse(BaseModel):
    status_code: int
    raw_json: Dict[str, Any]
    raw_text: str
    latency_ms: float
    tokens_prompt: Optional[int] = None
    tokens_candidate: Optional[int] = None


class ValidatedGatewayResult(BaseModel):
    id: str
    packet_id: str
    provider: str
    model: str
    generated_at: str = Field(default_factory=utc_now_iso)
    summary: str
    claims: List[StructuredClaim]
    unresolved_questions: List[str] = Field(default_factory=list)
    tokens_prompt: int = 0
    tokens_candidate: int = 0
    latency_ms: float = 0.0
    validation_summary: Dict[str, Any] = Field(default_factory=dict)
