"""
Outcome Recorder — computes structured trade outcomes when positions close.

Called by the monitor when it detects a closed position, or manually via CLI.
Produces a TradeOutcome that can feed into the reflection/learning loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from verumtrade.agents.journal.core.models import (
    TradeThesis,
    TradeOutcome,
    ThesisStatus,
)
from verumtrade.agents.journal.core.store import JournalStore

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class OutcomeRecorder:
    """
    Computes and records structured outcomes for closed trades.

    Uses:
    - The original TradeThesis for planned parameters
    - Historical PositionSnapshots for path analysis (MAE, MFE)
    - Brokerage execution logs or final snapshot for exit price
    - SPY data for benchmark comparison
    """

    def __init__(self, store: JournalStore):
        self.store = store

    def record_outcome(
        self,
        thesis_id: str,
        exit_price: Optional[float] = None,
        exit_reason: Optional[str] = None,
    ) -> Optional[TradeOutcome]:
        """
        Compute and save a TradeOutcome for a closed thesis.

        Args:
            thesis_id: ID of the closed TradeThesis
            exit_price: The exit price (if known). Falls back to last snapshot price.
            exit_reason: Why the position was closed (stop_hit, target_reached,
                         time_stop, manual, invalidated). Auto-inferred if not provided.

        Returns:
            The computed TradeOutcome, or None if thesis not found.
        """
        thesis = self.store.get_thesis(thesis_id)
        if not thesis:
            logger.error(f"Thesis {thesis_id} not found")
            return None

        # Get the last snapshot for exit price fallback and excursion data
        last_snapshot = self.store.get_latest_snapshot(thesis_id)
        extremes = self.store.get_extreme_snapshots(thesis_id)

        # Determine exit price
        actual_exit = exit_price
        if actual_exit is None and last_snapshot:
            actual_exit = last_snapshot.current_price

        if actual_exit is None:
            logger.warning(
                f"No exit price available for thesis {thesis_id} ({thesis.ticker})"
            )

        # Build outcome
        outcome = TradeOutcome(
            thesis_id=thesis_id,
            ticker=thesis.ticker,
            entry_price=thesis.entry_price,
            exit_price=actual_exit,
        )

        # --- P&L ---
        if thesis.entry_price and actual_exit:
            if thesis.action == "BUY":
                outcome.realized_pl_pct = round(
                    (actual_exit - thesis.entry_price) / thesis.entry_price * 100, 4
                )
            else:  # SELL (short)
                outcome.realized_pl_pct = round(
                    (thesis.entry_price - actual_exit) / thesis.entry_price * 100, 4
                )

            if thesis.quantity:
                if thesis.action == "BUY":
                    outcome.realized_pl = round(
                        (actual_exit - thesis.entry_price) * thesis.quantity, 2
                    )
                else:
                    outcome.realized_pl = round(
                        (thesis.entry_price - actual_exit) * thesis.quantity, 2
                    )

        # --- Holding days ---
        if thesis.trade_date:
            try:
                entry_dt = datetime.strptime(thesis.trade_date, "%Y-%m-%d")
                close_dt = datetime.utcnow()
                calendar_days = (close_dt - entry_dt).days
                outcome.holding_days = max(0, int(calendar_days * 5 / 7))
            except ValueError:
                pass

        # --- Execution quality (slippage) ---
        if thesis.entry_price:
            # Entry slippage: how far fill was from planned entry midpoint
            if thesis.entry_zone_low and thesis.entry_zone_high:
                planned_mid = (thesis.entry_zone_low + thesis.entry_zone_high) / 2
                outcome.entry_slippage_pct = round(
                    abs(thesis.entry_price - planned_mid) / planned_mid * 100, 4
                )

        # --- Path analysis (from snapshots) ---
        outcome.max_adverse_excursion_pct = extremes.get("max_adverse_excursion_pct")
        outcome.max_favorable_excursion_pct = extremes.get("max_favorable_excursion_pct")

        # Capture ratio: how much of the best run-up was realized
        if (
            outcome.max_favorable_excursion_pct
            and outcome.max_favorable_excursion_pct > 0
            and outcome.realized_pl_pct is not None
        ):
            outcome.capture_ratio = round(
                max(0, outcome.realized_pl_pct) / outcome.max_favorable_excursion_pct,
                4,
            )

        # --- Benchmark comparison ---
        spy_return = self._get_spy_return(thesis.trade_date)
        if spy_return is not None:
            outcome.spy_return_pct = round(spy_return * 100, 4)
            if outcome.realized_pl_pct is not None:
                outcome.alpha_pct = round(
                    outcome.realized_pl_pct - outcome.spy_return_pct, 4
                )

        # --- Thesis accuracy ---
        outcome.exit_reason = exit_reason or self._infer_exit_reason(thesis, actual_exit)

        # Did the stock move in the predicted direction?
        if outcome.realized_pl_pct is not None:
            outcome.thesis_correct = outcome.realized_pl_pct > 0

        # Did price reach any target?
        if thesis.target_1 and actual_exit:
            if thesis.action == "BUY":
                outcome.target_reached = actual_exit >= thesis.target_1
            else:
                outcome.target_reached = actual_exit <= thesis.target_1

        # Was stop triggered?
        if thesis.stop_loss and actual_exit:
            if thesis.action == "BUY":
                outcome.stop_triggered = actual_exit <= thesis.stop_loss
            else:
                outcome.stop_triggered = actual_exit >= thesis.stop_loss

        # Time-stop?
        if thesis.time_stop_date:
            try:
                ts = datetime.strptime(thesis.time_stop_date, "%Y-%m-%d")
                outcome.time_stop_triggered = datetime.utcnow() >= ts
            except ValueError:
                pass

        # --- Risk metrics ---
        # Risk multiple: P&L in units of original risk
        if (
            thesis.entry_price
            and thesis.stop_loss
            and outcome.realized_pl_pct is not None
        ):
            risk_pct = abs(thesis.entry_price - thesis.stop_loss) / thesis.entry_price * 100
            if risk_pct > 0:
                outcome.risk_multiple = round(outcome.realized_pl_pct / risk_pct, 2)

        # Actual R:R
        if thesis.entry_price and thesis.stop_loss and actual_exit:
            risk = abs(thesis.entry_price - thesis.stop_loss)
            reward = abs(actual_exit - thesis.entry_price)
            if risk > 0:
                outcome.risk_reward_actual = round(reward / risk, 2)

        # --- Update thesis status based on exit reason ---
        status_map = {
            "stop_hit": ThesisStatus.STOPPED_OUT,
            "target_reached": ThesisStatus.TARGET_REACHED,
            "time_stop": ThesisStatus.TIME_STOPPED,
            "manual": ThesisStatus.MANUALLY_CLOSED,
            "invalidated": ThesisStatus.INVALIDATED,
        }
        final_status = status_map.get(
            outcome.exit_reason or "", ThesisStatus.CLOSED
        )
        self.store.close_thesis(thesis_id, final_status)

        # Save outcome
        self.store.save_outcome(outcome)
        logger.info(
            f"Recorded outcome for {thesis.ticker} (thesis {thesis_id}): "
            f"P&L={outcome.realized_pl_pct}%, exit_reason={outcome.exit_reason}"
        )

        return outcome

    def record_all_closed(self) -> List[TradeOutcome]:
        """
        Scan for closed theses that don't have outcomes yet and record them.

        Returns list of newly recorded outcomes.
        """
        outcomes = []
        with self.store._conn() as conn:
            rows = conn.execute(
                """
                SELECT t.id FROM trade_theses t
                LEFT JOIN trade_outcomes o ON t.id = o.thesis_id
                WHERE t.status != 'active' AND o.id IS NULL
                """
            ).fetchall()

        for row in rows:
            thesis_id = row["id"]
            try:
                outcome = self.record_outcome(thesis_id)
                if outcome:
                    outcomes.append(outcome)
            except Exception as e:
                logger.error(f"Failed to record outcome for thesis {thesis_id}: {e}")

        return outcomes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_exit_reason(
        self, thesis: TradeThesis, exit_price: Optional[float]
    ) -> str:
        """Infer exit reason from thesis parameters and exit price."""
        if not exit_price:
            return "unknown"

        # Check stop
        if thesis.stop_loss:
            if thesis.action == "BUY" and exit_price <= thesis.stop_loss:
                return "stop_hit"
            if thesis.action == "SELL" and exit_price >= thesis.stop_loss:
                return "stop_hit"

        # Check target
        if thesis.target_1:
            if thesis.action == "BUY" and exit_price >= thesis.target_1:
                return "target_reached"
            if thesis.action == "SELL" and exit_price <= thesis.target_1:
                return "target_reached"

        # Check time-stop
        if thesis.time_stop_date:
            try:
                ts = datetime.strptime(thesis.time_stop_date, "%Y-%m-%d")
                if datetime.utcnow() >= ts:
                    return "time_stop"
            except ValueError:
                pass

        return "manual"

    def _get_spy_return(self, trade_date: Optional[str]) -> Optional[float]:
        """Get SPY total return from trade_date to now."""
        if not trade_date:
            return None
        try:
            import yfinance as yf
            start_date = date.fromisoformat(trade_date)
            today_et = datetime.now(ET).date()
            if start_date > today_et:
                logger.info(
                    "Skipping SPY return calculation for future trade_date=%s (today_et=%s)",
                    trade_date,
                    today_et.isoformat(),
                )
                return None

            spy = yf.Ticker("SPY")
            hist = spy.history(
                start=start_date.isoformat(),
                end=(today_et + timedelta(days=1)).isoformat(),
            )
            if hist.empty or len(hist) < 2:
                return None
            start = float(hist["Close"].iloc[0])
            end = float(hist["Close"].iloc[-1])
            return (end - start) / start
        except Exception as e:
            logger.warning(f"SPY return calculation failed: {e}")
            return None
