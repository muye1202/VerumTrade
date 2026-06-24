"""
Condition Tracker — stateful, cross-tick condition evaluation.

The original decision_plan_evaluator is stateless: each tick is evaluated
in isolation. This module persists per-thesis state across ticks so the
journal agent can evaluate conditions like:
  - "two consecutive daily closes above $93 on declining volume"
  - "8 trading days since entry"
  - "volume below 0.8x average for N sessions"

State is stored in a small SQLite table alongside the journal DB.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── SQLite-backed state store ────────────────────────────────────────────


class ConditionStateStore:
    """Lightweight SQLite store for per-thesis condition state."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS condition_state (
        thesis_id   TEXT PRIMARY KEY,
        state_json  TEXT NOT NULL DEFAULT '{}',
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS phase_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_id   TEXT NOT NULL,
        old_phase   TEXT,
        new_phase   TEXT NOT NULL,
        reason      TEXT,
        timestamp   TEXT NOT NULL
    );
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self):
        self._conn.executescript(self._DDL)
        self._conn.commit()

    def load(self, thesis_id: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT state_json FROM condition_state WHERE thesis_id = ?",
            (thesis_id,),
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["state_json"])
        except Exception:
            return {}

    def save(self, thesis_id: str, state: Dict[str, Any]):
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            """INSERT INTO condition_state (thesis_id, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(thesis_id) DO UPDATE
               SET state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (thesis_id, json.dumps(state, default=str), now),
        )
        self._conn.commit()

    def log_phase_transition(
        self, thesis_id: str, old_phase: Optional[str], new_phase: str, reason: str
    ):
        self._conn.execute(
            """INSERT INTO phase_log (thesis_id, old_phase, new_phase, reason, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (thesis_id, old_phase, new_phase, reason, datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_phase_history(self, thesis_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM phase_log WHERE thesis_id = ? ORDER BY id ASC",
            (thesis_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Per-thesis condition tracker ─────────────────────────────────────────


class ConditionTracker:
    """
    Stateful condition evaluator for a single thesis.

    Maintains running counters, session history, and derived metrics
    that survive across scheduler ticks.
    """

    def __init__(self, thesis_id: str, store: ConditionStateStore):
        self.thesis_id = thesis_id
        self._store = store
        self.state: Dict[str, Any] = store.load(thesis_id)

    def persist(self):
        """Flush current state to SQLite."""
        self._store.save(self.thesis_id, self.state)

    # ── Tick ingestion ───────────────────────────────────────────────

    def ingest_tick(
        self,
        *,
        price: Optional[float],
        volume_ratio: Optional[float],
        market_session: str,
        timestamp: Optional[str] = None,
    ):
        """
        Record a new tick's data. Call once per scheduler tick.
        Automatically computes derived state (session close log, peaks, etc.).
        """
        ts = timestamp or datetime.utcnow().isoformat()
        trade_day = self._extract_trade_day(ts)

        # Track the latest values
        if price is not None:
            self.state["last_price"] = price
            self.state["price_high"] = max(price, self.state.get("price_high", price))
            self.state["price_low"] = min(price, self.state.get("price_low", price))

        self.state["last_volume_ratio"] = volume_ratio
        self.state["last_session"] = market_session
        self.state["last_tick_ts"] = ts
        self.state["tick_count"] = self.state.get("tick_count", 0) + 1

        # Track end-of-day close prices for consecutive-close logic.
        # We approximate "daily close" as the last market_hours tick of each day.
        if trade_day and market_session == "market_hours" and price is not None:
            day_key = f"eod_{trade_day}"
            self.state[day_key] = {
                "price": price,
                "volume_ratio": volume_ratio,
                "ts": ts,
            }
            # Keep a rolling list of observed trading days
            days: List[str] = self.state.get("trading_days", [])
            if not days or days[-1] != trade_day:
                days.append(trade_day)
            self.state["trading_days"] = days[-30:]  # rolling 30-day window

        self.persist()

    # ── Condition checks ─────────────────────────────────────────────

    def check_consecutive_closes(
        self,
        *,
        direction: str,        # "above" or "below"
        threshold: float,
        required: int = 2,
        volume_ratio_max: Optional[float] = None,
    ) -> bool:
        """
        Check if the last N daily closes satisfy a price+volume condition.

        E.g., "two consecutive closes above $93 on volume below 0.8x average"
        """
        days: List[str] = self.state.get("trading_days", [])
        if len(days) < required:
            return False

        consecutive = 0
        for day in reversed(days):
            eod = self.state.get(f"eod_{day}")
            if not eod or eod.get("price") is None:
                break

            price_ok = (
                (direction == "above" and eod["price"] > threshold)
                or (direction == "below" and eod["price"] < threshold)
            )
            volume_ok = True
            if volume_ratio_max is not None and eod.get("volume_ratio") is not None:
                volume_ok = eod["volume_ratio"] <= volume_ratio_max

            if price_ok and volume_ok:
                consecutive += 1
                if consecutive >= required:
                    return True
            else:
                break

        return False

    def days_since(self, reference_date: str) -> int:
        """Count trading days observed since a reference date (inclusive)."""
        days: List[str] = self.state.get("trading_days", [])
        return sum(1 for d in days if d >= reference_date)

    def days_active(self) -> int:
        """Total trading days with at least one tick."""
        return len(self.state.get("trading_days", []))

    def check_volume_in_range(
        self,
        *,
        ratio_min: Optional[float] = None,
        ratio_max: Optional[float] = None,
        current_ratio: Optional[float] = None,
    ) -> bool:
        """Check if volume ratio is within [min, max] bounds."""
        vr = current_ratio if current_ratio is not None else self.state.get("last_volume_ratio")
        if vr is None:
            return ratio_min is None and ratio_max is None
        if ratio_min is not None and vr < ratio_min:
            return False
        if ratio_max is not None and vr > ratio_max:
            return False
        return True

    def check_time_stop(self, *, max_trading_days: int, entry_date: Optional[str] = None) -> bool:
        """True if the time stop has been breached."""
        if entry_date:
            return self.days_since(entry_date) >= max_trading_days
        return self.days_active() >= max_trading_days

    def get_eod_series(self, last_n: int = 5) -> List[Dict[str, Any]]:
        """Return the last N end-of-day snapshots."""
        days: List[str] = self.state.get("trading_days", [])
        result = []
        for day in days[-last_n:]:
            eod = self.state.get(f"eod_{day}")
            if eod:
                result.append({"date": day, **eod})
        return result

    # ── Trigger proximity (Tier 2 hook) ──────────────────────────────

    def price_proximity(self, current: float, target: float) -> float:
        """
        0.0 = far away, 1.0 = at or past the target.
        Used to decide whether LLM evaluation is warranted.
        """
        if current == target:
            return 1.0
        distance_pct = abs(current - target) / max(abs(target), 1e-9) * 100
        # Map: 0% distance → 1.0, 5%+ distance → 0.0
        return max(0.0, min(1.0, 1.0 - distance_pct / 5.0))

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_trade_day(ts: str) -> Optional[str]:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.date().isoformat()
        except Exception:
            return None
