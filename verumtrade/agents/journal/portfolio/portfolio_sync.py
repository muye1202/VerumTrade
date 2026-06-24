"""Utilities for syncing live brokerage positions into journal theses."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from verumtrade.agents.journal.core.models import ThesisStatus, TradeThesis
from verumtrade.agents.journal.core.store import JournalStore

logger = logging.getLogger(__name__)


def sync_missing_positions(
    store: JournalStore,
    executor: Any,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Create active theses for live brokerage positions not already tracked.

    Existing active ticker theses are never modified (create-only behavior).
    """
    summary: Dict[str, Any] = {
        "positions_seen": 0,
        "created": 0,
        "skipped_existing": 0,
        "errors": [],
        "created_tickers": [],
    }

    positions = _fetch_brokerage_positions(executor)
    summary["positions_seen"] = len(positions)

    for position in positions:
        try:
            normalized = _normalize_position(position)
            ticker = normalized["symbol"]

            existing = store.get_active_thesis_by_ticker(ticker)
            if existing is not None:
                summary["skipped_existing"] += 1
                continue

            thesis = _build_synced_thesis(normalized, now=now)
            store.save_thesis(thesis)
            summary["created"] += 1
            summary["created_tickers"].append(ticker)
        except Exception as e:
            err = f"{position!r}: {e}"
            summary["errors"].append(err)
            logger.warning("Failed syncing position to journal: %s", err, exc_info=True)

    return summary


def _fetch_brokerage_positions(executor: Any) -> List[Dict[str, Any]]:
    if executor is None:
        return []
    try:
        portfolio = executor.get_portfolio_summary()
    except Exception as e:
        logger.error("Failed to fetch brokerage portfolio for journal sync: %s", e)
        return []
    return list(portfolio.get("positions") or [])


def _normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(position.get("symbol") or "").upper().strip()
    if not symbol:
        raise ValueError("missing symbol")

    qty = _safe_float(position.get("qty"))
    if qty is None or qty == 0:
        raise ValueError("invalid qty")

    avg_entry = _safe_float(position.get("avg_entry_price"))
    if avg_entry is None or avg_entry <= 0:
        market_value = _safe_float(position.get("market_value"))
        if market_value is not None and qty != 0:
            avg_entry = abs(market_value) / abs(qty)

    if avg_entry is None or avg_entry <= 0:
        raise ValueError("invalid avg_entry_price")

    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": float(avg_entry),
    }


def _build_synced_thesis(position: Dict[str, Any], *, now: Optional[datetime] = None) -> TradeThesis:
    ts = now or datetime.utcnow()
    ticker = str(position["symbol"]).upper()
    qty = float(position["qty"])
    entry_price = float(position["avg_entry_price"])
    is_long = qty > 0

    if is_long:
        stop_loss = entry_price * 0.95
        target_1 = entry_price * 1.10
        action = "BUY"
    else:
        stop_loss = entry_price * 1.05
        target_1 = entry_price * 0.90
        action = "SELL"

    risk = abs(entry_price - stop_loss)
    reward = abs(target_1 - entry_price)
    risk_reward_ratio = (reward / risk) if risk > 0 else None

    quantity = max(1, int(abs(qty)))

    return TradeThesis(
        ticker=ticker,
        trade_date=ts.strftime("%Y-%m-%d"),
        action=action,
        conviction=70.0,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        risk_reward_ratio=risk_reward_ratio,
        time_horizon_label="swing",
        holding_days_planned=10,
        catalyst="Position opened before journal activation",
        regime="unknown",
        key_risks="Manually synced - no original analysis",
        quantity=quantity,
        market_analyst_summary="[Synced from existing position]",
        final_decision_text=f"Synced existing {ticker} position",
        status=ThesisStatus.ACTIVE.value,
    )


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
