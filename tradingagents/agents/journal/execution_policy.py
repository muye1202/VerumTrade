from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from tradingagents.agents.journal.models import (
    ActionDecisionType,
    ActionReasonCode,
    JournalActionDecision,
    ThesisStatus,
)


@dataclass
class PolicyResult:
    allowed: bool
    dry_run: bool
    block_reasons: List[str]


class JournalExecutionPolicy:
    """Hard execution guardrails for journal-generated actions."""

    def __init__(
        self,
        *,
        execution_enabled: bool = False,
        dry_run: bool = True,
        min_confidence: float = 70.0,
        cooldown_minutes: float = 30.0,
        max_actions_per_day: int = 5,
        allow_profit_take: bool = True,
        allow_defensive_exit: bool = True,
    ):
        self.execution_enabled = bool(execution_enabled)
        self.dry_run = bool(dry_run)
        self.min_confidence = float(min_confidence)
        self.cooldown_minutes = float(cooldown_minutes)
        self.max_actions_per_day = int(max_actions_per_day)
        self.allow_profit_take = bool(allow_profit_take)
        self.allow_defensive_exit = bool(allow_defensive_exit)

    @classmethod
    def from_env(cls) -> "JournalExecutionPolicy":
        return cls(
            execution_enabled=_as_bool(os.getenv("JOURNAL_EXECUTION_ENABLED", "false"), False),
            dry_run=_as_bool(os.getenv("JOURNAL_EXECUTION_DRY_RUN", "true"), True),
            min_confidence=_as_float(os.getenv("JOURNAL_EXECUTION_MIN_CONFIDENCE"), 70.0),
            cooldown_minutes=_as_float(os.getenv("JOURNAL_EXECUTION_COOLDOWN_MINUTES"), 30.0),
            max_actions_per_day=_as_int(os.getenv("JOURNAL_EXECUTION_MAX_ACTIONS_PER_DAY"), 5),
            allow_profit_take=_as_bool(os.getenv("JOURNAL_EXECUTION_ALLOW_PROFIT_TAKE", "true"), True),
            allow_defensive_exit=_as_bool(os.getenv("JOURNAL_EXECUTION_ALLOW_DEFENSIVE_EXIT", "true"), True),
        )

    def evaluate(
        self,
        *,
        thesis: Any,
        decision: JournalActionDecision,
        store: Any,
        position_qty: Optional[float],
        market_session: str,
        thesis_execution_eligible: bool = True,
        ineligibility_reason: Optional[str] = None,
    ) -> PolicyResult:
        reasons: List[str] = []

        if str(getattr(thesis, "status", "")) != ThesisStatus.ACTIVE.value:
            reasons.append("inactive_thesis")

        if decision.decision_type == ActionDecisionType.NO_ACTION.value:
            reasons.append("no_action")

        if not thesis_execution_eligible:
            reasons.append(str(ineligibility_reason or "thesis_ineligible_for_execution"))

        if float(decision.confidence or 0.0) < self.min_confidence:
            reasons.append("below_min_confidence")

        if not self._action_type_allowed(decision):
            reasons.append("action_type_disabled")

        if not self.execution_enabled:
            reasons.append("execution_disabled")

        if self.max_actions_per_day >= 0:
            count = int(store.count_action_executions_for_day(datetime.utcnow().date()))
            if count >= self.max_actions_per_day:
                reasons.append("daily_limit_reached")

        reason_enum = _safe_reason_code(decision.reason_code)
        if reason_enum and self.cooldown_minutes > 0:
            if store.has_recent_action_decision(
                thesis_id=decision.thesis_id,
                reason_code=reason_enum,
                within_minutes=self.cooldown_minutes,
            ):
                reasons.append("cooldown_active")

        qty = float(position_qty or 0.0)
        if decision.decision_type in {
            ActionDecisionType.EXIT_POSITION.value,
            ActionDecisionType.REDUCE_POSITION.value,
            ActionDecisionType.TAKE_PROFIT_PARTIAL.value,
            ActionDecisionType.TAKE_PROFIT_FULL.value,
        }:
            if qty <= 0:
                reasons.append("no_position_qty")

        allowed_sessions = {"market_hours", "premarket"}
        if market_session not in allowed_sessions:
            reasons.append("session_not_allowed")

        return PolicyResult(
            allowed=len(reasons) == 0,
            dry_run=self.dry_run,
            block_reasons=reasons,
        )

    def _action_type_allowed(self, decision: JournalActionDecision) -> bool:
        d = decision.decision_type
        if d in {
            ActionDecisionType.TAKE_PROFIT_PARTIAL.value,
            ActionDecisionType.TAKE_PROFIT_FULL.value,
        }:
            return self.allow_profit_take
        if d in {
            ActionDecisionType.EXIT_POSITION.value,
            ActionDecisionType.REDUCE_POSITION.value,
        }:
            return self.allow_defensive_exit
        return True



def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(raw: Any, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw: Any, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _safe_reason_code(value: str) -> Optional[ActionReasonCode]:
    try:
        return ActionReasonCode(str(value))
    except Exception:
        return None
