"""Viva Defence Reporter for Milestone 8: Viva Defence Engine.
Compiles comprehensive defence readiness reports distinguishing NOT_EVALUATED from UNKNOWN.
"""

import json
import uuid
from typing import List, Dict, Optional, Any

from backend.domain.models import utc_now_iso
from backend.project_model.db import Database
from backend.viva.models import (
    VivaCategory,
    VivaRating,
    VivaDefenceReadiness,
    VivaSessionMode,
    CategoryMastery,
    VivaDefenceReport,
    VivaKnowledgeGap,
    VivaGapSeverity,
)
from backend.viva.exceptions import VivaSessionNotFoundError
from backend.viva.index import ProjectArchitecturalIndex


def _aggregate_category_rating(turns: List[Dict[str, Any]]) -> VivaRating:
    """Computes aggregate category rating deterministically from turn records."""
    if not turns:
        return VivaRating.NOT_EVALUATED

    ratings = [t["rating"] for t in turns]
    if all(r == "UNKNOWN" for r in ratings):
        return VivaRating.UNKNOWN

    scores = {
        "STRONG": 3.0,
        "ADEQUATE": 2.0,
        "PARTIAL": 1.0,
        "WEAK": 0.0,
        "UNKNOWN": 0.0,
    }
    vals = [scores.get(r, 0.0) for r in ratings]
    avg = sum(vals) / len(vals)

    if avg >= 2.5:
        return VivaRating.STRONG
    elif avg >= 1.7:
        return VivaRating.ADEQUATE
    elif avg >= 0.8:
        return VivaRating.PARTIAL
    else:
        return VivaRating.WEAK


