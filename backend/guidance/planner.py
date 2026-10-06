"""Action planner: converts detected knowledge gaps into concrete candidate next actions (Milestone 12.7)."""

from typing import List, Dict, Any, Optional
import hashlib
from backend.domain.models import utc_now_iso
from backend.guidance.models import (
    KnowledgeGap,
    GapCategory,
    NextAction,
    ActionType,
    ActionPriority,
    ActionStatus,
)


def _compute_action_id(project_id: str, action_type: str, seed: str) -> str:
    raw = f"{project_id}:{action_type}:{seed}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"act_{action_type.lower()}_{h}"


class NextActionPlanner:
    """Converts evidence-grounded KnowledgeGap instances into concrete human NextAction candidates.
    
    CRITICAL INVARIANTS:
    1. Every action has an objective, non-vague completion condition based on observable state.
    2. Zero autonomous execution: recommends actions for humans to perform.
    3. Every action references real gap_ids and valid evidence_ids.
    """

    def plan_actions(
        self,
        project_id: str,
        gaps: List[KnowledgeGap],
        incident_id: Optional[str] = None,
    ) -> List[NextAction]:
        """Maps each detected gap into one or more concrete action candidates."""
        actions: List[NextAction] = []

        for gap in gaps:
            # 1. UNRESOLVED_ERROR -> INVESTIGATE_ERROR
            if gap.category == GapCategory.UNRESOLVED_ERROR:
                err_type = gap.metadata.get("error_type", "runtime failure")
                location = gap.metadata.get("location") or {}
                file_target = location.get("file_path", "the affected file")
                
                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.INVESTIGATE_ERROR.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.INVESTIGATE_ERROR,
                        title=f"Diagnose recurring {err_type} in {file_target}",
                        description=f"Inspect the runtime stack trace and exception origin in {file_target}. Verify whether recent edits introduced a syntax error, broken import, or type mismatch.",
                        priority=ActionPriority.CRITICAL,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition=f"Observe a new execution, test run, or process start where error signature '{gap.metadata.get('error_signature', '')}' does not recur.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

            # 2. TEST_COVERAGE -> RUN_TEST
            elif gap.category == GapCategory.TEST_COVERAGE:
                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.RUN_TEST.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.RUN_TEST,
                        title="Run targeted test suite to verify fix",
                        description="Execute automated unit/integration tests covering the modified modules to confirm that the failure is genuinely resolved and not merely masked.",
                        priority=ActionPriority.HIGH,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition="Observe at least one TEST_FINISHED event with status=PASSED covering the affected module.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

            # 3. VERIFICATION -> CHECK_RUNTIME / RUN_COMMAND
            elif gap.category == GapCategory.VERIFICATION:
                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.CHECK_RUNTIME.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.CHECK_RUNTIME,
                        title="Verify runtime operational behavior",
                        description="Start the project application or execute an entrypoint command to confirm that the system runs cleanly without immediate crashes.",
                        priority=ActionPriority.HIGH,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition="Observe a successful COMMAND_FINISHED (exit code 0), HTTP_RESPONSE (2xx), or clean PROCESS_STARTED event.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

            # 4. CODE_CHANGE_REVIEW -> INSPECT_DIFF
            elif gap.category == GapCategory.CODE_CHANGE_REVIEW:
                changed_files = gap.metadata.get("changed_files", [])
                file_summary = ", ".join(changed_files[:2])
                if len(changed_files) > 2:
                    file_summary += f" and {len(changed_files) - 2} more"

                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.INSPECT_DIFF.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.INSPECT_DIFF,
                        title=f"Review code diff in {file_summary}",
                        description=f"Inspect the recent changes made to {file_summary}. Verify that only intended modifications were applied and no stray debug statements remain.",
                        priority=ActionPriority.MEDIUM,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition=f"Review confirmation recorded or subsequent clean verification observed after inspecting {file_summary}.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

            # 5. UNDERSTANDING -> EXPLAIN_BACK (M7 Integration)
            elif gap.category == GapCategory.UNDERSTANDING:
                concepts = gap.metadata.get("concepts", [])
                concept_text = concepts[0] if concepts else "recent architectural changes"

                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.EXPLAIN_BACK.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.EXPLAIN_BACK,
                        title="Explain architectural mechanism in your own words",
                        description=f"Perform a 'Can I Explain This?' comprehension exercise for: {concept_text[:140]}. Explain the Purpose, Mechanism, Failure Modes, and Downstream Impact.",
                        priority=ActionPriority.MEDIUM,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition="Complete an M7 comprehension evaluation achieving overall_state='UNDERSTOOD'.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

            # 6. DEPENDENCY -> READ_FILE / RUN_COMMAND
            elif gap.category == GapCategory.DEPENDENCY:
                actions.append(
                    NextAction(
                        action_id=_compute_action_id(project_id, ActionType.READ_FILE.value, gap.gap_id),
                        project_id=project_id,
                        incident_id=gap.incident_id or incident_id,
                        action_type=ActionType.READ_FILE,
                        title="Inspect dependency declarations and imports",
                        description="Check package manifest (e.g. requirements.txt, pyproject.toml, package.json) to verify that all referenced dependencies are explicitly installed in your active environment.",
                        priority=ActionPriority.HIGH if gap.priority == ActionPriority.HIGH else ActionPriority.MEDIUM,
                        gap_ids=[gap.gap_id],
                        evidence_ids=list(gap.evidence_ids),
                        completion_condition="Observe installation command or successful import without ModuleNotFoundError / ImportError.",
                        status=ActionStatus.PROPOSED,
                        metadata=gap.metadata,
                    )
                )

        return actions
