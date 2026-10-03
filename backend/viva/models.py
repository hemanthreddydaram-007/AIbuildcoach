"""Domain models for Milestone 8: Viva Defence Engine."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class VivaDifficulty(str, Enum):
    """Discrete 4-tier difficulty levels."""
    EASY = "EASY"       # Component identification & direct responsibilities
    MEDIUM = "MEDIUM"   # Architectural purpose & component interactions
    HARD = "HARD"       # Edge cases, failure modes, boundary defenses
    DEEP = "DEEP"       # Design trade-offs, security model, invariants


class VivaCategory(str, Enum):
    """Project-grounded architectural categories."""
    PROJECT_PURPOSE = "PROJECT_PURPOSE"
    ARCHITECTURE_OVERVIEW = "ARCHITECTURE_OVERVIEW"
    DATA_FLOW = "DATA_FLOW"
    API_CONTRACTS = "API_CONTRACTS"
    STORAGE_PERSISTENCE = "STORAGE_PERSISTENCE"
    SECURITY_AUTH = "SECURITY_AUTH"
    DEPENDENCIES = "DEPENDENCIES"
    FAILURE_MODES = "FAILURE_MODES"
    TESTING_VERIFICATION = "TESTING_VERIFICATION"


class VivaRating(str, Enum):
    """Evaluation ratings. NOT_EVALUATED is strictly distinct from UNKNOWN."""
    STRONG = "STRONG"
    ADEQUATE = "ADEQUATE"
    PARTIAL = "PARTIAL"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"              # Evaluated, but evidence/answer indeterminate
    NOT_EVALUATED = "NOT_EVALUATED"  # Never queried during this session


class VivaGapSeverity(str, Enum):
    """M8-owned knowledge gap severity."""
    CRITICAL = "CRITICAL"  # Fundamental flaw in security, data integrity, or core flow
    MODERATE = "MODERATE"  # Missed error handling, boundary condition, or interaction
    MINOR = "MINOR"        # Secondary nuance or suboptimal rationale


class VivaDefenceReadiness(str, Enum):
    """Overall defence readiness. DEFENCE_READY requires complete evaluation."""
    DEFENCE_READY = "DEFENCE_READY"
    NEEDS_PREPARATION = "NEEDS_PREPARATION"
    SUBSTANTIAL_GAPS = "SUBSTANTIAL_GAPS"
    INCOMPLETE = "INCOMPLETE"  # Required categories were NOT_EVALUATED


class VivaSessionStatus(str, Enum):
    """Lifecycle status of a viva examination session."""
    ACTIVE = "ACTIVE"
    AWAITING_ANSWER = "AWAITING_ANSWER"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class VivaSessionMode(str, Enum):
    """Mode of the viva defence session."""
    PROJECT_WIDE = "PROJECT_WIDE"      # Covers all required categories
    CATEGORY_FOCUS = "CATEGORY_FOCUS"  # Focused drill on specific categories


class VivaQuestion(BaseModel):
    """A project-specific viva question, persisted for crash/restart recovery."""
    question_id: str
    session_id: str
    turn_index: int
    category: VivaCategory
    difficulty: VivaDifficulty
    question_text: str
    target_modules: List[str] = Field(default_factory=list)
    target_files: List[str] = Field(default_factory=list)
    expected_concepts: List[str] = Field(default_factory=list)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    is_follow_up: bool = False
    parent_question_id: Optional[str] = None
    packet_id: str
    created_at: str = Field(default_factory=utc_now_iso)


class VivaAnswerSubmission(BaseModel):
    """Transient in-memory answer submission. NEVER persisted to SQLite."""
    submission_id: str
    session_id: str
    question_id: str
    turn_index: int
    answer_text: str  # Kept in-memory only


class VivaKnowledgeGap(BaseModel):
    """Knowledge gap grounded in project evidence."""
    gap_id: str
    category: VivaCategory
    severity: VivaGapSeverity
    summary: str
    expected_understanding: str
    student_misconception: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    grounded: bool = False


class VivaTurnEvaluation(BaseModel):
    """Evaluation result for a single viva interview turn."""
    evaluation_id: str
    session_id: str
    question_id: str
    turn_index: int
    category: VivaCategory
    difficulty: VivaDifficulty
    rating: VivaRating
    feedback: str
    is_project_grounded: bool = True  # False if answer is detached textbook trivia
    gaps: List[VivaKnowledgeGap] = Field(default_factory=list)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    follow_up_question: Optional[VivaQuestion] = None
    is_fast_path: bool = False
    grounded: bool = False
    evaluated_at: str = Field(default_factory=utc_now_iso)


class CategoryMastery(BaseModel):
    """Category-level mastery. Distinguishes NOT_EVALUATED from UNKNOWN."""
    category: VivaCategory
    rating: VivaRating = VivaRating.NOT_EVALUATED
    questions_evaluated: int = 0
    grounded_evidence_ids: List[str] = Field(default_factory=list)
    gaps: List[VivaKnowledgeGap] = Field(default_factory=list)


class VivaDefenceReport(BaseModel):
    """Final comprehensive report summarizing viva readiness."""
    report_id: str
    session_id: str
    project_id: str
    session_mode: VivaSessionMode
    total_turns: int
    readiness: VivaDefenceReadiness
    summary: str
    category_masteries: Dict[VivaCategory, CategoryMastery]
    evaluated_categories_count: int
    total_categories_count: int
    verified_strengths: List[str] = Field(default_factory=list)
    critical_gaps: List[VivaKnowledgeGap] = Field(default_factory=list)
    recommended_study_files: List[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)


class VivaSessionRecord(BaseModel):
    """Operational session record in SQLite (zero raw answer text)."""
    session_id: str
    project_id: str
    status: VivaSessionStatus
    mode: VivaSessionMode
    current_turn: int
    base_questions_asked: int
    followups_asked: int
    current_difficulty: VivaDifficulty
    target_categories: List[VivaCategory]
    evaluated_categories: List[VivaCategory] = Field(default_factory=list)
    started_at: str
    updated_at: str
    completed_at: Optional[str] = None
