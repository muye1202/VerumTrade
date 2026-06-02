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

import json
import logging
import os
from datetime import datetime, date, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from opentrace.agents.journal.core.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    AlertType,
    ThesisStatus,
    ActionDecisionType,
    ActionReasonCode,
    JournalActionDecision,
    JournalActionExecution,
)
from opentrace.agents.journal.core.store import JournalStore
from opentrace.agents.journal.execution.execution_advisor import (
    ActionContext,
    JournalExecutionAdvisor,
)
from opentrace.agents.journal.execution.execution_policy import (
    JournalExecutionPolicy,
    PolicyResult,
)
from opentrace.agents.journal.evaluation.decision_plan_evaluator import (
    evaluate_decision_plan,
)
from opentrace.agents.journal.evaluation.news_event_inference import (
    infer_event_flags,
    event_inference_enabled,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
PREMARKET_OPEN = time(8, 0)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
AFTERHOURS_CLOSE = time(20, 0)


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
        lesson_memory: Any = None,
        execution_advisor: Optional[JournalExecutionAdvisor] = None,
        execution_policy: Optional[JournalExecutionPolicy] = None,
        alert_dedup_hours: float = 4.0,
        spy_ticker: str = "SPY",
        smart_evaluator: Any = None,  # Optional[SmartPlanEvaluator]
    ):
        self.store = store
        self.executor = executor
        self.lesson_memory = lesson_memory
        self.execution_advisor = execution_advisor or JournalExecutionAdvisor.from_env()
        self.execution_policy = execution_policy or JournalExecutionPolicy.from_env()
        self.alert_dedup_hours = alert_dedup_hours
        self.spy_ticker = spy_ticker
        self._smart_evaluator = smart_evaluator
        self.semantic_lesson_limit = int(os.getenv("JOURNAL_EXECUTION_SEMANTIC_LESSON_LIMIT", "3") or 3)
        self.ticker_lesson_limit = int(os.getenv("JOURNAL_EXECUTION_TICKER_LESSON_LIMIT", "5") or 5)
        max_rel_spread = _safe_float(os.getenv("JOURNAL_QUOTE_MAX_REL_SPREAD"))
        if max_rel_spread is None:
            max_rel_spread = 0.025
        self.quote_max_rel_spread = min(1.0, max(0.0, float(max_rel_spread)))

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
            "actions_evaluated": 0,
            "actions_recommended": 0,
            "actions_blocked": 0,
            "actions_executed": 0,
            "actions_failed": 0,
            "action_decision_rows": [],
            "errors": [],
            "dedup_closed": 0,
        }

        # Startup-safe cleanup: keep one active thesis per ticker.
        try:
            dedup = self.store.deduplicate_active_theses(executor=self.executor)
            summary["dedup_closed"] = int(dedup.get("closed_theses", 0))
        except Exception as e:
            logger.warning(f"Active thesis deduplication failed: {e}")

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

        # Reconcile pending entry prices from Alpaca order status before monitoring.
        if self._reconcile_pending_entry(thesis):
            return

        # Check if position still exists in brokerage
        if ticker not in brokerage_tickers:
            if self._has_decision_plan(thesis):
                self._monitor_plan_only_thesis(
                    thesis=thesis,
                    summary=summary,
                )
                return
            self._handle_position_closed(thesis, summary)
            return

        # Get brokerage position data
        pos_data = next(
            (p for p in brokerage_positions if p["symbol"].upper() == ticker),
            None,
        )
        thesis, thesis_execution_eligible, ineligibility_reason = self._normalize_stop_orientation(
            thesis=thesis,
            pos_data=pos_data,
        )

        # Fetch current price + quote quality metadata.
        # Execution decisions must be based on brokerage position-derived market price.
        position_price = self._derive_position_price(pos_data)
        quote_data = self._fetch_current_price(ticker)
        current_price = position_price
        if current_price is not None:
            quote_data["price"] = current_price
            quote_data["source"] = "position_derived"

        if current_price is None:
            logger.warning(
                "ABORT EXECUTION for %s: failed to derive current market price from position "
                "(requires valid market_value and qty).",
                ticker,
            )
            return

        # Build and save snapshot
        snapshot = self._build_snapshot(
            thesis,
            pos_data,
            current_price,
            bid=_safe_float(quote_data.get("bid")),
            ask=_safe_float(quote_data.get("ask")),
        )
        self.store.save_snapshot(snapshot)
        summary["snapshots_taken"] += 1

        market_session = self._get_market_session()
        if bool(quote_data.get("unreliable_quote")):
            logger.info(
                "Skipping alerts/actions for %s due to unreliable quote "
                "(session=%s source=%s rel_spread=%.4f threshold=%.4f)",
                ticker,
                market_session,
                quote_data.get("source") or "unknown",
                float(quote_data.get("relative_spread") or 0.0),
                self.quote_max_rel_spread,
            )
            return

        # Check thesis parameters and fire alerts
        alerts = self._check_thesis_parameters(thesis, snapshot, current_price, pos_data=pos_data)
        persisted_alerts: List[JournalAlert] = []
        for alert in alerts:
            # Dedup: don't fire the same alert type repeatedly
            if not self.store.has_recent_alert(
                thesis.id, AlertType(alert.alert_type), self.alert_dedup_hours
            ):
                self.store.save_alert(alert)
                summary["alerts_fired"] += 1
                persisted_alerts.append(alert)

        self._evaluate_action_pipeline(
            thesis=thesis,
            snapshot=snapshot,
            pos_data=pos_data,
            recent_alerts=persisted_alerts,
            thesis_execution_eligible=thesis_execution_eligible,
            ineligibility_reason=ineligibility_reason,
            summary=summary,
        )

    def _monitor_plan_only_thesis(
        self,
        *,
        thesis: TradeThesis,
        summary: Dict[str, Any],
    ) -> None:
        """
        Monitor conditional-entry theses even when no brokerage position exists.
        """
        ticker = thesis.ticker.upper()
        quote_data = self._fetch_current_price(ticker)
        current_price = _safe_float(quote_data.get("price"))
        if current_price is None:
            current_price = _safe_float(quote_data.get("ask")) or _safe_float(quote_data.get("bid"))
        if current_price is None:
            logger.info("Plan-only thesis %s skipped: no quote price available", ticker)
            return

        snapshot = self._build_snapshot(
            thesis=thesis,
            pos_data=None,
            current_price=current_price,
            bid=_safe_float(quote_data.get("bid")),
            ask=_safe_float(quote_data.get("ask")),
        )
        self.store.save_snapshot(snapshot)
        summary["snapshots_taken"] += 1

        self._evaluate_action_pipeline(
            thesis=thesis,
            snapshot=snapshot,
            pos_data=None,
            recent_alerts=[],
            thesis_execution_eligible=True,
            ineligibility_reason=None,
            summary=summary,
        )

    def _has_decision_plan(self, thesis: TradeThesis) -> bool:
        return bool(getattr(thesis, "decision_plan_json", None))

    def _resolve_effective_side(
        self,
        *,
        thesis: TradeThesis,
        pos_data: Optional[Dict[str, Any]] = None,
        snapshot: Optional[PositionSnapshot] = None,
    ) -> str:
        """Resolve trade side used for risk math when thesis.action is not BUY/SELL."""
        action = str(thesis.action or "").upper()
        if action in {"BUY", "SELL"}:
            return action

        qty = None
        if pos_data is not None:
            qty = _safe_float(pos_data.get("qty"))
        if qty is None and snapshot is not None:
            qty = _safe_float(snapshot.quantity)

        if qty is not None:
            return "SELL" if qty < 0 else "BUY"

        logger.debug(
            "Could not infer side for %s thesis %s (action=%s); defaulting to BUY",
            thesis.ticker,
            thesis.id,
            action or "UNKNOWN",
        )
        return "BUY"

    # ------------------------------------------------------------------
    # Snapshot building
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        thesis: TradeThesis,
        pos_data: Optional[Dict[str, Any]],
        current_price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> PositionSnapshot:
        """Build a PositionSnapshot from thesis + live data."""
        side = self._resolve_effective_side(thesis=thesis, pos_data=pos_data)
        snap = PositionSnapshot(
            thesis_id=thesis.id,
            ticker=thesis.ticker.upper(),
            current_price=current_price,
            bid=bid,
            ask=ask,
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
            if side == "BUY":
                snap.distance_to_stop_pct = round(
                    (current_price - thesis.stop_loss) / current_price * 100, 2
                )
            else:  # SELL/short
                snap.distance_to_stop_pct = round(
                    (thesis.stop_loss - current_price) / current_price * 100, 2
                )

        # Distance to target 1
        if thesis.target_1 and current_price:
            if side == "BUY":
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
            if side == "SELL":
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
        pos_data: Optional[Dict[str, Any]] = None,
    ) -> List[JournalAlert]:
        """Check all thesis parameters and return any alerts that should fire."""
        alerts: List[JournalAlert] = []
        side = self._resolve_effective_side(
            thesis=thesis,
            pos_data=pos_data,
            snapshot=snapshot,
        )
        market_session = self._get_market_session()
        illiquid = market_session in {"weekend", "overnight", "afterhours"}
        # --- Stop-loss check ---
        if thesis.stop_loss:
            stop_breached = False
            if side == "BUY" and current_price <= thesis.stop_loss:
                stop_breached = True
            elif side == "SELL" and current_price >= thesis.stop_loss:
                stop_breached = True
            if stop_breached:
                if illiquid:
                    severity = "info"
                    message = (
                        f"STOP LEVEL TOUCHED ({market_session}): {thesis.ticker} at "
                        f"${current_price:.2f} touched stop ${thesis.stop_loss:.2f} during "
                        "an illiquid session. Action deferred until liquid market hours."
                    )
                    action_recommended = (
                        "Monitor-only event: wait for a liquid session confirmation."
                    )
                else:
                    severity = "critical"
                    message = (
                        f"STOP-LOSS BREACHED: {thesis.ticker} at ${current_price:.2f} "
                        f"has {'fallen below' if side == 'BUY' else 'risen above'} "
                        f"stop-loss at ${thesis.stop_loss:.2f}"
                    )
                    action_recommended = "EXIT POSITION - stop-loss hit per original thesis"
                alerts.append(
                    JournalAlert(
                        thesis_id=thesis.id,
                        ticker=thesis.ticker,
                        alert_type=AlertType.STOP_HIT.value,
                        severity=severity,
                        message=message,
                        trigger_price=current_price,
                        threshold_price=thesis.stop_loss,
                        current_price=current_price,
                        unrealized_pl_pct=snapshot.unrealized_pl_pct,
                        holding_days=snapshot.holding_days_elapsed,
                        action_recommended=action_recommended,
                    )
                )
        # --- Target checks ---
        for target_num, target_price in [
            (1, thesis.target_1),
            (2, thesis.target_2),
        ]:
            if target_price:
                target_reached = False
                if side == "BUY" and current_price >= target_price:
                    target_reached = True
                elif side == "SELL" and current_price <= target_price:
                    target_reached = True
                if target_reached:
                    if illiquid:
                        severity = "info"
                        message = (
                            f"TARGET {target_num} TOUCHED ({market_session}): "
                            f"{thesis.ticker} at ${current_price:.2f} touched target "
                            f"${target_price:.2f} during an illiquid session."
                        )
                        action_recommended = (
                            "Monitor-only event: wait for a liquid session confirmation."
                        )
                    else:
                        severity = "warning" if target_num == 1 else "info"
                        message = (
                            f"TARGET {target_num} REACHED: {thesis.ticker} at "
                            f"${current_price:.2f} hit target ${target_price:.2f}"
                        )
                        action_recommended = (
                            f"Consider taking partial profits (target {target_num})"
                            if target_num == 1
                            else "Full target reached - consider closing position"
                        )
                    alerts.append(
                        JournalAlert(
                            thesis_id=thesis.id,
                            ticker=thesis.ticker,
                            alert_type=AlertType.TARGET_HIT.value,
                            severity=severity,
                            message=message,
                            trigger_price=current_price,
                            threshold_price=target_price,
                            current_price=current_price,
                            unrealized_pl_pct=snapshot.unrealized_pl_pct,
                            holding_days=snapshot.holding_days_elapsed,
                            action_recommended=action_recommended,
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
                                "Review position - holding period expired. "
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
                            f"last check (${last_snap.current_price:.2f} -> ${current_price:.2f})"
                        ),
                        trigger_price=current_price,
                        threshold_price=last_snap.current_price,
                        current_price=current_price,
                        unrealized_pl_pct=snapshot.unrealized_pl_pct,
                        holding_days=snapshot.holding_days_elapsed,
                        action_recommended="Review thesis - large price movement detected",
                    )
                )
        return alerts

    def _normalize_stop_orientation(
        self,
        *,
        thesis: TradeThesis,
        pos_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[TradeThesis, bool, Optional[str]]:
        """
        Enforce valid stop orientation.

        BUY: stop must be below entry.
        SELL: stop must be above entry.
        If invalid, reset stop to 5% directional from entry and persist.
        If entry is missing/invalid, block execution for this thesis and emit alert.
        """
        action = self._resolve_effective_side(thesis=thesis, pos_data=pos_data)
        if thesis.stop_loss is None:
            return thesis, True, None

        entry = _safe_float(thesis.entry_price)
        stop = _safe_float(thesis.stop_loss)
        if stop is None:
            return thesis, True, None

        if entry is None or entry <= 0:
            marker = "[STOP_ORIENTATION_BLOCKED_NO_ENTRY]"
            if not self._has_recent_custom_marker(thesis.id, marker, within_hours=24.0):
                self.store.save_alert(
                    JournalAlert(
                        thesis_id=thesis.id,
                        ticker=thesis.ticker,
                        alert_type=AlertType.CUSTOM.value,
                        severity="critical",
                        message=(
                            f"{marker} Cannot validate/fix stop orientation for {thesis.ticker}: "
                            "entry_price is missing or invalid. Execution is blocked for this thesis."
                        ),
                        action_recommended="Set a valid entry_price and stop_loss orientation.",
                    )
                )
            return thesis, False, "invalid_stop_no_entry"

        invalid = (action == "BUY" and stop >= entry) or (action == "SELL" and stop <= entry)
        if not invalid:
            return thesis, True, None

        corrected = round(entry * (0.95 if action == "BUY" else 1.05), 6)
        # Idempotence: if already corrected (or very close), skip duplicate writes/alerts.
        if abs(stop - corrected) <= 1e-6:
            return thesis, True, None

        old = thesis.stop_loss
        thesis.stop_loss = corrected
        self.store.save_thesis(thesis)

        marker = "[STOP_ORIENTATION_AUTOCORRECTED]"
        if not self._has_recent_custom_marker(thesis.id, marker, within_hours=24.0):
            self.store.save_alert(
                JournalAlert(
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    alert_type=AlertType.CUSTOM.value,
                    severity="warning",
                    message=(
                        f"{marker} {thesis.ticker} {action} stop was invalid (old={old}, entry={entry}). "
                        f"Replaced with 5% directional stop={corrected}."
                    ),
                    action_taken=f"Persisted corrected stop_loss={corrected}",
                    action_recommended="Review thesis risk parameters.",
                )
            )
        return thesis, True, None

    def _has_recent_custom_marker(
        self,
        thesis_id: str,
        marker: str,
        within_hours: float = 24.0,
    ) -> bool:
        alerts = self.store.get_alerts(thesis_id=thesis_id, unacknowledged_only=False, limit=100)
        if not alerts:
            return False
        cutoff = datetime.utcnow().timestamp() - (within_hours * 3600.0)
        marker_l = str(marker or "").lower()
        for a in alerts:
            msg = str(a.message or "").lower()
            if marker_l not in msg:
                continue
            try:
                ts = datetime.fromisoformat(str(a.timestamp).replace("Z", "+00:00"))
                if ts.tzinfo is not None:
                    ts = ts.astimezone().replace(tzinfo=None)
                if ts.timestamp() > cutoff:
                    return True
            except Exception:
                continue
        return False

    def _evaluate_action_pipeline(
        self,
        *,
        thesis: TradeThesis,
        snapshot: PositionSnapshot,
        pos_data: Optional[Dict[str, Any]],
        recent_alerts: List[JournalAlert],
        thesis_execution_eligible: bool,
        ineligibility_reason: Optional[str],
        summary: Dict[str, Any],
    ) -> None:
        """Evaluate deterministic action rules and optionally execute guarded actions."""
        summary["actions_evaluated"] += 1

        plan_decision = self._build_plan_trigger_decision(
            thesis=thesis,
            snapshot=snapshot,
            market_session=self._get_market_session(),
        )
        if plan_decision is not None:
            self._process_action_decision(
                thesis=thesis,
                decision=plan_decision,
                pos_data=pos_data,
                recent_alerts=recent_alerts,
                market_session=self._get_market_session(),
                thesis_execution_eligible=thesis_execution_eligible,
                ineligibility_reason=ineligibility_reason,
                summary=summary,
            )
            return

        ticker_lessons, semantic_lessons, memory_unavailable = self._fetch_memory_context(
            thesis=thesis,
            snapshot=snapshot,
        )
        market_session = self._get_market_session()

        context = ActionContext(
            thesis=thesis,
            snapshot=snapshot,
            market_session=market_session,
            recent_alerts=[a.to_dict() for a in recent_alerts],
            ticker_lessons=ticker_lessons,
            semantic_lessons=semantic_lessons,
            memory_unavailable=memory_unavailable,
        )

        cooldown_hit = self.store.has_recent_action_decision(
            thesis.id,
            self._reason_code_for_snapshot(thesis=thesis, snapshot=snapshot),
            within_minutes=self.execution_policy.cooldown_minutes,
        )

        decision = self.execution_advisor.evaluate(
            context=context,
            cooldown_hit=cooldown_hit,
        )
        if memory_unavailable:
            decision.context_summary = self._annotate_context_summary(
                decision.context_summary, "memory_unavailable", True
            )

        self._process_action_decision(
            thesis=thesis,
            decision=decision,
            pos_data=pos_data,
            recent_alerts=recent_alerts,
            market_session=market_session,
            thesis_execution_eligible=thesis_execution_eligible,
            ineligibility_reason=ineligibility_reason,
            summary=summary,
        )

    def _build_plan_trigger_decision(
        self,
        *,
        thesis: TradeThesis,
        snapshot: PositionSnapshot,
        market_session: str,
    ) -> Optional[JournalActionDecision]:
        required_event_keys = self._collect_required_event_keys(thesis)
        manual_flags = self.store.get_event_confirmations(
            ticker=thesis.ticker.upper(),
            as_of_ts=snapshot.timestamp,
            sources=["manual", "agent"],
        )
        inferred_flags: Dict[str, Any] = {}
        missing_event_keys = [k for k in required_event_keys if k not in manual_flags]
        if missing_event_keys and event_inference_enabled():
            inferred_flags = infer_event_flags(
                thesis=thesis,
                event_keys=missing_event_keys,
            )
            for key, val in inferred_flags.items():
                try:
                    self.store.save_event_confirmation(
                        ticker=thesis.ticker.upper(),
                        event_key=key,
                        value=val,
                        source="inferred",
                        confidence=1.0,
                        timestamp=snapshot.timestamp,
                    )
                except Exception:
                    pass
        merged_confirmations = dict(inferred_flags)
        merged_confirmations.update(manual_flags)

        matched = self._eval_plan(
            thesis,
            snapshot,
            market_session,
            volume_ratio=None,
            event_confirmations=merged_confirmations,
        )
        if not matched:
            return None
        template = matched.get("action_template") or {}
        action = str(template.get("action", "HOLD")).strip().upper()
        if action == "BUY":
            decision_type = ActionDecisionType.ENTER_POSITION.value
            qty_pct = _safe_float(template.get("position_size_pct"))
        elif action == "SELL":
            decision_type = ActionDecisionType.EXIT_POSITION.value
            qty_pct = 1.0
        else:
            return None

        context_summary = {
            "source": "decision_plan_v2",
            "matched_branch_id": matched.get("branch_id"),
            "market_session": market_session,
            "action_template": template,
            "plan_summary": matched.get("summary") or {},
            "event_confirmations_manual": manual_flags,
            "event_confirmations_inferred": inferred_flags,
            "event_confirmation_used": merged_confirmations,
        }
        return JournalActionDecision(
            thesis_id=thesis.id,
            ticker=thesis.ticker.upper(),
            tick_timestamp=snapshot.timestamp,
            decision_type=decision_type,
            reason_code=ActionReasonCode.PLAN_TRIGGER.value,
            confidence=85.0,
            recommended_qty_pct=qty_pct,
            dry_run=True,
            gates_passed=False,
            gate_block_reasons=json.dumps([]),
            context_summary=json.dumps(context_summary),
            linked_alert_ids=json.dumps([]),
            created_at=datetime.utcnow().isoformat(),
        )

    def _eval_plan(
        self,
        thesis,
        snapshot,
        market_session,
        volume_ratio=None,
        event_confirmations=None,
    ):
        """Dispatch to SmartPlanEvaluator if available, else fall back to evaluate_decision_plan."""
        if self._smart_evaluator:
            has_pos = False
            if self.executor:
                try:
                    has_pos = any(
                        p.symbol == thesis.ticker
                        for p in self.executor.get_positions()
                    )
                except Exception:
                    pass
            return self._smart_evaluator.evaluate(
                thesis=thesis,
                snapshot=snapshot,
                market_session=market_session,
                volume_ratio=volume_ratio,
                has_position=has_pos,
            )
        return evaluate_decision_plan(
            thesis=thesis,
            snapshot=snapshot,
            market_session=market_session,
            volume_ratio=volume_ratio,
            event_confirmations=event_confirmations,
        )

    def _collect_required_event_keys(self, thesis: TradeThesis) -> List[str]:
        raw = getattr(thesis, "decision_plan_json", None)
        if not raw:
            return []
        try:
            plan = json.loads(str(raw))
        except Exception:
            return []
        execution_plan = plan.get("execution_plan") or []
        if not isinstance(execution_plan, list):
            return []
        keys: List[str] = []
        for branch in execution_plan:
            if not isinstance(branch, dict):
                continue
            conditions = branch.get("conditions") or {}
            ev = conditions.get("event_conditions")
            if ev is None:
                ev = branch.get("event_conditions") or []
            if not isinstance(ev, list):
                continue
            for item in ev:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("event_key") or "").strip()
                if key and key not in keys:
                    keys.append(key)
        return keys

    def _process_action_decision(
        self,
        *,
        thesis: TradeThesis,
        decision: JournalActionDecision,
        pos_data: Optional[Dict[str, Any]],
        recent_alerts: List[JournalAlert],
        market_session: str,
        thesis_execution_eligible: bool,
        ineligibility_reason: Optional[str],
        summary: Dict[str, Any],
    ) -> None:
        is_actionable = self.execution_advisor.is_actionable(decision)
        if is_actionable:
            summary["actions_recommended"] += 1

        position_qty = _safe_float((pos_data or {}).get("qty")) if pos_data else None
        policy_result = self.execution_policy.evaluate(
            thesis=thesis,
            decision=decision,
            store=self.store,
            position_qty=position_qty,
            market_session=market_session,
            thesis_execution_eligible=thesis_execution_eligible,
            ineligibility_reason=ineligibility_reason,
        )
        decision.dry_run = bool(policy_result.dry_run)
        decision.gates_passed = bool(policy_result.allowed)
        decision.gate_block_reasons = json.dumps(policy_result.block_reasons)
        self.store.save_action_decision(decision)

        if not is_actionable:
            return

        row = {
            "ticker": thesis.ticker.upper(),
            "decision_type": decision.decision_type,
            "reason_code": decision.reason_code,
            "confidence": round(float(decision.confidence or 0.0), 1),
            "gates_passed": bool(policy_result.allowed),
            "block_reasons": list(policy_result.block_reasons),
            "recommended_qty_pct": decision.recommended_qty_pct,
            "execution_status": "blocked",
        }
        try:
            ctx = json.loads(decision.context_summary or "{}")
            if isinstance(ctx, dict):
                row["matched_branch_id"] = ctx.get("matched_branch_id")
                manual = ctx.get("event_confirmations_manual") or {}
                inferred = ctx.get("event_confirmations_inferred") or {}
                row["event_confirmation_source"] = (
                    "manual_or_agent" if manual else ("inferred" if inferred else None)
                )
        except Exception:
            pass

        if not policy_result.allowed:
            summary["actions_blocked"] += 1
            summary.setdefault("action_decision_rows", []).append(row)
            return

        execution = self._execute_decision(
            thesis=thesis,
            decision=decision,
            position_qty=position_qty,
            dry_run=policy_result.dry_run,
        )
        self.store.save_action_execution(execution)
        row["execution_status"] = execution.status
        summary.setdefault("action_decision_rows", []).append(row)

        if execution.status in {"dry_run", "submitted"}:
            summary["actions_executed"] += 1
            self._annotate_alert_action_taken(
                alerts=recent_alerts,
                action_note=f"journal_action:{decision.decision_type}:{execution.status}",
            )
        else:
            summary["actions_failed"] += 1
            self._create_execution_failure_alert(thesis=thesis, execution=execution)

    def _execute_decision(
        self,
        *,
        thesis: TradeThesis,
        decision: JournalActionDecision,
        position_qty: Optional[float],
        dry_run: bool,
    ) -> JournalActionExecution:
        """Execute a decision through AlpacaExecutor or produce a dry-run artifact."""
        signal = "BUY" if decision.decision_type == ActionDecisionType.ENTER_POSITION.value else "SELL"
        action_template = self._extract_action_template(decision)
        qty = self._resolve_exec_qty(
            position_qty=position_qty,
            recommended_qty_pct=decision.recommended_qty_pct,
            signal=signal,
            explicit_quantity=_safe_float((action_template or {}).get("quantity")),
        )
        if signal == "SELL" and qty <= 0:
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal=signal,
                submitted_qty=0,
                status="rejected",
                error="resolved_qty_is_zero",
                created_at=datetime.utcnow().isoformat(),
            )

        if dry_run or self.executor is None:
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal=signal,
                submitted_qty=qty,
                order_type=(action_template or {}).get("order_type") or "MARKET",
                status="dry_run",
                raw_result_json=json.dumps(
                    {
                        "decision_type": decision.decision_type,
                        "reason_code": decision.reason_code,
                        "dry_run": True,
                        "executor_available": self.executor is not None,
                        "signal": signal,
                        "action_template": action_template,
                    }
                ),
                created_at=datetime.utcnow().isoformat(),
            )

        try:
            result = self.executor.execute_signal(
                ticker=thesis.ticker.upper(),
                signal=signal,
                analysis_state={
                    "journal_action_decision_id": decision.id,
                    "journal_decision_type": decision.decision_type,
                    "journal_reason_code": decision.reason_code,
                    "journal_confidence": decision.confidence,
                },
                trade_date=datetime.utcnow().date().isoformat(),
                agent_quantity=int(qty) if qty > 0 else None,
                agent_order_type=(action_template or {}).get("order_type") or "MARKET",
                agent_time_in_force=(action_template or {}).get("time_in_force"),
                agent_limit_price=_safe_float((action_template or {}).get("limit_price")),
                agent_stop_price=_safe_float((action_template or {}).get("stop_price")),
                agent_trail_percent=_safe_float((action_template or {}).get("trail_percent")),
                agent_trail_price=_safe_float((action_template or {}).get("trail_price")),
                agent_position_size_pct=_safe_float((action_template or {}).get("position_size_pct")),
            )
            executed = bool((result or {}).get("executed"))
            status = "submitted" if executed else "rejected"
            order_id = (result or {}).get("order_id")
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal=signal,
                submitted_qty=qty,
                order_type=(action_template or {}).get("order_type") or "MARKET",
                status=status,
                broker_order_id=str(order_id) if order_id else None,
                error=None if executed else str((result or {}).get("reason") or "execution_rejected"),
                raw_result_json=json.dumps(result or {}),
                created_at=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal=signal,
                submitted_qty=qty,
                order_type=(action_template or {}).get("order_type") or "MARKET",
                status="failed",
                error=str(e),
                created_at=datetime.utcnow().isoformat(),
            )

    def _extract_action_template(self, decision: JournalActionDecision) -> Optional[Dict[str, Any]]:
        if not decision.context_summary:
            return None
        try:
            payload = json.loads(decision.context_summary)
        except Exception:
            return None
        template = payload.get("action_template")
        if isinstance(template, dict):
            return template
        return None

    def _fetch_memory_context(
        self,
        *,
        thesis: TradeThesis,
        snapshot: PositionSnapshot,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        """Return (ticker_lessons, semantic_lessons, memory_unavailable)."""
        if self.lesson_memory is None:
            return [], [], False

        try:
            ticker_lessons = self.lesson_memory.get_lessons_by_ticker(
                thesis.ticker.upper(),
                limit=self.ticker_lesson_limit,
            )
            query = (
                f"{thesis.ticker} {thesis.action} regime={thesis.regime or 'unknown'} "
                f"pl={snapshot.unrealized_pl_pct or 0:.2f} rs={snapshot.relative_strength or 0:.2f}"
            )
            semantic_lessons = self.lesson_memory.query_similar(
                query,
                n_results=self.semantic_lesson_limit,
            )
            return ticker_lessons or [], semantic_lessons or [], False
        except Exception as e:
            logger.warning("Lesson memory unavailable for %s: %s", thesis.ticker, e)
            return [], [], True

    def _resolve_exec_qty(
        self,
        *,
        position_qty: Optional[float],
        recommended_qty_pct: Optional[float],
        signal: str,
        explicit_quantity: Optional[float] = None,
    ) -> int:
        if explicit_quantity is not None:
            q = int(round(abs(float(explicit_quantity))))
            return max(0, q)
        if str(signal).upper() == "BUY":
            return 0
        qty = abs(_safe_float(position_qty) or 0.0)
        if qty <= 0:
            return 0
        pct = _safe_float(recommended_qty_pct) or 1.0
        pct = min(1.0, max(0.01, pct))
        resolved = int(round(qty * pct))
        return max(1, resolved)

    def _annotate_context_summary(
        self,
        raw_json: Optional[str],
        key: str,
        value: Any,
    ) -> str:
        payload: Dict[str, Any]
        try:
            payload = json.loads(raw_json or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        payload[key] = value
        return json.dumps(payload)

    def _create_execution_failure_alert(
        self,
        *,
        thesis: TradeThesis,
        execution: JournalActionExecution,
    ) -> None:
        alert = JournalAlert(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            alert_type=AlertType.CUSTOM.value,
            severity="critical",
            message=(
                f"JOURNAL EXECUTION FAILED: {thesis.ticker} action submission failed "
                f"({execution.status})"
            ),
            action_taken=f"Execution failure: {execution.error or 'unknown_error'}",
        )
        self.store.save_alert(alert)

    def _annotate_alert_action_taken(self, *, alerts: List[JournalAlert], action_note: str) -> None:
        for alert in alerts:
            self.store.update_alert_action_taken(alert.id, action_note)

    def _reason_code_for_snapshot(self, *, thesis: TradeThesis, snapshot: PositionSnapshot):
        price = _safe_float(snapshot.current_price)
        if price is None:
            return ActionReasonCode.NONE
        side = self._resolve_effective_side(thesis=thesis, snapshot=snapshot)
        if thesis.stop_loss and (
            (side == "BUY" and price <= thesis.stop_loss)
            or (side == "SELL" and price >= thesis.stop_loss)
        ):
            return ActionReasonCode.STOP_BREACH
        if thesis.target_2 and (
            (side == "BUY" and price >= thesis.target_2)
            or (side == "SELL" and price <= thesis.target_2)
        ):
            return ActionReasonCode.TARGET2_REACHED
        if thesis.target_1 and (
            (side == "BUY" and price >= thesis.target_1)
            or (side == "SELL" and price <= thesis.target_1)
        ):
            return ActionReasonCode.TARGET1_REACHED
        if thesis.time_stop_date:
            try:
                stop = datetime.strptime(thesis.time_stop_date, "%Y-%m-%d")
                if datetime.utcnow() >= stop:
                    return ActionReasonCode.TIME_STOP_EXPIRED
            except ValueError:
                pass
        return ActionReasonCode.NONE

    def _reconcile_pending_entry(self, thesis: TradeThesis) -> bool:
        """Update pending entry prices using order status (if available). Returns True if thesis was closed."""
        if not thesis.entry_price_pending or not thesis.order_id or not self.executor:
            return False

        order: Optional[Dict[str, Any]] = None
        try:
            get_status = getattr(self.executor, "get_order_status", None)
            if callable(get_status):
                order = get_status(thesis.order_id)
            elif getattr(self.executor, "trading_client", None) is not None:
                raw_order = self.executor.trading_client.get_order_by_id(thesis.order_id)
                if hasattr(self.executor, "_order_to_dict"):
                    order = self.executor._order_to_dict(raw_order)
        except Exception as e:
            logger.warning(
                "Order reconciliation failed for %s (%s): %s",
                thesis.ticker,
                thesis.order_id,
                e,
            )
            return False

        if not order:
            return False

        status = str(order.get("status") or "").upper()
        filled_avg = _safe_float(order.get("filled_avg_price"))
        filled = status == "FILLED" or (filled_avg is not None and filled_avg > 0)
        now_iso = datetime.utcnow().isoformat()

        if filled and filled_avg is not None and filled_avg > 0:
            thesis.entry_price = float(filled_avg)
            thesis.entry_price_source = "filled_avg_price"
            thesis.entry_price_pending = False
            thesis.last_reconciled_at = now_iso
            self.store.save_thesis(thesis)
            logger.info(
                "Reconciled entry for %s thesis %s to filled_avg_price=%s",
                thesis.ticker,
                thesis.id,
                thesis.entry_price,
            )
            return False

        if status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
            # If order died and no open position exists, close thesis.
            try:
                pos = self.store.get_open_position_for_ticker(self.executor, thesis.ticker)
            except Exception:
                pos = None
            if pos is None:
                self.store.close_thesis(thesis.id, ThesisStatus.CLOSED)
                logger.info(
                    "Closed pending thesis %s for %s after terminal order status %s",
                    thesis.id,
                    thesis.ticker,
                    status,
                )
                return True

        thesis.last_reconciled_at = now_iso
        self.store.save_thesis(thesis)
        return False

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

    def _get_market_session(self) -> str:
        """Best-effort market session classification used by execution policy."""
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return "weekend"
        t = now_et.time()
        if t < PREMARKET_OPEN:
            return "overnight"
        if t < MARKET_OPEN:
            return "premarket"
        if t < MARKET_CLOSE:
            return "market_hours"
        if t < AFTERHOURS_CLOSE:
            return "afterhours"
        return "overnight"

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

    def _derive_position_price(self, pos_data: Optional[Dict[str, Any]]) -> Optional[float]:
        """Estimate mark price from brokerage position fields."""
        if not pos_data:
            return None
        qty = _safe_float(pos_data.get("qty"))
        market_value = _safe_float(pos_data.get("market_value"))
        if qty is None or market_value is None:
            return None
        if abs(qty) <= 0:
            return None
        price = abs(market_value) / abs(qty)
        if price <= 0:
            return None
        return float(price)

    def _fetch_current_price(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch quote metadata for a ticker.
        This does not provide the execution-decision price; that must come from
        position-derived market price (market_value / qty).
        """
        out: Dict[str, Any] = {
            "price": None,
            "bid": None,
            "ask": None,
            "relative_spread": None,
            "unreliable_quote": False,
            "source": "none",
        }
        # Try Alpaca quote first (if executor available)
        if self.executor:
            try:
                quote = self.executor._get_latest_quote(ticker)
                if quote:
                    bid = _safe_float(quote.get("bid_price"))
                    ask = _safe_float(quote.get("ask_price"))
                    out["bid"] = bid
                    out["ask"] = ask
                    rel_spread = _relative_spread(bid=bid, ask=ask)
                    out["relative_spread"] = rel_spread
                    if rel_spread is not None and rel_spread > self.quote_max_rel_spread:
                        out["unreliable_quote"] = True
                    out["source"] = "alpaca_quote"
                    return out
            except Exception:
                pass

        return out

    def _get_spy_return_since(self, trade_date: Optional[str]) -> Optional[float]:
        """Get SPY return from trade_date to now (cached per tick)."""
        if not trade_date:
            return None

        cache_key = f"spy_since_{trade_date}"
        if cache_key in self._spy_cache:
            return self._spy_cache[cache_key]

        try:
            import yfinance as yf
            start_date = date.fromisoformat(trade_date)
            today_et = datetime.now(ET).date()
            if start_date > today_et:
                # Guard against future-dated theses (e.g., local timezone rollover).
                logger.info(
                    "Skipping SPY return for future trade_date=%s (today_et=%s)",
                    trade_date,
                    today_et.isoformat(),
                )
                return None

            spy = yf.Ticker(self.spy_ticker)
            hist = spy.history(
                start=start_date.isoformat(),
                end=(today_et + timedelta(days=1)).isoformat(),
            )
            if hist.empty:
                return None
            elif len(hist) == 1:
                # If only one day, use Open to Close for that day
                start_price = float(hist["Open"].iloc[0])
                end_price = float(hist["Close"].iloc[0])
            else:
                start_price = float(hist["Close"].iloc[0])
                end_price = float(hist["Close"].iloc[-1])

            if start_price <= 0:
                return None
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


def _relative_spread(*, bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Return (ask-bid)/mid when both quote sides are valid."""
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid
