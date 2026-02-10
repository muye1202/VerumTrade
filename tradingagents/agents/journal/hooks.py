"""
Execution Hook — auto-registers trades in the journal.

Call `hook_into_execution()` after a trade executes to capture the thesis.
This is the glue between the existing execution flow and the new journal system.

Usage in the existing pipeline (analysis_utils.py or portfolio_analysis_utils.py):

    from tradingagents.agents.journal.hooks import capture_trade_thesis

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
from typing import Any, Dict, Optional

from tradingagents.agents.journal.models import TradeThesis, ThesisStatus
from tradingagents.agents.journal.store import JournalStore
from tradingagents.agents.journal.thesis_extractor import ThesisExtractor

logger = logging.getLogger(__name__)


def capture_trade_thesis(
    store: JournalStore,
    final_state: Dict[str, Any],
    structured_decision: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
) -> Optional[TradeThesis]:
    """
    Extract a thesis from the pipeline output and save it to the journal.

    Only saves if the action is BUY or SELL (not HOLD).

    Args:
        store: JournalStore instance
        final_state: From TradingAgentsGraph.propagate()
        structured_decision: From SignalProcessor.extract_structured_decision()
        execution_result: From AlpacaExecutor.execute_signal() (optional)
        trade_date: ISO date string

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

        store.save_thesis(thesis)

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


def capture_from_propagate_and_execute(
    store: JournalStore,
    graph: Any,  # TradingAgentsGraph
    final_state: Dict[str, Any],
    decision: str,
    execution_result: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
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
    )
