"""
Thesis Extractor — pulls structured thesis parameters from the multi-agent
analysis state and execution results.

This bridges the gap between the free-text agent outputs and the structured
data the journal needs to monitor positions.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from tradingagents.agents.journal.models import TradeThesis, ThesisStatus

logger = logging.getLogger(__name__)


class ThesisExtractor:
    """
    Extracts a TradeThesis from:
    1. The agent analysis final_state (reports, decision text)
    2. The structured decision dict (from SignalProcessor)
    3. The execution result dict (from AlpacaExecutor)

    Call `extract()` after a trade is executed (or after analysis if no execution).
    """

    @staticmethod
    def extract(
        final_state: Dict[str, Any],
        structured_decision: Dict[str, Any],
        execution_result: Optional[Dict[str, Any]] = None,
        trade_date: Optional[str] = None,
    ) -> TradeThesis:
        """
        Build a TradeThesis from the full agent pipeline output.

        Args:
            final_state: The final state dict from TradingAgentsGraph.propagate()
            structured_decision: Dict from SignalProcessor.extract_structured_decision()
            execution_result: Dict from AlpacaExecutor.execute_signal() (optional)
            trade_date: ISO date string (falls back to final_state['trade_date'])

        Returns:
            A populated TradeThesis ready to be saved to the JournalStore.
        """
        ticker = (
            structured_decision.get("ticker")
            or final_state.get("company_of_interest", "")
        ).upper()

        thesis = TradeThesis(
            ticker=ticker,
            trade_date=trade_date or final_state.get("trade_date", ""),
            action=structured_decision.get("action", "HOLD"),
            status=ThesisStatus.ACTIVE.value,
        )

        # --- From structured decision ---
        thesis.conviction = _parse_float(structured_decision.get("confidence"))
        thesis.stop_loss = _parse_float(structured_decision.get("stop_loss"))
        thesis.target_1 = _parse_float(structured_decision.get("take_profit"))
        thesis.order_type = structured_decision.get("order_type")
        thesis.quantity = _parse_int(structured_decision.get("quantity"))
        thesis.position_size_pct = _parse_float(
            structured_decision.get("position_size_pct")
        )
        thesis.trailing_stop_pct = _parse_float(
            structured_decision.get("trail_percent")
        )
        thesis.time_horizon_label = structured_decision.get("time_horizon")

        # --- From execution result ---
        if execution_result and execution_result.get("executed"):
            final_entry = _parse_float(execution_result.get("entry_price_final"))
            order_entry = _parse_float(
                (execution_result.get("order") or {}).get("filled_avg_price")
            )
            provisional_entry = _parse_float(
                execution_result.get("entry_price_provisional")
                or execution_result.get("price")
            )

            if final_entry:
                thesis.entry_price = final_entry
                thesis.entry_price_source = "filled_avg_price"
                thesis.entry_price_pending = False
            elif order_entry:
                thesis.entry_price = order_entry
                thesis.entry_price_source = "filled_avg_price"
                thesis.entry_price_pending = False
            elif provisional_entry:
                thesis.entry_price = provisional_entry
                thesis.entry_price_source = "submission_quote"
                thesis.entry_price_pending = bool(
                    execution_result.get("order_needs_reconcile", True)
                )
            else:
                thesis.entry_price = None
                thesis.entry_price_source = "unknown"
                thesis.entry_price_pending = bool(
                    execution_result.get("order_needs_reconcile", False)
                )

            if not thesis.entry_price_pending:
                thesis.last_reconciled_at = datetime.utcnow().isoformat()
            thesis.order_id = execution_result.get("order", {}).get("id")
            thesis.quantity = _parse_int(
                execution_result.get("qty")
            ) or thesis.quantity

        # --- Parse the raw decision text for additional thesis parameters ---
        decision_text = final_state.get("final_trade_decision", "")
        thesis.final_decision_text = _truncate(decision_text, 4000)

        parsed = _parse_thesis_levels(decision_text)
        # Only override if the structured decision didn't already have them
        if not thesis.stop_loss and parsed.get("stop_loss"):
            thesis.stop_loss = parsed["stop_loss"]
        if not thesis.target_1 and parsed.get("target_1"):
            thesis.target_1 = parsed["target_1"]
        if parsed.get("target_2"):
            thesis.target_2 = parsed["target_2"]
        if parsed.get("entry_zone_low"):
            thesis.entry_zone_low = parsed["entry_zone_low"]
        if parsed.get("entry_zone_high"):
            thesis.entry_zone_high = parsed["entry_zone_high"]
        if parsed.get("invalidation"):
            thesis.invalidation_trigger = _truncate(parsed["invalidation"], 500)
        if parsed.get("regime"):
            thesis.regime = _truncate(parsed["regime"], 200)
        if parsed.get("catalyst"):
            thesis.catalyst = _truncate(parsed["catalyst"], 500)
        if parsed.get("risks"):
            thesis.key_risks = _truncate(parsed["risks"], 500)

        # --- Compute derived fields ---

        # Risk:reward ratio
        if thesis.entry_price and thesis.stop_loss and thesis.target_1:
            risk = abs(thesis.entry_price - thesis.stop_loss)
            reward = abs(thesis.target_1 - thesis.entry_price)
            if risk > 0:
                thesis.risk_reward_ratio = round(reward / risk, 2)

        # Stop-loss as percentage from entry
        if thesis.entry_price and thesis.stop_loss:
            thesis.stop_loss_pct = round(
                abs(thesis.entry_price - thesis.stop_loss) / thesis.entry_price * 100, 2
            )

        # Time-stop date (estimate from time_horizon_label)
        thesis.holding_days_planned = _estimate_holding_days(thesis.time_horizon_label)
        if thesis.holding_days_planned and thesis.trade_date:
            try:
                entry_date = datetime.strptime(thesis.trade_date, "%Y-%m-%d")
                # Rough: add calendar days ≈ trading_days * 7/5
                calendar_days = int(thesis.holding_days_planned * 7 / 5)
                thesis.time_stop_date = (
                    entry_date + timedelta(days=calendar_days)
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

        # --- Compressed agent summaries ---
        thesis.market_analyst_summary = _truncate(
            final_state.get("market_report", ""), 1500
        )
        thesis.fundamentals_summary = _truncate(
            final_state.get("fundamentals_report", ""), 1500
        )
        thesis.news_summary = _truncate(
            final_state.get("news_report", ""), 1500
        )

        # Risk judge decision is nested in risk_debate_state
        risk_debate = final_state.get("risk_debate_state", {})
        if isinstance(risk_debate, dict):
            thesis.risk_judge_summary = _truncate(
                risk_debate.get("judge_decision", ""), 1500
            )

        return thesis


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_float(value) -> Optional[float]:
    """Safely parse a float from various input types."""
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None  # NaN check
    except (ValueError, TypeError):
        if isinstance(value, str):
            m = re.search(r"([-+]?\d[\d,]*\.?\d*)", value.replace("$", ""))
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return None


def _parse_int(value) -> Optional[int]:
    """Safely parse an int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        f = _parse_float(value)
        return int(f) if f is not None else None


