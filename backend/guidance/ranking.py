"""Deterministic ranking and filtering engine for candidate next actions (Milestone 12.7)."""

from typing import List, Tuple, Optional
from backend.guidance.models import (
    NextAction,
    ActionPriority,
    ActionStatus,
    ActionType,
)

PRIORITY_WEIGHTS = {
    ActionPriority.CRITICAL: 400,
    ActionPriority.HIGH: 300,
    ActionPriority.MEDIUM: 200,
    ActionPriority.LOW: 100,
}

# Type tie-breaker preference when priorities are identical
TYPE_PREFERENCE = {
    ActionType.INVESTIGATE_ERROR: 50,
    ActionType.RUN_TEST: 40,
    ActionType.CHECK_RUNTIME: 30,
    ActionType.EXPLAIN_BACK: 25,
    ActionType.READ_FILE: 20,
    ActionType.INSPECT_DIFF: 15,
    ActionType.REVIEW_EVIDENCE: 10,
    ActionType.COMPARE_CHANGES: 8,
    ActionType.RUN_COMMAND: 5,
    ActionType.DOCUMENT_DECISION: 1,
}


class ActionRanker:
    """Ranks and isolates the TOP_NEXT_ACTION and small secondary actions set.
    
    CRITICAL INVARIANTS:
    1. Zero randomness: deterministic output for identical inputs.
    2. Does not overwhelm user: exposes top next action and at most 2 secondary actions.
    3. Respects action lifecycle status (ignores COMPLETED, SKIPPED).
    """

    def rank_actions(
        self,
        candidate_actions: List[NextAction],
        max_secondary: int = 2,
    ) -> Tuple[Optional[NextAction], List[NextAction]]:
        """Sorts candidates deterministically and returns (top_next_action, secondary_actions)."""
        # Filter for actionable items (PROPOSED or STARTED)
        active_actions = [
            a for a in candidate_actions
            if a.status in (ActionStatus.PROPOSED, ActionStatus.STARTED)
        ]

        if not active_actions:
            return None, []

        def sort_key(action: NextAction) -> Tuple[int, int, str]:
            # Priority weight descending
            p_weight = PRIORITY_WEIGHTS.get(action.priority, 0)
            # Type preference descending
            t_pref = TYPE_PREFERENCE.get(action.action_type, 0)
            # Action ID ascending for stable deterministic tie-breaker
            return (-p_weight, -t_pref, action.action_id)

        sorted_actions = sorted(active_actions, key=sort_key)

        top_action = sorted_actions[0]
        secondary = sorted_actions[1:1 + max_secondary]

        return top_action, secondary
