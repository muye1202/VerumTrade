"""
Position Monitor — the scheduler's workhorse.

On each tick, the monitor:
1. Fetches live position data from Alpaca
2. Fetches current market prices via yfinance (fallback to Alpaca quotes)
3. Compares each active thesis against its parameters
4. Generates PositionSnapshots and JournalAlerts
5. Detects closed positions and triggers outcome recording

This is a pure-computation module — it doesn't schedule itself.
The JournalScheduler calls monitor.run_tick() on a timer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from tradingagents.journal.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    AlertType,
    ThesisStatus,
)
from tradingagents.journal.store import JournalStore

logger = logging.getLogger(__name__)


class PositionMonitor:
    """
    Monitors active theses against live market data.

    Dependencies:
        store: JournalStore for reading theses and writing snapshots/alerts
        executor: AlpacaExecutor instance (for brokerage position data)

    The monitor does NOT import yfinance or alpaca at module level —
    it lazy-imports to keep the module importable without those deps.
    """

    def __init__(
        self,
        store: JournalStore,
        executor: Any = None,  # AlpacaExecutor (optional — can work without brokerage)
        alert_dedup_hours: float = 4.0,
        spy_ticker: str = "SPY",
    ):
        self.store = store
        self.executor = executor
        self.alert_dedup_hours = alert_dedup_hours
        self.spy_ticker = spy_ticker

        # Cached SPY data per tick (reset each run_tick)
        self._spy_cache: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_tick(self) -> Dict[str, Any]:
        """
        Execute one monitoring cycle across all active theses.

        Returns:
            Summary dict with counts of snapshots taken, alerts fired,
            positions detected as closed, and any errors.
        """
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "theses_checked": 0,
            "snapshots_taken": 0,
            "alerts_fired": 0,
            "positions_closed": 0,
            "errors": [],
        }

        active_theses = self.store.get_active_theses()
        if not active_theses:
            logger.debug("No active theses to monitor.")
            return summary

        # Fetch all brokerage positions in one call
        brokerage_positions = self._fetch_brokerage_positions()
        brokerage_tickers = {
            p["symbol"].upper() for p in brokerage_positions
        }

        # Reset SPY cache for this tick
        self._spy_cache = {}

        for thesis in active_theses:
            try:
                self._monitor_thesis(thesis, brokerage_positions, brokerage_tickers, summary)
                summary["theses_checked"] += 1
            except Exception as e:
                error_msg = f"Error monitoring {thesis.ticker} (thesis {thesis.id}): {e}"
                logger.error(error_msg, exc_info=True)
                summary["errors"].append(error_msg)

        logger.info(
            f"Monitor tick complete: {summary['theses_checked']} theses, "
            f"{summary['snapshots_taken']} snapshots, "
            f"{summary['alerts_fired']} alerts, "
            f"{summary['positions_closed']} closed"
        )
        return summary

    # ------------------------------------------------------------------
    # Per-thesis monitoring
    # ------------------------------------------------------------------

    def _monitor_thesis(
        self,
        thesis: TradeThesis,
        brokerage_positions: List[Dict[str, Any]],
        brokerage_tickers: Set[str],
        summary: Dict[str, Any],
    ) -> None:
        """Monitor a single thesis."""
        ticker = thesis.ticker.upper()

        # Check if position still exists in brokerage
        if ticker not in brokerage_tickers:
            self._handle_position_closed(thesis, summary)
            return

        # Get brokerage position data
        pos_data = next(
            (p for p in brokerage_positions if p["symbol"].upper() == ticker),
            None,
        )

        # Fetch current price
        current_price = self._fetch_current_price(ticker)
        if current_price is None and pos_data:
            # Fallback: estimate from market_value / qty
            qty = pos_data.get("qty", 0)
            mv = pos_data.get("market_value", 0)
            if qty and float(qty) > 0:
                current_price = float(mv) / float(qty)

        if current_price is None:
            logger.warning(f"Could not get price for {ticker}, skipping snapshot")
            return

        # Build and save snapshot
        snapshot = self._build_snapshot(thesis, pos_data, current_price)
        self.store.save_snapshot(snapshot)
        summary["snapshots_taken"] += 1

        # Check thesis parameters and fire alerts
        alerts = self._check_thesis_parameters(thesis, snapshot, current_price)
        for alert in alerts:
            # Dedup: don't fire the same alert type repeatedly
            if not self.store.has_recent_alert(
                thesis.id, AlertType(alert.alert_type), self.alert_dedup_hours
            ):
                self.store.save_alert(alert)
                summary["alerts_fired"] += 1

    # ------------------------------------------------------------------
    # Snapshot building
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        thesis: TradeThesis,
        pos_data: Optional[Dict[str, Any]],
        current_price: float,
    ) -> PositionSnapshot:
        """Build a PositionSnapshot from thesis + live data."""
        snap = PositionSnapshot(
            thesis_id=thesis.id,
            ticker=thesis.ticker.upper(),
            current_price=current_price,
        )

        # Brokerage data
        if pos_data:
            snap.quantity = _safe_float(pos_data.get("qty"))
            snap.market_value = _safe_float(pos_data.get("market_value"))
            snap.unrealized_pl = _safe_float(pos_data.get("unrealized_pl"))
            snap.unrealized_pl_pct = _safe_float(pos_data.get("unrealized_plpc"))
            snap.cost_basis = _safe_float(pos_data.get("cost_basis"))

        # Distance to stop
        if thesis.stop_loss and current_price:
            if thesis.action == "BUY":
                snap.distance_to_stop_pct = round(
                    (current_price - thesis.stop_loss) / current_price * 100, 2
                )
            else:  # SELL/short
                snap.distance_to_stop_pct = round(
                    (thesis.stop_loss - current_price) / current_price * 100, 2
                )

        # Distance to target 1
        if thesis.target_1 and current_price:
            if thesis.action == "BUY":
                snap.distance_to_target1_pct = round(
                    (thesis.target_1 - current_price) / current_price * 100, 2
                )
            else:
                snap.distance_to_target1_pct = round(
                    (current_price - thesis.target_1) / current_price * 100, 2
                )

        # Holding days
        if thesis.trade_date:
            try:
                entry_dt = datetime.strptime(thesis.trade_date, "%Y-%m-%d")
                now = datetime.utcnow()
                calendar_days = (now - entry_dt).days
                snap.holding_days_elapsed = max(0, int(calendar_days * 5 / 7))  # Rough trading days

                if thesis.holding_days_planned:
                    snap.holding_days_remaining = max(
                        0, thesis.holding_days_planned - snap.holding_days_elapsed
                    )
            except ValueError:
                pass

        # SPY relative performance
        spy_return = self._get_spy_return_since(thesis.trade_date)
        if spy_return is not None and thesis.entry_price and current_price:
            stock_return = (current_price - thesis.entry_price) / thesis.entry_price
            snap.spy_change_pct = round(spy_return * 100, 2)
            snap.relative_strength = round((stock_return - spy_return) * 100, 2)

        # Max adverse/favorable excursion
        if thesis.entry_price and current_price:
            pct_from_entry = (current_price - thesis.entry_price) / thesis.entry_price * 100
            if thesis.action == "SELL":
                pct_from_entry = -pct_from_entry  # Invert for shorts

            # Get historical extremes
            extremes = self.store.get_extreme_snapshots(thesis.id)
            prev_mae = extremes.get("max_adverse_excursion_pct")
            prev_mfe = extremes.get("max_favorable_excursion_pct")

            adverse = min(pct_from_entry, 0)
            favorable = max(pct_from_entry, 0)

            snap.max_adverse_excursion_pct = round(
                min(adverse, prev_mae) if prev_mae is not None else adverse, 2
            )
            snap.max_favorable_excursion_pct = round(
                max(favorable, prev_mfe) if prev_mfe is not None else favorable, 2
            )

        return snap

    # ------------------------------------------------------------------
    # Thesis parameter checks → Alerts
    # ------------------------------------------------------------------

    def _check_thesis_parameters(
        self,
        thesis: TradeThesis,
        snapshot: PositionSnapshot,
        current_price: float,
    ) -> List[JournalAlert]:
        """Check all thesis parameters and return any alerts that should fire."""
        alerts: List[JournalAlert] = []

        # --- Stop-loss check ---
        if thesis.stop_loss:
            stop_breached = False
            if thesis.action == "BUY" and current_price <= thesis.stop_loss:
                stop_breached = True
            elif thesis.action == "SELL" and current_price >= thesis.stop_loss:
                stop_breached = True

            if stop_breached:
                alerts.append(
                    JournalAlert(
                        thesis_id=thesis.id,
                        ticker=thesis.ticker,
                        alert_type=AlertType.STOP_HIT.value,
                        severity="critical",
                        message=(
                            f"STOP-LOSS BREACHED: {thesis.ticker} at ${current_price:.2f} "
                            f"has {'fallen below' if thesis.action == 'BUY' else 'risen above'} "
                            f"stop-loss at ${thesis.stop_loss:.2f}"
                        ),
                        trigger_price=current_price,
                        threshold_price=thesis.stop_loss,
                        current_price=current_price,
                        unrealized_pl_pct=snapshot.unrealized_pl_pct,
                        holding_days=snapshot.holding_days_elapsed,
                        action_recommended="EXIT POSITION — stop-loss hit per original thesis",
                    )
                )

        # --- Target checks ---
        for target_num, target_price in [
            (1, thesis.target_1),
            (2, thesis.target_2),
        ]:
            if target_price:
                target_reached = False
                if thesis.action == "BUY" and current_price >= target_price:
                    target_reached = True
                elif thesis.action == "SELL" and current_price <= target_price:
                    target_reached = True

                if target_reached:
                    alerts.append(
                        JournalAlert(
                            thesis_id=thesis.id,
                            ticker=thesis.ticker,
                            alert_type=AlertType.TARGET_HIT.value,
                            severity="warning" if target_num == 1 else "info",
                            message=(
                                f"TARGET {target_num} REACHED: {thesis.ticker} at "
                                f"${current_price:.2f} hit target ${target_price:.2f}"
                            ),
                            trigger_price=current_price,
                            threshold_price=target_price,
                            current_price=current_price,
                            unrealized_pl_pct=snapshot.unrealized_pl_pct,
                            holding_days=snapshot.holding_days_elapsed,
                            action_recommended=(
                                f"Consider taking partial profits (target {target_num})"
                                if target_num == 1
                                else "Full target reached — consider closing position"
                            ),
                        )
                    )

        # --- Time-stop check ---
        if thesis.time_stop_date:
            try:
                time_stop = datetime.strptime(thesis.time_stop_date, "%Y-%m-%d")
                if datetime.utcnow() >= time_stop:
                    alerts.append(
                        JournalAlert(
                            thesis_id=thesis.id,
                            ticker=thesis.ticker,
                            alert_type=AlertType.TIME_STOP.value,
                            severity="warning",
                            message=(
                                f"TIME-STOP EXPIRED: {thesis.ticker} has been held for "
                                f"{snapshot.holding_days_elapsed} trading days, exceeding "
                                f"planned horizon of {thesis.holding_days_planned} days"
                            ),
                            current_price=current_price,
                            unrealized_pl_pct=snapshot.unrealized_pl_pct,
                            holding_days=snapshot.holding_days_elapsed,
                            action_recommended=(
                                "Review position — holding period expired. "
                                "Close unless thesis has been explicitly updated."
                            ),
                        )
                    )
            except ValueError:
                pass

        # --- Gap risk detection ---
        # If price moved > 3% since last snapshot, flag as gap risk
        last_snap = self.store.get_latest_snapshot(thesis.id)
        if last_snap and last_snap.current_price:
            pct_change = abs(current_price - last_snap.current_price) / last_snap.current_price * 100
            if pct_change > 3.0:
                alerts.append(
                    JournalAlert(
                        thesis_id=thesis.id,
                        ticker=thesis.ticker,
                        alert_type=AlertType.GAP_RISK.value,
                        severity="warning",
                        message=(
                            f"LARGE MOVE: {thesis.ticker} moved {pct_change:.1f}% since "
                            f"last check (${last_snap.current_price:.2f} → ${current_price:.2f})"
                        ),
                        trigger_price=current_price,
                        threshold_price=last_snap.current_price,
                        current_price=current_price,
                        unrealized_pl_pct=snapshot.unrealized_pl_pct,
                        holding_days=snapshot.holding_days_elapsed,
                        action_recommended="Review thesis — large price movement detected",
                    )
                )

        return alerts

    # ------------------------------------------------------------------
    # Closed position detection
    # ------------------------------------------------------------------

    def _handle_position_closed(
        self, thesis: TradeThesis, summary: Dict[str, Any]
    ) -> None:
        """Handle a thesis whose position no longer exists in the brokerage."""
        logger.info(
            f"Position {thesis.ticker} (thesis {thesis.id}) no longer in brokerage — marking closed"
        )

        # Fire a position_closed alert
        alert = JournalAlert(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            alert_type=AlertType.POSITION_CLOSED.value,
            severity="info",
            message=(
                f"POSITION CLOSED: {thesis.ticker} is no longer in your brokerage "
                f"account. The journal will compute the trade outcome."
            ),
            action_taken="Marked thesis as closed; outcome recording pending.",
        )
        if not self.store.has_recent_alert(
            thesis.id, AlertType.POSITION_CLOSED, within_hours=24.0
        ):
            self.store.save_alert(alert)
            summary["alerts_fired"] += 1

        # Mark thesis as closed (generic — OutcomeRecorder will refine the status)
        self.store.close_thesis(thesis.id, ThesisStatus.CLOSED)
        summary["positions_closed"] += 1

    # ------------------------------------------------------------------
    # Data fetching (lazy imports for optional deps)
    # ------------------------------------------------------------------

    def _fetch_brokerage_positions(self) -> List[Dict[str, Any]]:
        """Fetch all positions from the brokerage."""
        if not self.executor:
            return []
        try:
            portfolio = self.executor.get_portfolio_summary()
            return portfolio.get("positions", [])
        except Exception as e:
            logger.error(f"Failed to fetch brokerage positions: {e}")
            return []

    def _fetch_current_price(self, ticker: str) -> Optional[float]:
        """
        Fetch the current price for a ticker.

        Priority: Alpaca quote → yfinance fast_info → None
        """
        # Try Alpaca quote first (if executor available)
        if self.executor:
            try:
                quote = self.executor._get_latest_quote(ticker)
                if quote:
                    price = quote.get("ask_price") or quote.get("bid_price")
                    if price and float(price) > 0:
                        return float(price)
            except Exception:
                pass

        # Fallback to yfinance
        try:
            import yfinance as yf

            t = yf.Ticker(ticker)
            # fast_info is the lightweight accessor
            price = getattr(t, "fast_info", {}).get("lastPrice")
            if price and float(price) > 0:
                return float(price)

            # Fallback: last close from history
            hist = t.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.warning(f"yfinance price fetch failed for {ticker}: {e}")

        return None

    def _get_spy_return_since(self, trade_date: Optional[str]) -> Optional[float]:
        """Get SPY return from trade_date to now (cached per tick)."""
        if not trade_date:
            return None

        cache_key = f"spy_since_{trade_date}"
        if cache_key in self._spy_cache:
            return self._spy_cache[cache_key]

        try:
            import yfinance as yf

            spy = yf.Ticker(self.spy_ticker)
            hist = spy.history(start=trade_date)
            if hist.empty or len(hist) < 2:
                return None

            start_price = float(hist["Close"].iloc[0])
            end_price = float(hist["Close"].iloc[-1])
            ret = (end_price - start_price) / start_price
            self._spy_cache[cache_key] = ret
            return ret
        except Exception as e:
            logger.warning(f"SPY return fetch failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value) -> Optional[float]:
    """Convert to float safely."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
