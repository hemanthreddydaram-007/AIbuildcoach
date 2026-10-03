"""Milestone 7: Can I Explain This? Active Comprehension Loop."""

from backend.comprehension.models import (
    ComprehensionRating,
    ComprehensionDimension,
    GapSeverity,
    RunStatus,
    KnowledgeGap,
    DimensionEvaluation,
    StudentExplanationSubmission,
    ReverificationPrompt,
    TargetedTeaching,
    ComprehensionEvaluationResult,
    ComprehensionRunRecord,
)
from backend.comprehension.exceptions import (
    ComprehensionError,
    ContextBindingMismatchError,
    InvalidAttemptProgressionError,
    ConcurrentAttemptError,
)
from backend.comprehension.evaluator import (
    evaluate_student_explanation,
    process_gateway_result,
    check_fast_path,
    escape_untrusted_text,
    build_evaluation_objective,
    validate_canonical_m6_binding,
)
from backend.comprehension.teaching import synthesize_targeted_teaching

__all__ = [
    "ComprehensionRating",
    "ComprehensionDimension",
    "GapSeverity",
    "RunStatus",
    "KnowledgeGap",
    "DimensionEvaluation",
    "StudentExplanationSubmission",
    "ReverificationPrompt",
    "TargetedTeaching",
    "ComprehensionEvaluationResult",
    "ComprehensionRunRecord",
    "ComprehensionError",
    "ContextBindingMismatchError",
    "InvalidAttemptProgressionError",
    "ConcurrentAttemptError",
    "evaluate_student_explanation",
    "process_gateway_result",
    "check_fast_path",
    "escape_untrusted_text",
    "build_evaluation_objective",
    "validate_canonical_m6_binding",
    "synthesize_targeted_teaching",
]