def compile_viva_report(
    db: Database,
    session_id: str,
    index: Optional[ProjectArchitecturalIndex] = None,
) -> VivaDefenceReport:
    """Compiles and persists a fully reconstructable viva defence report."""
    session_data = db.get_viva_session(session_id)
    if not session_data:
        raise VivaSessionNotFoundError(f"Viva session {session_id} not found.")

    turns = db.get_viva_turns_for_session(session_id)
    questions = db.get_viva_questions_for_session(session_id)

    target_category_names = session_data.get("target_categories", [])
    target_categories = [VivaCategory(c) for c in target_category_names]
    mode = VivaSessionMode(session_data["mode"])

    # Map questions and turns by turn_index
    questions_by_turn = {q["turn_index"]: q for q in questions}
    turns_by_cat: Dict[VivaCategory, List[Dict[str, Any]]] = {cat: [] for cat in VivaCategory}

    for turn in turns:
        try:
            cat = VivaCategory(turn["category"])
            turns_by_cat[cat].append(turn)
        except ValueError:
            pass

    # Build CategoryMastery for every category, strictly distinguishing NOT_EVALUATED from UNKNOWN
    category_masteries: Dict[VivaCategory, CategoryMastery] = {}
    verified_strengths: List[str] = []
    recommended_study_files: List[str] = []
    critical_gaps: List[VivaKnowledgeGap] = []

    for cat in VivaCategory:
        cat_turns = turns_by_cat[cat]
        rating = _aggregate_category_rating(cat_turns)

        # Collect evidence references and study files
        cat_files: List[str] = []
        if index:
            cat_files = index.get_category_files(cat)
        else:
            for t in cat_turns:
                q = questions_by_turn.get(t["turn_index"])
                if q:
                    cat_files.extend(q.get("target_files", []))

        # Remove duplicates preserving order
        unique_files = list(dict.fromkeys(cat_files))

        if rating in (VivaRating.STRONG, VivaRating.ADEQUATE):
            verified_strengths.append(f"Demonstrated solid mastery in {cat.value}")
        elif rating in (VivaRating.WEAK, VivaRating.PARTIAL):
            for f in unique_files:
                if f not in recommended_study_files:
                    recommended_study_files.append(f)

        # Gaps
        cat_gaps: List[VivaKnowledgeGap] = []
        for t in cat_turns:
            if t["gap_count"] > 0:
                q = questions_by_turn.get(t["turn_index"])
                expected = ", ".join(q.get("expected_concepts", [])) if q else "Core architectural principles"
                gap = VivaKnowledgeGap(
                    gap_id=f"gap_rep_{uuid.uuid4().hex[:8]}",
                    category=cat,
                    severity=VivaGapSeverity.CRITICAL if t["rating"] == "WEAK" else VivaGapSeverity.MODERATE,
                    summary=f"Weakness in {cat.value} ({t['rating']})",
                    expected_understanding=expected,
                    student_misconception=None,
                    evidence_references=q.get("supporting_evidence_ids", []) if q else [],
                    grounded=True,
                )
                cat_gaps.append(gap)
                if gap.severity == VivaGapSeverity.CRITICAL:
                    critical_gaps.append(gap)

        category_masteries[cat] = CategoryMastery(
            category=cat,
            rating=rating,
            questions_evaluated=len(cat_turns),
            grounded_evidence_ids=[],
            gaps=cat_gaps,
        )

    evaluated_categories = [c for c in target_categories if category_masteries[c].rating != VivaRating.NOT_EVALUATED]
    unevaluated_target_categories = [c for c in target_categories if category_masteries[c].rating == VivaRating.NOT_EVALUATED]

    # Deterministic Readiness Assessment
    readiness: VivaDefenceReadiness
    if len(unevaluated_target_categories) > 0:
        readiness = VivaDefenceReadiness.INCOMPLETE
        summary = (
            f"Viva defence session ended before all target categories were evaluated. "
            f"Evaluated {len(evaluated_categories)}/{len(target_categories)} categories."
        )
    else:
        weak_cats = [c for c in target_categories if category_masteries[c].rating == VivaRating.WEAK]
        partial_cats = [c for c in target_categories if category_masteries[c].rating == VivaRating.PARTIAL]
        unknown_cats = [c for c in target_categories if category_masteries[c].rating == VivaRating.UNKNOWN]
        strong_or_adeq = [c for c in target_categories if category_masteries[c].rating in (VivaRating.STRONG, VivaRating.ADEQUATE)]

        if len(weak_cats) > 0 or len(critical_gaps) > 0:
            readiness = VivaDefenceReadiness.SUBSTANTIAL_GAPS
            summary = (
                f"Candidate exhibited substantial knowledge gaps across {len(weak_cats)} categories "
                f"with {len(critical_gaps)} critical gaps identified."
            )
        elif len(unknown_cats) > 0 or len(partial_cats) > 0:
            readiness = VivaDefenceReadiness.NEEDS_PREPARATION
            affected = [c.value for c in unknown_cats + partial_cats]
            summary = (
                f"Candidate demonstrated partial or unverified understanding, needing targeted preparation in: "
                f"{', '.join(affected)}."
            )
        elif len(strong_or_adeq) == len(target_categories):
            readiness = VivaDefenceReadiness.DEFENCE_READY
            summary = (
                f"Candidate demonstrated robust, project-grounded comprehension across all {len(target_categories)} "
                f"target categories. Fully prepared for oral defence."
            )
        else:
            readiness = VivaDefenceReadiness.NEEDS_PREPARATION
            summary = (
                f"Candidate demonstrated partial preparation across target categories."
            )

    report_id = f"vr_{uuid.uuid4().hex[:12]}"
    created_at = utc_now_iso()

    report = VivaDefenceReport(
        report_id=report_id,
        session_id=session_id,
        project_id=session_data["project_id"],
        session_mode=mode,
        total_turns=len(turns),
        readiness=readiness,
        summary=summary,
        category_masteries=category_masteries,
        evaluated_categories_count=len(evaluated_categories),
        total_categories_count=len(target_categories),
        verified_strengths=verified_strengths,
        critical_gaps=critical_gaps,
        recommended_study_files=recommended_study_files[:10],
        generated_at=created_at,
    )

    # Persist report for full reconstructability
    cat_masteries_json = json.dumps({
        cat.value: {
            "rating": cm.rating.value,
            "questions_evaluated": cm.questions_evaluated,
            "gaps_count": len(cm.gaps),
        }
        for cat, cm in category_masteries.items()
    })
    strengths_json = json.dumps(verified_strengths)
    gaps_json = json.dumps([g.model_dump() for g in critical_gaps])
    study_files_json = json.dumps(report.recommended_study_files)

    db.save_viva_report(
        report_id=report.report_id,
        session_id=report.session_id,
        project_id=report.project_id,
        mode=report.session_mode.value,
        total_turns=report.total_turns,
        readiness=report.readiness.value,
        summary=report.summary,
        category_masteries_json=cat_masteries_json,
        strengths_json=strengths_json,
        gaps_json=gaps_json,
        study_files_json=study_files_json,
        created_at=report.generated_at,
    )

    return report
