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
    PositionSnapshot,
    TradeThesis,
)


@dataclass
class ActionContext:
    thesis: TradeThesis
    snapshot: PositionSnapshot
    market_session: str
    recent_alerts: List[Dict[str, Any]]
    ticker_lessons: List[Dict[str, Any]]
    semantic_lessons: List[Dict[str, Any]]
    memory_unavailable: bool = False


class JournalExecutionAdvisor:
    """Rules-first decision engine for journal execution actions."""

    def __init__(
        self,
        min_confidence: float = 70.0,
        partial_take_profit_pct: float = 0.5,
    ):
        self.min_confidence = float(min_confidence)
        self.partial_take_profit_pct = float(partial_take_profit_pct)

    @classmethod
    def from_env(cls) -> "JournalExecutionAdvisor":
        return cls(
            min_confidence=_safe_float(os.getenv("JOURNAL_EXECUTION_MIN_CONFIDENCE"), 70.0),
            partial_take_profit_pct=_safe_float(
                os.getenv("JOURNAL_EXECUTION_PARTIAL_TAKE_PROFIT_PCT"),
                0.5,
            ),
        )

    def evaluate(
        self,
        *,
        context: ActionContext,
        cooldown_hit: bool = False,
    ) -> JournalActionDecision:
        decision_type, reason_code = self._decide_action(context)
        confidence = self._score_confidence(reason_code)

        if cooldown_hit:
            confidence = max(0.0, confidence - 20.0)

        confidence += self._memory_bonus(context=context, reason_code=reason_code)
        confidence = float(min(95.0, max(0.0, confidence)))

        qty_pct = self._recommended_qty_pct(decision_type)

        context_summary = {
            "market_session": context.market_session,
            "memory_unavailable": context.memory_unavailable,
            "recent_alert_types": [str(a.get("alert_type", "")) for a in context.recent_alerts[:5]],
            "ticker_lessons": len(context.ticker_lessons),
            "semantic_lessons": len(context.semantic_lessons),
            "snapshot": {
                "current_price": context.snapshot.current_price,
                "unrealized_pl_pct": context.snapshot.unrealized_pl_pct,
                "relative_strength": context.snapshot.relative_strength,
                "distance_to_stop_pct": context.snapshot.distance_to_stop_pct,
                "distance_to_target1_pct": context.snapshot.distance_to_target1_pct,
            },
        }

        linked_alert_ids = [str(a.get("id", "")) for a in context.recent_alerts if a.get("id")][:10]

        return JournalActionDecision(
            thesis_id=context.thesis.id,
            ticker=context.thesis.ticker.upper(),
            tick_timestamp=context.snapshot.timestamp,
            decision_type=decision_type.value,
            reason_code=reason_code.value,
            confidence=confidence,
            recommended_qty_pct=qty_pct,
            dry_run=True,
            gates_passed=False,
            gate_block_reasons=json.dumps([]),
            context_summary=json.dumps(context_summary),
            linked_alert_ids=json.dumps(linked_alert_ids),
            created_at=datetime.utcnow().isoformat(),
        )

    def is_actionable(self, decision: JournalActionDecision) -> bool:
        if decision.decision_type == ActionDecisionType.NO_ACTION.value:
            return False
        return float(decision.confidence or 0.0) >= self.min_confidence

    def _decide_action(self, context: ActionContext) -> tuple[ActionDecisionType, ActionReasonCode]:
        thesis = context.thesis
        snap = context.snapshot
        price = _safe_float(snap.current_price)

        if price is None:
            return ActionDecisionType.NO_ACTION, ActionReasonCode.NONE

        if _stop_breached(thesis=thesis, price=price):
            return ActionDecisionType.EXIT_POSITION, ActionReasonCode.STOP_BREACH

        if _time_stop_expired(thesis):
            return ActionDecisionType.EXIT_POSITION, ActionReasonCode.TIME_STOP_EXPIRED

        if _target2_reached(thesis=thesis, price=price):
            return ActionDecisionType.TAKE_PROFIT_FULL, ActionReasonCode.TARGET2_REACHED

        if _target1_reached(thesis=thesis, price=price):
            return ActionDecisionType.TAKE_PROFIT_PARTIAL, ActionReasonCode.TARGET1_REACHED

        if _defensive_reduction_needed(context=context):
            if _is_gap_adverse(context):
                return ActionDecisionType.REDUCE_POSITION, ActionReasonCode.GAP_ADVERSE
            return ActionDecisionType.REDUCE_POSITION, ActionReasonCode.VOLATILITY_SPIKE

        if _relative_strength_breakdown(context):
            return ActionDecisionType.REDUCE_POSITION, ActionReasonCode.RELATIVE_STRENGTH_BREAKDOWN

        return ActionDecisionType.NO_ACTION, ActionReasonCode.NONE

    def _score_confidence(self, reason_code: ActionReasonCode) -> float:
        if reason_code in {ActionReasonCode.STOP_BREACH, ActionReasonCode.TIME_STOP_EXPIRED}:
            return 90.0
        if reason_code == ActionReasonCode.TARGET2_REACHED:
            return 88.0
        if reason_code == ActionReasonCode.TARGET1_REACHED:
            return 78.0
        if reason_code in {
            ActionReasonCode.GAP_ADVERSE,
            ActionReasonCode.VOLATILITY_SPIKE,
            ActionReasonCode.RELATIVE_STRENGTH_BREAKDOWN,
        }:
            return 72.0
        return 0.0

    def _memory_bonus(self, *, context: ActionContext, reason_code: ActionReasonCode) -> float:
        lessons = list(context.ticker_lessons or []) + list(context.semantic_lessons or [])
        if not lessons or reason_code == ActionReasonCode.NONE:
            return 0.0

        tags: List[str] = []
        for lesson in lessons:
            meta = lesson.get("metadata", {}) if isinstance(lesson, dict) else {}
            ls_tags = meta.get("tags", [])
            if isinstance(ls_tags, list):
                tags.extend(str(t).lower() for t in ls_tags)

        lookup = {
            ActionReasonCode.STOP_BREACH: ("stop", "drawdown", "loss"),
            ActionReasonCode.TIME_STOP_EXPIRED: ("time", "horizon", "stale"),
            ActionReasonCode.TARGET1_REACHED: ("target", "profit", "trim"),
            ActionReasonCode.TARGET2_REACHED: ("target", "profit", "exit"),
            ActionReasonCode.GAP_ADVERSE: ("gap", "volatility", "risk"),
            ActionReasonCode.VOLATILITY_SPIKE: ("volatility", "whipsaw", "risk"),
            ActionReasonCode.RELATIVE_STRENGTH_BREAKDOWN: ("relative", "weakness", "underperform"),
        }
        needles = lookup.get(reason_code, ())
        matches = 0
        for tag in tags:
            if any(n in tag for n in needles):
                matches += 1
        if matches <= 0:
            return 0.0
        if matches >= 4:
            return 10.0
        if matches >= 2:
            return 7.0
        return 5.0

    def _recommended_qty_pct(self, decision_type: ActionDecisionType) -> Optional[float]:
        if decision_type == ActionDecisionType.TAKE_PROFIT_PARTIAL:
            return max(0.05, min(1.0, self.partial_take_profit_pct))
        if decision_type in {
            ActionDecisionType.EXIT_POSITION,
            ActionDecisionType.TAKE_PROFIT_FULL,
        }:
            return 1.0
        if decision_type == ActionDecisionType.REDUCE_POSITION:
            return 0.5
        return None


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stop_breached(*, thesis: TradeThesis, price: float) -> bool:
    if thesis.stop_loss is None:
        return False
    if thesis.action == "BUY":
        return price <= float(thesis.stop_loss)
    if thesis.action == "SELL":
        return price >= float(thesis.stop_loss)
    return False


