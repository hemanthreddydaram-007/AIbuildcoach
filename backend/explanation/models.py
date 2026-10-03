"""Domain models for Milestone 6: Understand What Changed workflow."""

from enum import Enum
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class IntentEpistemicStatus(str, Enum):
    """Epistemic classification of architectural intent."""
    EXPLICIT = "EXPLICIT"  # Supported by code comments, docstrings, or commit messages
    INFERRED = "INFERRED"  # Deduced from code structure/diffs; no explicit author text
    UNKNOWN = "UNKNOWN"    # Inconclusive or absent evidence


class ChangeCategory(str, Enum):
    """Functional categories for changed files and changesets."""
    BUSINESS_LOGIC = "BUSINESS_LOGIC"
    SECURITY_AUTH = "SECURITY_AUTH"
    API_CONTRACT = "API_CONTRACT"
    DATA_STORAGE = "DATA_STORAGE"
    CONFIG_INFRA = "CONFIG_INFRA"
    TESTING = "TESTING"
    REFACTORING = "REFACTORING"
    DOCUMENTATION = "DOCUMENTATION"
    UNKNOWN = "UNKNOWN"


class DeterministicFileChange(BaseModel):
    """File change record anchored strictly in deterministic M3 physical truth."""
    file_path: str
    old_path: Optional[str] = None
    change_type: str                   # ADDED, MODIFIED, DELETED, RENAMED (from M3)
    is_staged: bool                    # from M3
    is_untracked: bool                 # from M3
    lines_added: int                   # from M3 hunk calculations
    lines_removed: int                 # from M3 hunk calculations
    category: ChangeCategory           # Deterministic path heuristic
    is_critical: bool                  # True when any ContextItem for this file has relevance_score >= 90.0
    supplementary_summary: Optional[str] = None  # Optional model-provided descriptive text (never physical truth)


class WhatChangedSection(BaseModel):
    """Section 1: What Changed? 100% deterministic physical truth from M3."""
    total_files_changed: int           # from M3 ChangeSet
    total_lines_added: int             # from M3 ChangeSet hunks
    total_lines_removed: int           # from M3 ChangeSet hunks
    files: List[DeterministicFileChange] # 100% M3 truth
    primary_category: ChangeCategory   # Aggregated deterministic category
    supplementary_ai_narrative: str    # Supplementary AI narrative (MUST NOT determine physical truth)


class IntentRationale(BaseModel):
    """Epistemically qualified statement explaining change rationale."""
    statement: str
    status: IntentEpistemicStatus      # EXPLICIT, INFERRED, or UNKNOWN
    evidence_source: Optional[str] = None  # e.g., "docstring in auth.py:12" or "inferred from dependencies"
    supporting_refs: List[str] = Field(default_factory=list)


class WhySection(BaseModel):
    """Section 2: Why? Epistemically explicit architectural rationale."""
    primary_intent: IntentRationale
    inferences: List[IntentRationale]  # Explicitly flagged as INFERRED
    unknown_aspects: List[str]         # Explicit gaps where rationale cannot be proven from evidence


class LocalEvidenceTrace(BaseModel):
    """Evidence trace item where snippets are strictly resolved from local ContextItem content."""
    claim_statement: str
    claim_type: str                    # OBSERVATION, INFERENCE, UNKNOWN
    grounded: bool                     # Validated by M5 EvidenceValidator
    item_id: str                       # Canonical ContextItem.item_id
    source_type: str                   # DIFF, FILE, PROJECT_GRAPH, EVIDENCE
    file_path: Optional[str]
    line_start: Optional[int]
    line_end: Optional[int]
    verbatim_snippet: str              # Extracted directly from local ContextItem.content (never LLM text)
    validation_notes: Optional[str]


class EvidenceSection(BaseModel):
    """Section 3: Evidence & Provenance."""
    grounded_traces: List[LocalEvidenceTrace]
    ungrounded_or_unknown: List[LocalEvidenceTrace]
    grounding_ratio: float
    total_claims: int


class ConceptToUnderstand(BaseModel):
    """Section 4: What Should I Understand? Evidence-supported architectural concepts."""
    concept_name: str                  # e.g., "Stateless Bearer Authentication"
    why_it_matters: str                # Educational rationale grounded in the change
    file_references: List[str]         # Files directly instantiating this concept
    downstream_impacts: List[str]      # Derived from M2 ProjectGraph edges
    potential_failure_modes: List[str] # Edge cases derived from changed hunks/types
    supporting_item_ids: List[str]     # ContextItem.item_id links proving grounding


class CanIExplainThisPrompt(BaseModel):
    """Section 5: Can I Explain This? Prompt metadata only (zero evaluation/scoring in M6)."""
    prompt_id: str
    question: str                      # Targeted conceptual question
    target_concepts: List[str]         # Concepts being tested
    expected_aspects: List[str]        # ["Purpose", "Mechanism", "Failure Modes", "Downstream Impact"]
    target_files: List[str]            # Key files the developer should reference
    is_available: bool = True          # False when in degraded provider-failure fallback mode


class ChangeExplanationPreview(BaseModel):
    """Human-inspectable preview prepared prior to requesting consent."""
    preview_id: str
    project_id: str
    changeset_id: str
    packet_id: str
    packet_hash: str
    clean_working_tree: bool
    total_files_changed: int
    changed_files: List[str]
    token_estimate: int
    redaction_summary: Dict[str, Any]
    preview_generated_at: str = Field(default_factory=utc_now_iso)


class UnderstandChangeResult(BaseModel):
    """Top-level result model for Workflow 1: Understand What Changed."""
    id: str                            # uc_<uuid>
    project_id: str
    changeset_id: str
    packet_id: str
    gateway_run_id: Optional[str] = None
    generated_at: str = Field(default_factory=utc_now_iso)
    clean_working_tree: bool
    what_changed: WhatChangedSection
    why: WhySection
    evidence: EvidenceSection
    what_should_i_understand: List[ConceptToUnderstand]
    can_i_explain_this: CanIExplainThisPrompt
    unresolved_questions: List[str] = Field(default_factory=list)