def _truncate(text: Optional[str], max_len: int) -> Optional[str]:
    """Truncate text to max_len, preserving None."""
    if not text:
        return None
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _estimate_holding_days(label: Optional[str]) -> Optional[int]:
    """
    Estimate planned holding days from a time horizon label.

    Handles labels like:
        "swing (1-3 weeks)" -> 10  (midpoint)
        "short-term" -> 5
        "medium-term" -> 20
        "long-term" -> 60
        "~10-15 trading days" -> 12
    """
    if not label:
        return None
    label_lower = label.lower()

    # Try to extract explicit trading day ranges
    m = re.search(r"~?(\d+)\s*[-–]\s*(\d+)\s*trading\s*days?", label_lower)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo + hi) // 2

    # Try week ranges
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*weeks?", label_lower)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return ((lo + hi) // 2) * 5  # 5 trading days per week

    # Fallback labels
    if "day" in label_lower and "trade" not in label_lower:
        m = re.search(r"(\d+)", label_lower)
        if m:
            return int(m.group(1))

    if "short" in label_lower:
        return 5
    if "swing" in label_lower:
        return 10
    if "medium" in label_lower:
        return 20
    if "long" in label_lower:
        return 60

    return None


def _parse_thesis_levels(text: str) -> Dict[str, Any]:
    """
    Parse thesis levels from free-text decision output.

    Looks for patterns like:
        STOP_LOSS: $145.00
        TAKE_PROFIT: $165.00
        TARGET_1: $160.00
        TARGET_2: $175.00
        Entry zone: $148-$152
        Invalidation: close below $140
        Regime: uptrend
    """
    if not text:
        return {}

    result: Dict[str, Any] = {}

    # Stop loss
    for pattern in [
        r"STOP[_\s]*LOSS\s*:\s*\$?([\d,]+\.?\d*)",
        r"stop[:\s]+\$?([\d,]+\.?\d*)",
        r"stop[-\s]loss[:\s]+\$?([\d,]+\.?\d*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["stop_loss"] = _parse_float(m.group(1))
            break

    # Targets
    for pattern in [
        r"TARGET[_\s]*1\s*:\s*\$?([\d,]+\.?\d*)",
        r"TAKE[_\s]*PROFIT\s*:\s*\$?([\d,]+\.?\d*)",
        r"(?:first|1st)\s+target[:\s]+\$?([\d,]+\.?\d*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["target_1"] = _parse_float(m.group(1))
            break

    for pattern in [
        r"TARGET[_\s]*2\s*:\s*\$?([\d,]+\.?\d*)",
        r"(?:second|2nd)\s+target[:\s]+\$?([\d,]+\.?\d*)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["target_2"] = _parse_float(m.group(1))
            break

    # Entry zone
    m = re.search(
        r"entry\s*(?:zone|range)?[:\s]+\$?([\d,]+\.?\d*)\s*[-–]\s*\$?([\d,]+\.?\d*)",
        text,
        re.IGNORECASE,
    )
    if m:
        result["entry_zone_low"] = _parse_float(m.group(1))
        result["entry_zone_high"] = _parse_float(m.group(2))

    # Invalidation
    m = re.search(
        r"(?:invalidat(?:ion|ed?)|breakdown)[:\s]+(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        result["invalidation"] = m.group(1).strip()[:300]

    # Regime
    for pattern in [
        r"regime[:\s]+(.+?)(?:\n|$)",
        r"(?:trend|regime)\s*:\s*(\w[\w\s/]+?)(?:\n|[,;])",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["regime"] = m.group(1).strip()[:200]
            break

    # Catalyst
    m = re.search(
        r"catalyst[:\s]+(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        result["catalyst"] = m.group(1).strip()[:300]

    # Key risks
    m = re.search(
        r"(?:key\s+)?risks?[:\s]+(.+?)(?:\n\n|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        result["risks"] = m.group(1).strip()[:500]

    return result
