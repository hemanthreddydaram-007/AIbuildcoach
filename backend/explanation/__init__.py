"""Milestone 6: Understand What Changed module."""

from backend.explanation.models import (
    IntentEpistemicStatus,
    ChangeCategory,
    DeterministicFileChange,
    WhatChangedSection,
    IntentRationale,
    WhySection,
    LocalEvidenceTrace,
    EvidenceSection,
    ConceptToUnderstand,
    CanIExplainThisPrompt,
    ChangeExplanationPreview,
    UnderstandChangeResult,
)
from backend.explanation.categorizer import DeterministicCategorizer
from backend.explanation.synthesizer import (
    prepare_change_explanation,
    explain_changes,
)

__all__ = [
    "IntentEpistemicStatus",
    "ChangeCategory",
    "DeterministicFileChange",
    "WhatChangedSection",
    "IntentRationale",
    "WhySection",
    "LocalEvidenceTrace",
    "EvidenceSection",
    "ConceptToUnderstand",
    "CanIExplainThisPrompt",
    "ChangeExplanationPreview",
    "UnderstandChangeResult",
    "DeterministicCategorizer",
    "prepare_change_explanation",
    "explain_changes",
]
