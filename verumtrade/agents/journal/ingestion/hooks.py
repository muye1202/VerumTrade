"""
Execution Hook — auto-registers trades in the journal.

Call `hook_into_execution()` after a trade executes to capture the thesis.
This is the glue between the existing execution flow and the new journal system.

Usage in the existing pipeline (analysis_utils.py or portfolio_analysis_utils.py):

    from verumtrade.agents.journal.ingestion.hooks import capture_trade_thesis

    # After propagate_and_execute or manual execute_signal:
    capture_trade_thesis(
        store=journal_store,
        final_state=final_state,
        structured_decision=structured,
        execution_result=execution_result,
        trade_date=analysis_date,
    )
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from verumtrade.agents.journal.core.models import TradeThesis, ThesisStatus
from verumtrade.agents.journal.core.store import JournalStore
from verumtrade.agents.journal.ingestion.thesis_extractor import ThesisExtractor

logger = logging.getLogger(__name__)


def capture_trade_thesis(
    store: JournalStore,
    final_state: Dict[str, Any],
    structured_decision: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
    executor: Any = None,
) -> Optional[TradeThesis]:
    """
    Extract a thesis from the pipeline output and save it to the journal.

    Only saves if the action is BUY or SELL (not HOLD).

    Args:
        store: JournalStore instance
        final_state: From VerumtradeGraph.propagate()
        structured_decision: From SignalProcessor.extract_structured_decision()
        execution_result: From AlpacaExecutor.execute_signal() (optional)
        trade_date: ISO date string
        executor: Optional executor for broker-truth aggregation

    Returns:
        The saved TradeThesis, or None if action was HOLD or extraction failed.
    """
    action = structured_decision.get("action", "HOLD").upper()
    if action == "HOLD":
        logger.debug("Action is HOLD — skipping journal capture")
        return None

    try:
        thesis = ThesisExtractor.extract(
            final_state=final_state,
            structured_decision=structured_decision,
            execution_result=execution_result,
            trade_date=trade_date,
        )

        # Only mark as active if the trade was actually executed
        if execution_result and execution_result.get("executed"):
            thesis.status = ThesisStatus.ACTIVE.value
        else:
            # Trade was recommended but not executed — still track it
            # but mark differently so the monitor doesn't try to find a brokerage position
            thesis.status = ThesisStatus.ACTIVE.value  # Still monitor if position exists

        ticker_u = thesis.ticker.upper()
        existing = store.get_active_thesis_by_ticker(ticker_u)

        # New thesis when none exists or this is not a BUY add.
        if (
            existing is None
            or thesis.action != "BUY"
            or (existing.id == thesis.id)
        ):
            store.save_thesis(thesis)
        else:
            merged = _merge_buy_thesis(
                existing=existing,
                incoming=thesis,
                store=store,
                execution_result=execution_result,
                executor=executor,
            )
            thesis = merged
            store.save_thesis(thesis)

        # Safety invariant: one active thesis per ticker.
        store.deduplicate_active_theses(executor=executor)

        logger.info(
            f"Captured thesis {thesis.id} for {thesis.ticker}: "
            f"action={thesis.action}, entry=${thesis.entry_price}, "
            f"stop=${thesis.stop_loss}, target=${thesis.target_1}, "
            f"time_stop={thesis.time_stop_date}"
        )

        return thesis

    except Exception as e:
        logger.error(f"Failed to capture trade thesis: {e}", exc_info=True)
        return None


def refresh_active_thesis_from_portfolio_analysis(
    store: JournalStore,
    final_state: Dict[str, Any],
    structured_decision: Dict[str, Any],
    trade_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Refresh an existing active thesis from portfolio-analysis output.

    This is a non-critical update path intended for portfolio analysis mode.
    It never creates new theses; it only patches an already-active ticker thesis.
    """
    ticker = str(
        structured_decision.get("ticker")
        or final_state.get("company_of_interest")
        or ""
    ).upper()
    if not ticker:
        return {"status": "skipped", "reason": "missing_ticker", "ticker": ""}

    existing = store.get_active_thesis_by_ticker(ticker)
    if existing is None:
        return {"status": "skipped", "reason": "no_active_thesis", "ticker": ticker}

    try:
        extracted = ThesisExtractor.extract(
            final_state=final_state,
            structured_decision=structured_decision,
            execution_result=None,
            trade_date=trade_date,
        )

        # Always replace configured analysis-refresh fields.
        _replace_fields = (
            "action",
            "conviction",
            "stop_loss",
            "target_1",
            "target_2",
            "entry_zone_low",
            "entry_zone_high",
            "trailing_stop_pct",
            "time_horizon_label",
            "holding_days_planned",
            "time_stop_date",
            "invalidation_trigger",
            "regime",
            "catalyst",
            "key_risks",
            "market_analyst_summary",
            "fundamentals_summary",
            "news_summary",
            "risk_judge_summary",
            "final_decision_text",
            "risk_reward_ratio",
            "stop_loss_pct",
        )
        for field_name in _replace_fields:
            setattr(existing, field_name, getattr(extracted, field_name, None))

        store.save_thesis(existing)
        logger.info(
            "Refreshed active thesis %s for %s from portfolio analysis "
            "(action=%s stop=%s target1=%s time_stop=%s)",
            existing.id,
            ticker,
            existing.action,
            existing.stop_loss,
            existing.target_1,
            existing.time_stop_date,
        )
        return {
            "status": "updated",
            "reason": "refreshed",
            "ticker": ticker,
            "thesis_id": existing.id,
        }
    except Exception as e:
        logger.warning(
            "Failed refreshing active thesis for %s from portfolio analysis: %s",
            ticker,
            e,
            exc_info=True,
        )
        return {
            "status": "failed",
            "reason": str(e),
            "ticker": ticker,
            "thesis_id": existing.id,
        }


