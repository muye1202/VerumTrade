"""
Journal Scheduler — the daemon that wakes the PositionMonitor periodically.

Uses APScheduler (lightweight, no external broker). Runs as:
- A background thread within the CLI process, OR
- A standalone daemon via `python -m tradingagents.journal.scheduler`

Scheduling logic:
- During US market hours (9:30-16:00 ET, Mon-Fri): every N minutes (default 15)
- Pre-market (8:00-9:30 ET): every 30 minutes
- After-hours (16:00-20:00 ET): every 30 minutes
- Overnight / weekends: every 2 hours (just to catch position closures)

After each monitoring tick, runs the OutcomeRecorder to compute outcomes
for any newly closed positions.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from tradingagents.agents.journal.store import JournalStore
from tradingagents.agents.journal.monitor import PositionMonitor
from tradingagents.agents.journal.outcome import OutcomeRecorder
from tradingagents.agents.journal.models import TradeThesis, TradeOutcome

logger = logging.getLogger(__name__)

# US Eastern timezone for market hours
ET = ZoneInfo("America/New_York")

# Market session boundaries (Eastern Time)
PREMARKET_OPEN = time(8, 0)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
AFTERHOURS_CLOSE = time(20, 0)


class JournalScheduler:
    """
    Manages the periodic monitoring schedule.

    Can run in two modes:
    1. Background thread (call start_background())
    2. Foreground blocking (call run_forever())

    Uses APScheduler if available, falls back to a simple threading.Timer loop.
    """

    def __init__(
        self,
        store: JournalStore,
        executor: Any = None,  # AlpacaExecutor
        market_interval_minutes: int = 15,
        off_hours_interval_minutes: int = 120,
        extended_hours_interval_minutes: int = 30,
        on_tick_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_alert: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_outcome_recorded: Optional[Callable[["TradeThesis", "TradeOutcome"], None]] = None,
    ):
        """
        Args:
            store: JournalStore instance
            executor: AlpacaExecutor for brokerage data
            market_interval_minutes: How often to check during market hours
            off_hours_interval_minutes: How often to check overnight/weekends
            extended_hours_interval_minutes: How often during pre/post market
            on_tick_complete: Callback fired after each tick with summary dict
            on_alert: Callback fired for each new alert
            on_outcome_recorded: Callback fired for each newly recorded outcome (thesis, outcome)
        """
        self.store = store
        self.monitor = PositionMonitor(store=store, executor=executor)
        self.outcome_recorder = OutcomeRecorder(store=store)

        self.market_interval = market_interval_minutes * 60  # Convert to seconds
        self.off_hours_interval = off_hours_interval_minutes * 60
        self.extended_hours_interval = extended_hours_interval_minutes * 60

        self.on_tick_complete = on_tick_complete
        self.on_alert = on_alert
        self.on_outcome_recorded = on_outcome_recorded

        self._running = False
        self._timer: Optional[threading.Timer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Tick history (last N summaries for the CLI dashboard)
        self._tick_history: List[Dict[str, Any]] = []
        self._max_history = 100

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_background(self) -> None:
        """Start the scheduler in a background daemon thread."""
        if self._running:
            logger.warning("Scheduler is already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="journal-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("Journal scheduler started (background thread)")

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        logger.info("Journal scheduler stopped")

    def run_forever(self) -> None:
        """
        Run the scheduler in the foreground (blocking).

        Handles SIGINT/SIGTERM for graceful shutdown.
        """
        self._running = True

        def _shutdown(signum, frame):
            logger.info(f"Received signal {signum}, shutting down scheduler...")
            self.stop()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        logger.info("Journal scheduler started (foreground mode)")
        self._run_loop()

    def run_once(self) -> Dict[str, Any]:
        """
        Run a single monitoring tick immediately.

        Useful for CLI commands like `journal check-now`.
        Returns the tick summary.
        """
        return self._execute_tick()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def tick_history(self) -> List[Dict[str, Any]]:
        return list(self._tick_history)

    @property
    def last_tick(self) -> Optional[Dict[str, Any]]:
        return self._tick_history[-1] if self._tick_history else None

    # ------------------------------------------------------------------
    # Internal scheduling loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main scheduling loop — executes tick, sleeps, repeats."""
        while self._running:
            # Execute the tick
            summary = self._execute_tick()

            # Determine next interval
            interval = self._get_current_interval()
            session = self._get_market_session()

            logger.info(
                f"Next tick in {interval // 60}m (session: {session})"
            )

            # Sleep using a cancellable timer
            with self._lock:
                self._timer = threading.Timer(interval, lambda: None)
                self._timer.start()

            # Wait for timer or cancellation
            if self._timer:
                self._timer.join()

    def _execute_tick(self) -> Dict[str, Any]:
        """Run one complete monitoring + outcome-recording cycle."""
        tick_start = datetime.utcnow()

        # Run position monitor
        try:
            summary = self.monitor.run_tick()
        except Exception as e:
            logger.error(f"Monitor tick failed: {e}", exc_info=True)
            summary = {
                "timestamp": tick_start.isoformat(),
                "theses_checked": 0,
                "snapshots_taken": 0,
                "alerts_fired": 0,
                "positions_closed": 0,
                "errors": [str(e)],
            }

        # Record outcomes for any newly closed positions
        try:
            new_outcomes = self.outcome_recorder.record_all_closed()
            summary["outcomes_recorded"] = len(new_outcomes)
        except Exception as e:
            logger.error(f"Outcome recording failed: {e}", exc_info=True)
            summary["outcomes_recorded"] = 0
            summary.setdefault("errors", []).append(f"Outcome recording: {e}")

        # Fire on_outcome_recorded callback for each new outcome
        if self.on_outcome_recorded and new_outcomes:
            for outcome in new_outcomes:
                try:
                    thesis = self.store.get_thesis(outcome.thesis_id)
                    if thesis:
                        self.on_outcome_recorded(thesis, outcome)
                except Exception as e:
                    logger.error(f"on_outcome_recorded callback failed: {e}")

        # Add timing info
        summary["duration_seconds"] = round(
            (datetime.utcnow() - tick_start).total_seconds(), 2
        )
        summary["market_session"] = self._get_market_session()

        # Store in history
        self._tick_history.append(summary)
        if len(self._tick_history) > self._max_history:
            self._tick_history = self._tick_history[-self._max_history:]

        # Fire callbacks
        if self.on_tick_complete:
            try:
                self.on_tick_complete(summary)
            except Exception as e:
                logger.error(f"on_tick_complete callback failed: {e}")

        # Fire alert callbacks
        if self.on_alert and summary.get("alerts_fired", 0) > 0:
            try:
                alerts = self.store.get_alerts(unacknowledged_only=True, limit=20)
                for alert in alerts:
                    self.on_alert(alert.to_dict())
            except Exception as e:
                logger.error(f"on_alert callback failed: {e}")

        return summary

    # ------------------------------------------------------------------
    # Market session awareness
    # ------------------------------------------------------------------

    def _get_market_session(self) -> str:
        """Determine current market session."""
        now_et = datetime.now(ET)

        # Weekend
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

    def _get_current_interval(self) -> int:
        """Get the monitoring interval (seconds) for the current session."""
        session = self._get_market_session()

        if session == "market_hours":
            return self.market_interval
        if session in ("premarket", "afterhours"):
            return self.extended_hours_interval
        # overnight, weekend
        return self.off_hours_interval

    # ------------------------------------------------------------------
    # Status for CLI dashboard
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status for display."""
        active_theses = self.store.get_active_theses()

        return {
            "running": self._running,
            "market_session": self._get_market_session(),
            "current_interval_minutes": self._get_current_interval() // 60,
            "active_theses": len(active_theses),
            "active_tickers": [t.ticker for t in active_theses],
            "total_ticks": len(self._tick_history),
            "last_tick": self.last_tick,
            "unacknowledged_alerts": len(
                self.store.get_alerts(unacknowledged_only=True, limit=100)
            ),
        }


# ---------------------------------------------------------------------------
# Standalone daemon entry point
# ---------------------------------------------------------------------------


def main():
    """
    Run the journal scheduler as a standalone process.

    Usage:
        python -m tradingagents.journal.scheduler [--db-path PATH] [--interval MINUTES]
    """
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Trade Journal Scheduler Daemon")
    parser.add_argument(
        "--db-path",
        type=str,
        default="./journal/trade_journal.db",
        help="Path to the SQLite journal database",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Monitoring interval during market hours (minutes)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("journal/scheduler.log", mode="a"),
        ],
    )

    # Initialize components
    store = JournalStore(db_path=args.db_path)

    # Try to initialize Alpaca executor
    executor = None
    try:
        from tradingagents.execution import AlpacaExecutor

        api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            executor = AlpacaExecutor(paper=True)
            logger.info("Alpaca executor initialized for position monitoring")
        else:
            logger.warning("No Alpaca credentials — running without brokerage data")
    except Exception as e:
        logger.warning(f"Could not initialize Alpaca executor: {e}")

    def on_tick(summary):
        alerts = summary.get("alerts_fired", 0)
        if alerts > 0:
            logger.info(f"🚨 {alerts} new alert(s) fired!")

    scheduler = JournalScheduler(
        store=store,
        executor=executor,
        market_interval_minutes=args.interval,
        on_tick_complete=on_tick,
    )

    logger.info(
        f"Starting journal scheduler daemon "
        f"(db={args.db_path}, market_interval={args.interval}m)"
    )
    scheduler.run_forever()


if __name__ == "__main__":
    main()