def _target1_reached(*, thesis: TradeThesis, price: float) -> bool:
    if thesis.target_1 is None:
        return False
    if thesis.action == "BUY":
        return price >= float(thesis.target_1)
    if thesis.action == "SELL":
        return price <= float(thesis.target_1)
    return False


def _target2_reached(*, thesis: TradeThesis, price: float) -> bool:
    if thesis.target_2 is None:
        return False
    if thesis.action == "BUY":
        return price >= float(thesis.target_2)
    if thesis.action == "SELL":
        return price <= float(thesis.target_2)
    return False


def _time_stop_expired(thesis: TradeThesis) -> bool:
    if not thesis.time_stop_date:
        return False
    try:
        return datetime.utcnow().date() >= datetime.strptime(thesis.time_stop_date, "%Y-%m-%d").date()
    except ValueError:
        return False


def _is_gap_adverse(context: ActionContext) -> bool:
    for alert in context.recent_alerts:
        if str(alert.get("alert_type", "")).lower() == "gap_risk":
            return True
    return False


def _relative_strength_breakdown(context: ActionContext) -> bool:
    rs = _safe_float(context.snapshot.relative_strength)
    pl = _safe_float(context.snapshot.unrealized_pl_pct)
    if rs is None:
        return False
    if rs <= -2.0 and (pl is None or pl < 0):
        return True
    return False


def _defensive_reduction_needed(context: ActionContext) -> bool:
    if _is_gap_adverse(context):
        return _relative_strength_breakdown(context)

    snap = context.snapshot
    pl = _safe_float(snap.unrealized_pl_pct)
    mae = _safe_float(snap.max_adverse_excursion_pct)
    rs = _safe_float(snap.relative_strength)

    if pl is None or rs is None:
        return False
    if pl <= -2.5 and rs <= -1.0:
        return True
    if mae is not None and mae <= -4.0 and rs <= -1.0:
        return True
    return False
