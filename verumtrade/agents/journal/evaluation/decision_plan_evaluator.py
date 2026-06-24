from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from verumtrade.agents.journal.core.models import PositionSnapshot, TradeThesis


def evaluate_decision_plan(
    *,
    thesis: TradeThesis,
    snapshot: PositionSnapshot,
    market_session: str,
    event_confirmations: Optional[Dict[str, Any]] = None,
    volume_ratio: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a serialized v2 decision plan against current tick context.
    Returns a normalized trigger payload when a branch matches, else None.
    """
    raw = getattr(thesis, "decision_plan_json", None)
    if not raw:
        return None
    try:
        plan = json.loads(str(raw))
    except Exception:
        return None

    if str(plan.get("decision_version", "")).lower() != "v2":
        return None

    execution_plan = plan.get("execution_plan") or []
    if not isinstance(execution_plan, list):
        return None

    confirmations = event_confirmations or {}
    price = _safe_float(snapshot.current_price)
    trade_day = _extract_trade_day(snapshot.timestamp)
    session_norm = _normalize_session(market_session)

    for branch in execution_plan:
        if not isinstance(branch, dict):
            continue
        conditions = branch.get("conditions") or {}
        if not _matches_conditions(
            conditions=conditions,
            price=price,
            volume_ratio=volume_ratio,
            trade_day=trade_day,
            market_session=session_norm,
        ):
            continue
        event_conditions = conditions.get("event_conditions")
        if event_conditions is None:
            event_conditions = branch.get("event_conditions") or []
        if not _matches_events(event_conditions, confirmations):
            continue
        template = branch.get("action_template")
        if not isinstance(template, dict):
            continue
        return {
            "matched": True,
            "branch_id": str(branch.get("branch_id") or "").strip(),
            "action_template": template,
            "reason_code": "plan_trigger",
            "summary": {
                "plan_mode": plan.get("plan_mode"),
                "matched_branch_id": str(branch.get("branch_id") or "").strip(),
                "market_session": session_norm,
            },
        }
    return None


def _matches_conditions(
    *,
    conditions: Dict[str, Any],
    price: Optional[float],
    volume_ratio: Optional[float],
    trade_day: Optional[str],
    market_session: str,
) -> bool:
    price_cond = conditions.get("price") or {}
    if price_cond:
        if price is None:
            return False
        close_above = _safe_float(price_cond.get("close_above"))
        close_below = _safe_float(price_cond.get("close_below"))
        last_price = _safe_float(price_cond.get("last_price"))
        tolerance = _safe_float(price_cond.get("tolerance_pct")) or 0.0
        tol_mult = 1.0 + max(0.0, tolerance) / 100.0
        if close_above is not None and price < close_above:
            return False
        if close_below is not None and price > close_below:
            return False
        if last_price is not None:
            lower = last_price / tol_mult
            upper = last_price * tol_mult
            if price < lower or price > upper:
                return False

    volume_cond = conditions.get("volume") or {}
    if volume_cond:
        ratio_min = _safe_float(volume_cond.get("volume_ratio_min"))
        if ratio_min is not None:
            if volume_ratio is None:
                return False
            if float(volume_ratio) < float(ratio_min):
                return False

    schedule = conditions.get("schedule") or {}
    if schedule:
        session_constraint = _normalize_session(schedule.get("session_constraint") or "any")
        if session_constraint != "any" and market_session != session_constraint:
            return False
        valid_from = str(schedule.get("valid_from") or "").strip()
        valid_to = str(schedule.get("valid_to") or "").strip()
        if trade_day:
            if valid_from and trade_day < valid_from:
                return False
            if valid_to and trade_day > valid_to:
                return False

    return True


def _matches_events(event_conditions: Any, confirmations: Dict[str, Any]) -> bool:
    if not event_conditions:
        return True
    if not isinstance(event_conditions, list):
        return False
    for event in event_conditions:
        if not isinstance(event, dict):
            return False
        key = str(event.get("event_key") or "").strip()
        if not key:
            return False
        requires_confirmation = bool(event.get("requires_confirmation", True))
        expected = event.get("expected_value")
        if expected is not None:
            expected = str(expected).strip().lower()
        actual = confirmations.get(key)
        if requires_confirmation and actual is None:
            return False
        if expected is not None:
            if actual is None:
                return False
            if str(actual).strip().lower() != expected:
                return False
    return True


def _extract_trade_day(timestamp: Optional[str]) -> Optional[str]:
    if not timestamp:
        return None
    try:
        ts = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return ts.date().isoformat()
    except Exception:
        return None


def _normalize_session(value: Any) -> str:
    s = str(value or "any").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"premarket", "market_hours", "afterhours", "overnight", "weekend", "any"}:
        return s
    return "any"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