def _merge_buy_thesis(
    existing: TradeThesis,
    incoming: TradeThesis,
    store: JournalStore,
    execution_result: Optional[Dict[str, Any]],
    executor: Any = None,
) -> TradeThesis:
    """Merge repeated BUY into existing active ticker thesis."""
    merged = existing
    merged.action = "BUY"
    merged.status = ThesisStatus.ACTIVE.value

    # Prefer incoming execution metadata.
    if incoming.order_id:
        merged.order_id = incoming.order_id
    if incoming.order_type:
        merged.order_type = incoming.order_type
    if incoming.final_decision_text:
        merged.final_decision_text = incoming.final_decision_text

    # Update latest thesis/risk content only when explicitly present.
    for attr in (
        "stop_loss",
        "target_1",
        "target_2",
        "time_horizon_label",
        "time_stop_date",
        "holding_days_planned",
        "conviction",
        "trailing_stop_pct",
        "position_size_pct",
        "regime",
        "catalyst",
        "key_risks",
        "invalidation_trigger",
        "market_analyst_summary",
        "fundamentals_summary",
        "news_summary",
        "risk_judge_summary",
    ):
        value = getattr(incoming, attr, None)
        if value is not None:
            setattr(merged, attr, value)

    # Authoritative aggregation: brokerage avg_entry/qty when available.
    broker_pos = store.get_open_position_for_ticker(executor, merged.ticker)
    if broker_pos:
        qty = _safe_float(broker_pos.get("qty"))
        avg_entry = _safe_float(broker_pos.get("avg_entry_price"))
        if qty is not None:
            merged.quantity = int(abs(qty))
        if avg_entry and avg_entry > 0:
            merged.entry_price = avg_entry
            merged.entry_price_source = "broker_avg_entry"
            merged.entry_price_pending = False
            merged.last_reconciled_at = datetime.utcnow().isoformat()
            return merged

    # Fallback: weighted average from prior thesis + incoming buy.
    old_qty = _safe_float(existing.quantity) or 0.0
    old_entry = _safe_float(existing.entry_price)
    add_qty = _safe_float(incoming.quantity) or 0.0
    add_entry = _safe_float(incoming.entry_price)
    if old_qty > 0 and old_entry and add_qty > 0 and add_entry:
        total_qty = old_qty + add_qty
        merged.quantity = int(abs(total_qty))
        merged.entry_price = round(((old_qty * old_entry) + (add_qty * add_entry)) / total_qty, 6)
        merged.entry_price_source = incoming.entry_price_source or existing.entry_price_source
        merged.entry_price_pending = bool(incoming.entry_price_pending)
        if not merged.entry_price_pending:
            merged.last_reconciled_at = datetime.utcnow().isoformat()
    else:
        # Last-resort overwrite if incoming has better info.
        if incoming.entry_price:
            merged.entry_price = incoming.entry_price
        if incoming.quantity:
            merged.quantity = incoming.quantity
        if incoming.entry_price_source:
            merged.entry_price_source = incoming.entry_price_source
        merged.entry_price_pending = bool(incoming.entry_price_pending)
        if not merged.entry_price_pending and merged.entry_price:
            merged.last_reconciled_at = datetime.utcnow().isoformat()

    if merged.entry_price and merged.stop_loss and merged.target_1:
        risk = abs(merged.entry_price - merged.stop_loss)
        reward = abs(merged.target_1 - merged.entry_price)
        if risk > 0:
            merged.risk_reward_ratio = round(reward / risk, 2)
    if merged.entry_price and merged.stop_loss:
        merged.stop_loss_pct = round(
            abs(merged.entry_price - merged.stop_loss) / merged.entry_price * 100,
            2,
        )

    return merged


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def capture_from_propagate_and_execute(
    store: JournalStore,
    graph: Any,  # VerumtradeGraph
    final_state: Dict[str, Any],
    decision: str,
    execution_result: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
    executor: Any = None,
) -> Optional[TradeThesis]:
    """
    Convenience wrapper that extracts the structured decision from the graph
    and then captures the thesis.

    Use this when you have the raw outputs from propagate_and_execute().
    """
    structured = graph.extract_structured_decision(
        final_state.get("final_trade_decision", "")
    )
    # Ensure action is set from the decision string if not in structured
    if not structured.get("action") or structured["action"] == "HOLD":
        structured["action"] = decision.strip().upper()

    return capture_trade_thesis(
        store=store,
        final_state=final_state,
        structured_decision=structured,
        execution_result=execution_result,
        trade_date=trade_date,
        executor=executor,
    )
