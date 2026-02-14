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
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from tradingagents.agents.journal.models import (
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
from tradingagents.agents.journal.store import JournalStore
from tradingagents.agents.journal.execution_advisor import (
    ActionContext,
    JournalExecutionAdvisor,
)
from tradingagents.agents.journal.execution_policy import (
    JournalExecutionPolicy,
    PolicyResult,
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
    ):
        self.store = store
        self.executor = executor
        self.lesson_memory = lesson_memory
        self.execution_advisor = execution_advisor or JournalExecutionAdvisor.from_env()
        self.execution_policy = execution_policy or JournalExecutionPolicy.from_env()
        self.alert_dedup_hours = alert_dedup_hours
        self.spy_ticker = spy_ticker
        self.semantic_lesson_limit = int(os.getenv("JOURNAL_EXECUTION_SEMANTIC_LESSON_LIMIT", "3") or 3)
        self.ticker_lesson_limit = int(os.getenv("JOURNAL_EXECUTION_TICKER_LESSON_LIMIT", "5") or 5)

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
            self._handle_position_closed(thesis, summary)
            return

        # Get brokerage position data
        pos_data = next(
            (p for p in brokerage_positions if p["symbol"].upper() == ticker),
            None,
        )
        thesis, thesis_execution_eligible, ineligibility_reason = self._normalize_stop_orientation(
            thesis=thesis,
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

    def _normalize_stop_orientation(
        self,
        *,
        thesis: TradeThesis,
    ) -> tuple[TradeThesis, bool, Optional[str]]:
        """
        Enforce valid stop orientation.

        BUY: stop must be below entry.
        SELL: stop must be above entry.
        If invalid, reset stop to 5% directional from entry and persist.
        If entry is missing/invalid, block execution for this thesis and emit alert.
        """
        action = str(thesis.action or "").upper()
        if action not in {"BUY", "SELL"}:
            return thesis, True, None
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
        qty = self._resolve_exec_qty(
            position_qty=position_qty,
            recommended_qty_pct=decision.recommended_qty_pct,
        )
        if qty <= 0:
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal="SELL",
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
                submitted_signal="SELL",
                submitted_qty=qty,
                order_type="MARKET",
                status="dry_run",
                raw_result_json=json.dumps(
                    {
                        "decision_type": decision.decision_type,
                        "reason_code": decision.reason_code,
                        "dry_run": True,
                        "executor_available": self.executor is not None,
                    }
                ),
                created_at=datetime.utcnow().isoformat(),
            )

        try:
            result = self.executor.execute_signal(
                ticker=thesis.ticker.upper(),
                signal="SELL",
                analysis_state={
                    "journal_action_decision_id": decision.id,
                    "journal_decision_type": decision.decision_type,
                    "journal_reason_code": decision.reason_code,
                    "journal_confidence": decision.confidence,
                },
                trade_date=datetime.utcnow().date().isoformat(),
                agent_quantity=int(qty),
                agent_order_type="MARKET",
            )
            executed = bool((result or {}).get("executed"))
            status = "submitted" if executed else "rejected"
            order_id = (result or {}).get("order_id")
            return JournalActionExecution(
                decision_id=decision.id,
                thesis_id=thesis.id,
                ticker=thesis.ticker.upper(),
                submitted_signal="SELL",
                submitted_qty=qty,
                order_type="MARKET",
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
                submitted_signal="SELL",
                submitted_qty=qty,
                order_type="MARKET",
                status="failed",
                error=str(e),
                created_at=datetime.utcnow().isoformat(),
            )

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
    ) -> int:
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
        if thesis.stop_loss and (
            (thesis.action == "BUY" and price <= thesis.stop_loss)
            or (thesis.action == "SELL" and price >= thesis.stop_loss)
        ):
            return ActionReasonCode.STOP_BREACH
        if thesis.target_2 and (
            (thesis.action == "BUY" and price >= thesis.target_2)
            or (thesis.action == "SELL" and price <= thesis.target_2)
        ):
            return ActionReasonCode.TARGET2_REACHED
        if thesis.target_1 and (
            (thesis.action == "BUY" and price >= thesis.target_1)
            or (thesis.action == "SELL" and price <= thesis.target_1)
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
