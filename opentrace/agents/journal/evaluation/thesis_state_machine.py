"""
Thesis State Machine — lifecycle phase tracking for trade theses.

Phases represent where a thesis sits in its lifecycle:

    PENDING ──→ WATCHING ──→ NEAR_TRIGGER ──→ TRIGGERED ──→ ACTIVE ──→ CLOSED
                   │              │                            │
                   └──────────────┴──→ INVALIDATED ────────────┘
                                            │
                                            └──→ WATCHING (new thesis spawned)

The state machine determines which evaluation path to take:
  - PENDING:       skip evaluation entirely
  - WATCHING:      rule-based only (cheap)
  - NEAR_TRIGGER:  invoke LLM evaluator (justified cost)
  - TRIGGERED:     order pending execution
  - ACTIVE:        position monitoring mode
  - INVALIDATED:   check for flip-thesis
  - CLOSED:        no further evaluation
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from opentrace.agents.journal.evaluation.condition_tracker import ConditionStateStore

logger = logging.getLogger(__name__)


class ThesisPhase(str, Enum):
    PENDING = "pending"
    WATCHING = "watching"
    NEAR_TRIGGER = "near_trigger"
    TRIGGERED = "triggered"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    CLOSED = "closed"

    @classmethod
    def from_str(cls, value: str) -> "ThesisPhase":
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.PENDING


# Valid phase transitions
_VALID_TRANSITIONS: Dict[ThesisPhase, Set[ThesisPhase]] = {
    ThesisPhase.PENDING:      {ThesisPhase.WATCHING, ThesisPhase.CLOSED},
    ThesisPhase.WATCHING:     {ThesisPhase.NEAR_TRIGGER, ThesisPhase.TRIGGERED, ThesisPhase.INVALIDATED, ThesisPhase.CLOSED},
    ThesisPhase.NEAR_TRIGGER: {ThesisPhase.WATCHING, ThesisPhase.TRIGGERED, ThesisPhase.INVALIDATED, ThesisPhase.CLOSED},
    ThesisPhase.TRIGGERED:    {ThesisPhase.ACTIVE, ThesisPhase.WATCHING, ThesisPhase.CLOSED},
    ThesisPhase.ACTIVE:       {ThesisPhase.CLOSED, ThesisPhase.INVALIDATED},
    ThesisPhase.INVALIDATED:  {ThesisPhase.WATCHING, ThesisPhase.CLOSED},
    ThesisPhase.CLOSED:       set(),
}

# Phases where evaluation should run
EVAL_PHASES = {ThesisPhase.WATCHING, ThesisPhase.NEAR_TRIGGER, ThesisPhase.ACTIVE}

# Phases where LLM evaluation is justified
LLM_PHASES = {ThesisPhase.NEAR_TRIGGER}


class ThesisStateMachine:
    """
    Manages phase transitions for a single thesis.

    State is persisted via the shared ConditionStateStore (piggybacked
    on the condition_state table using a `phase::{thesis_id}` key pattern
    stored inside the thesis's state dict).
    """

    PHASE_KEY = "_phase"
    HISTORY_KEY = "_phase_history"

    def __init__(
        self,
        thesis_id: str,
        state_store: ConditionStateStore,
        initial_phase: ThesisPhase = ThesisPhase.PENDING,
    ):
        self.thesis_id = thesis_id
        self._store = state_store
        self._state = state_store.load(thesis_id)

        if self.PHASE_KEY not in self._state:
            self._state[self.PHASE_KEY] = initial_phase.value
            self._persist()

    @property
    def phase(self) -> ThesisPhase:
        return ThesisPhase.from_str(self._state.get(self.PHASE_KEY, "pending"))

    @property
    def should_evaluate(self) -> bool:
        return self.phase in EVAL_PHASES

    @property
    def should_use_llm(self) -> bool:
        return self.phase in LLM_PHASES

    def transition(self, new_phase: ThesisPhase, reason: str = "") -> bool:
        """
        Attempt a phase transition. Returns True if successful.
        Logs the transition to both the state dict and the phase_log table.
        """
        old = self.phase
        if new_phase == old:
            return True  # no-op

        if new_phase not in _VALID_TRANSITIONS.get(old, set()):
            logger.warning(
                "Invalid phase transition %s → %s for thesis %s (reason: %s)",
                old.value, new_phase.value, self.thesis_id, reason,
            )
            return False

        self._state[self.PHASE_KEY] = new_phase.value

        # Append to in-state history (compact)
        history: List[Dict[str, str]] = self._state.get(self.HISTORY_KEY, [])
        history.append({
            "from": old.value,
            "to": new_phase.value,
            "reason": reason[:200],
        })
        self._state[self.HISTORY_KEY] = history[-20:]  # cap

        self._persist()
        self._store.log_phase_transition(self.thesis_id, old.value, new_phase.value, reason)

        logger.info(
            "Thesis %s: %s → %s (%s)", self.thesis_id, old.value, new_phase.value, reason
        )
        return True

    def auto_advance(
        self,
        *,
        in_schedule_window: bool,
        has_position: bool,
        max_proximity: float = 0.0,
        proximity_threshold: float = 0.6,
    ) -> ThesisPhase:
        """
        Automatically advance phase based on current conditions.
        Returns the (possibly new) phase.
        """
        current = self.phase

        if current == ThesisPhase.PENDING and in_schedule_window:
            self.transition(ThesisPhase.WATCHING, "entered_schedule_window")
            current = self.phase

        if current == ThesisPhase.WATCHING:
            if has_position:
                self.transition(ThesisPhase.ACTIVE, "position_detected")
            elif max_proximity >= proximity_threshold:
                self.transition(ThesisPhase.NEAR_TRIGGER, f"proximity={max_proximity:.2f}")

        if current == ThesisPhase.NEAR_TRIGGER:
            if has_position:
                self.transition(ThesisPhase.ACTIVE, "position_detected")
            elif max_proximity < proximity_threshold * 0.7:
                # Hysteresis: require a meaningful drop before going back to WATCHING
                self.transition(ThesisPhase.WATCHING, f"proximity_dropped={max_proximity:.2f}")

        if current == ThesisPhase.TRIGGERED and has_position:
            self.transition(ThesisPhase.ACTIVE, "order_filled")

        return self.phase

    def get_history(self) -> List[Dict[str, str]]:
        return list(self._state.get(self.HISTORY_KEY, []))

    def _persist(self):
        self._store.save(self.thesis_id, self._state)
