"""Domain models for Milestone 7: Can I Explain This? Comprehension Loop."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class ComprehensionRating(str, Enum):
    """Comprehension evaluation rating."""
    UNDERSTOOD = "UNDERSTOOD"
    PARTIALLY_UNDERSTOOD = "PARTIALLY_UNDERSTOOD"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN = "UNKNOWN"


class ComprehensionDimension(str, Enum):
    """The exact 4 dimensions defined by Milestone 6."""
    PURPOSE = "PURPOSE"
    MECHANISM = "MECHANISM"
    FAILURE_MODES = "FAILURE_MODES"
    DOWNSTREAM_IMPACT = "DOWNSTREAM_IMPACT"


class GapSeverity(str, Enum):
    """Severity of an identified knowledge gap."""
    CRITICAL = "CRITICAL"
    MODERATE = "MODERATE"
    MINOR = "MINOR"


class RunStatus(str, Enum):
    """Execution status of a comprehension run."""
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class KnowledgeGap(BaseModel):
    """An identified gap in student understanding, anchored in evidence."""
    gap_id: str
    dimension: ComprehensionDimension
    severity: GapSeverity
    summary: str
    expected_understanding: str
    student_misconception: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    grounded: bool = False


class DimensionEvaluation(BaseModel):
    """Evaluation for one of the four required comprehension dimensions."""
    dimension: ComprehensionDimension
    rating: ComprehensionRating
    feedback: str
    gaps: List[KnowledgeGap] = Field(default_factory=list)
    evidence_references: List[str] = Field(default_factory=list)
    grounded: bool = False


class StudentExplanationSubmission(BaseModel):
    """Student's natural-language explanation of a code change."""
    submission_id: str
    project_id: str
    prompt_id: str
    changeset_id: str
    packet_id: str
    attempt_number: int = Field(default=1, ge=1, le=3)
    explanation_text: str  # Transient in-memory only; never persisted


class ReverificationPrompt(BaseModel):
    """Targeted re-prompt focusing on unaddressed gaps for subsequent attempt."""
    prompt_id: str
    parent_submission_id: str
    attempt_number: int = Field(ge=2, le=3)
    focus_questions: List[str]
    context_hint: Optional[str] = None


class TargetedTeaching(BaseModel):
    """Targeted pedagogical feedback synthesized from validated claims and context."""
    teaching_id: str
    summary: str
    key_takeaways: List[str] = Field(default_factory=list)
    code_annotations: Dict[str, str] = Field(default_factory=dict)
    grounded_evidence_ids: List[str] = Field(default_factory=list)
    reverification_prompt: Optional[ReverificationPrompt] = None
    grounded: bool = False


class ComprehensionEvaluationResult(BaseModel):
    """Top-level evaluation result for Workflow 2: Can I Explain This?"""
    evaluation_id: str
    submission_id: str
    project_id: str
    changeset_id: str
    packet_id: str
    prompt_id: str
    attempt_number: int
    overall_state: ComprehensionRating
    dimensions: Dict[ComprehensionDimension, DimensionEvaluation]
    all_gaps: List[KnowledgeGap] = Field(default_factory=list)
    targeted_teaching: Optional[TargetedTeaching] = None
    is_fast_path: bool = False
    grounded: bool = False
    generated_at: str = Field(default_factory=utc_now_iso)


class ComprehensionRunRecord(BaseModel):
    """Operational metadata only - raw student text and payloads strictly omitted."""
    run_id: str
    project_id: str
    changeset_id: str
    packet_id: str
    prompt_id: str
    attempt_number: int
    run_status: RunStatus
    overall_state: ComprehensionRating
    gap_count: int
    started_at: str
    created_at: str = Field(default_factory=utc_now_iso)
