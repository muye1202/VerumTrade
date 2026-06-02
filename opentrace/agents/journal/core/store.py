"""
SQLite-backed persistence for the Trade Journal.

Single-file database, no external dependencies. Schema auto-migrates on init.
Designed for concurrent read access from the CLI while the scheduler writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

from opentrace.agents.journal.core.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    TradeOutcome,
    TradeLesson,
    JournalActionDecision,
    JournalActionExecution,
    ThesisStatus,
    AlertType,
    ActionReasonCode,
)

logger = logging.getLogger(__name__)

# Default location next to execution logs
DEFAULT_DB_PATH = Path("./journal/trade_journal.db")

_SCHEMA_VERSION = 4


class JournalStore:
    """
    SQLite-backed store for the Trade Journal.

    Thread-safe for single-writer / multiple-reader via WAL mode.
    All writes go through context-managed transactions.
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self):
        """Yield a connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trade_theses (
                    id              TEXT PRIMARY KEY,
                    ticker          TEXT NOT NULL,
                    trade_date      TEXT NOT NULL,
                    action          TEXT NOT NULL DEFAULT 'HOLD',
                    conviction      REAL,
                    entry_price     REAL,
                    entry_zone_low  REAL,
                    entry_zone_high REAL,
                    stop_loss       REAL,
                    stop_loss_pct   REAL,
                    trailing_stop_pct REAL,
                    target_1        REAL,
                    target_2        REAL,
                    risk_reward_ratio REAL,
                    time_horizon_label TEXT,
                    time_stop_date  TEXT,
                    holding_days_planned INTEGER,
                    catalyst        TEXT,
                    regime          TEXT,
                    key_risks       TEXT,
                    invalidation_trigger TEXT,
                    order_type      TEXT,
                    order_id        TEXT,
                    quantity        INTEGER,
                    position_size_pct REAL,
                    market_analyst_summary TEXT,
                    fundamentals_summary TEXT,
                    news_summary    TEXT,
                    risk_judge_summary TEXT,
                    final_decision_text TEXT,
                    decision_plan_json TEXT,
                    status          TEXT NOT NULL DEFAULT 'active',
                    created_at      TEXT NOT NULL,
                    closed_at       TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_theses_ticker ON trade_theses(ticker);
                CREATE INDEX IF NOT EXISTS idx_theses_status ON trade_theses(status);
                CREATE INDEX IF NOT EXISTS idx_theses_trade_date ON trade_theses(trade_date);

                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id              TEXT PRIMARY KEY,
                    thesis_id       TEXT NOT NULL REFERENCES trade_theses(id),
                    ticker          TEXT NOT NULL,
                    current_price   REAL,
                    bid             REAL,
                    ask             REAL,
                    vwap            REAL,
                    quantity        REAL,
                    market_value    REAL,
                    unrealized_pl   REAL,
                    unrealized_pl_pct REAL,
                    cost_basis      REAL,
                    distance_to_stop_pct REAL,
                    distance_to_target1_pct REAL,
                    holding_days_elapsed INTEGER,
                    holding_days_remaining INTEGER,
                    spy_change_pct  REAL,
                    relative_strength REAL,
                    max_adverse_excursion_pct REAL,
                    max_favorable_excursion_pct REAL,
                    timestamp       TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_snapshots_thesis ON position_snapshots(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON position_snapshots(ticker);
                CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON position_snapshots(timestamp);

                CREATE TABLE IF NOT EXISTS journal_alerts (
                    id              TEXT PRIMARY KEY,
                    thesis_id       TEXT NOT NULL REFERENCES trade_theses(id),
                    ticker          TEXT NOT NULL,
                    alert_type      TEXT NOT NULL,
                    severity        TEXT NOT NULL DEFAULT 'info',
                    message         TEXT NOT NULL,
                    trigger_price   REAL,
                    threshold_price REAL,
                    current_price   REAL,
                    unrealized_pl_pct REAL,
                    holding_days    INTEGER,
                    action_taken    TEXT,
                    action_recommended TEXT,
                    acknowledged    INTEGER NOT NULL DEFAULT 0,
                    timestamp       TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_thesis ON journal_alerts(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_alerts_type ON journal_alerts(alert_type);
                CREATE INDEX IF NOT EXISTS idx_alerts_ack ON journal_alerts(acknowledged);

                CREATE TABLE IF NOT EXISTS trade_outcomes (
                    id              TEXT PRIMARY KEY,
                    thesis_id       TEXT UNIQUE NOT NULL REFERENCES trade_theses(id),
                    ticker          TEXT NOT NULL,
                    entry_price     REAL,
                    exit_price      REAL,
                    realized_pl     REAL,
                    realized_pl_pct REAL,
                    holding_days    INTEGER,
                    entry_slippage_pct REAL,
                    exit_slippage_pct REAL,
                    max_adverse_excursion_pct REAL,
                    max_favorable_excursion_pct REAL,
                    capture_ratio   REAL,
                    spy_return_pct  REAL,
                    alpha_pct       REAL,
                    exit_reason     TEXT,
                    thesis_correct  INTEGER,
                    catalyst_materialized INTEGER,
                    target_reached  INTEGER,
                    stop_triggered  INTEGER,
                    time_stop_triggered INTEGER,
                    risk_reward_actual REAL,
                    risk_multiple   REAL,
                    market_analyst_accuracy TEXT,
                    news_analyst_accuracy TEXT,
                    risk_judge_accuracy TEXT,
                    closed_at       TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    reflection_notes TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_outcomes_thesis ON trade_outcomes(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_outcomes_ticker ON trade_outcomes(ticker);

                CREATE TABLE IF NOT EXISTS trade_lessons (
                    id              TEXT PRIMARY KEY,
                    thesis_id       TEXT NOT NULL REFERENCES trade_theses(id),
                    outcome_id      TEXT NOT NULL REFERENCES trade_outcomes(id),
                    ticker          TEXT NOT NULL,
                    trade_date      TEXT,
                    lesson_text     TEXT NOT NULL,
                    what_worked     TEXT,  -- JSON array
                    what_failed     TEXT,  -- JSON array
                    agent_accuracy  TEXT,  -- JSON object
                    most_accurate_agent TEXT,
                    least_accurate_agent TEXT,
                    regime_correct  INTEGER,
                    catalyst_materialized INTEGER,
                    category        TEXT NOT NULL DEFAULT 'uncategorized',
                    tags            TEXT,  -- JSON array
                    confidence      REAL DEFAULT 50.0,
                    action          TEXT,
                    realized_pl_pct REAL,
                    exit_reason     TEXT,
                    risk_multiple   REAL,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_lessons_thesis ON trade_lessons(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_lessons_ticker ON trade_lessons(ticker);
                CREATE INDEX IF NOT EXISTS idx_lessons_category ON trade_lessons(category);

                CREATE TABLE IF NOT EXISTS journal_action_decisions (
                    id              TEXT PRIMARY KEY,
                    thesis_id       TEXT NOT NULL REFERENCES trade_theses(id),
                    ticker          TEXT NOT NULL,
                    tick_timestamp  TEXT NOT NULL,
                    decision_type   TEXT NOT NULL,
                    reason_code     TEXT NOT NULL,
                    confidence      REAL NOT NULL DEFAULT 0,
                    recommended_qty_pct REAL,
                    dry_run         INTEGER NOT NULL DEFAULT 1,
                    gates_passed    INTEGER NOT NULL DEFAULT 0,
                    gate_block_reasons TEXT,
                    context_summary TEXT,
                    linked_alert_ids TEXT,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_action_decisions_thesis
                    ON journal_action_decisions(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_action_decisions_ticker
                    ON journal_action_decisions(ticker);
                CREATE INDEX IF NOT EXISTS idx_action_decisions_reason
                    ON journal_action_decisions(reason_code);
                CREATE INDEX IF NOT EXISTS idx_action_decisions_created
                    ON journal_action_decisions(created_at);

                CREATE TABLE IF NOT EXISTS journal_action_executions (
                    id              TEXT PRIMARY KEY,
                    decision_id     TEXT NOT NULL REFERENCES journal_action_decisions(id),
                    thesis_id       TEXT NOT NULL REFERENCES trade_theses(id),
                    ticker          TEXT NOT NULL,
                    submitted_signal TEXT NOT NULL,
                    submitted_qty   REAL,
                    order_type      TEXT,
                    limit_price     REAL,
                    stop_price      REAL,
                    status          TEXT NOT NULL,
                    broker_order_id TEXT,
                    error           TEXT,
                    raw_result_json TEXT,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_action_exec_decision
                    ON journal_action_executions(decision_id);
                CREATE INDEX IF NOT EXISTS idx_action_exec_thesis
                    ON journal_action_executions(thesis_id);
                CREATE INDEX IF NOT EXISTS idx_action_exec_ticker
                    ON journal_action_executions(ticker);
                CREATE INDEX IF NOT EXISTS idx_action_exec_created
                    ON journal_action_executions(created_at);

                CREATE TABLE IF NOT EXISTS journal_event_confirmations (
                    id              TEXT PRIMARY KEY,
                    ticker          TEXT NOT NULL,
                    event_key       TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    confidence      REAL,
                    timestamp       TEXT NOT NULL,
                    expires_at      TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_event_conf_ticker_key
                    ON journal_event_confirmations(ticker, event_key);
                CREATE INDEX IF NOT EXISTS idx_event_conf_timestamp
                    ON journal_event_confirmations(timestamp);

                CREATE TABLE IF NOT EXISTS journal_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            # executescript can't take parameters; use a separate execute
            conn.execute(
                "INSERT OR IGNORE INTO journal_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Best-effort additive migrations for existing journal DBs."""
        cols = {
            str(r["name"]) for r in conn.execute("PRAGMA table_info(trade_theses)").fetchall()
        }
        migrations = [
            ("entry_price_source", "TEXT"),
            ("entry_price_pending", "INTEGER NOT NULL DEFAULT 0"),
            ("last_reconciled_at", "TEXT"),
            ("decision_plan_json", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name in cols:
                continue
            conn.execute(f"ALTER TABLE trade_theses ADD COLUMN {col_name} {col_type}")
            logger.info("Journal schema migration: added trade_theses.%s", col_name)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_event_confirmations (
                id              TEXT PRIMARY KEY,
                ticker          TEXT NOT NULL,
                event_key       TEXT NOT NULL,
                value           TEXT NOT NULL,
                source          TEXT NOT NULL,
                confidence      REAL,
                timestamp       TEXT NOT NULL,
                expires_at      TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_conf_ticker_key "
            "ON journal_event_confirmations(ticker, event_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_conf_timestamp "
            "ON journal_event_confirmations(timestamp)"
        )

        # Keep schema version current after additive migrations.
        conn.execute(
            "INSERT INTO journal_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("schema_version", str(_SCHEMA_VERSION)),
        )

    # ------------------------------------------------------------------
    # TradeThesis CRUD
    # ------------------------------------------------------------------

    def save_thesis(self, thesis: TradeThesis) -> str:
        """Insert or update a trade thesis. Returns the thesis ID."""
        d = thesis.to_dict()
        if d.get("entry_price_pending") is not None:
            d["entry_price_pending"] = int(bool(d["entry_price_pending"]))
        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO trade_theses ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                [d[c] for c in cols],
            )
        logger.info(f"Saved thesis {thesis.id} for {thesis.ticker}")
        return thesis.id

    def get_thesis(self, thesis_id: str) -> Optional[TradeThesis]:
        """Fetch a single thesis by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trade_theses WHERE id = ?", (thesis_id,)
            ).fetchone()
        return self._row_to_thesis(row) if row else None

    def save_event_confirmation(
        self,
        *,
        ticker: str,
        event_key: str,
        value: Any,
        source: str,
        confidence: Optional[float] = None,
        timestamp: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> str:
        rec_id = uuid.uuid4().hex[:12]
        ts = timestamp or datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO journal_event_confirmations
                (id, ticker, event_key, value, source, confidence, timestamp, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    str(ticker or "").upper(),
                    str(event_key or "").strip(),
                    str(value),
                    str(source or "manual"),
                    _safe_float(confidence),
                    ts,
                    expires_at,
                ),
            )
        return rec_id

    def get_event_confirmations(
        self,
        *,
        ticker: str,
        as_of_ts: Optional[str] = None,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ticker_u = str(ticker or "").upper()
        as_of = as_of_ts or datetime.utcnow().isoformat()
        source_filters: List[str] = []
        if sources:
            source_filters = [str(s).strip().lower() for s in sources if str(s).strip()]
        where_source = ""
        params: List[Any] = [ticker_u, as_of, as_of]
        if source_filters:
            placeholders = ", ".join(["?"] * len(source_filters))
            where_source = f" AND lower(source) IN ({placeholders})"
            params.extend(source_filters)
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM journal_event_confirmations
                WHERE ticker = ?
                  AND timestamp <= ?
                  AND (expires_at IS NULL OR expires_at = '' OR expires_at >= ?)
                  {where_source}
                ORDER BY
                  CASE WHEN lower(source) IN ('manual','agent') THEN 0 ELSE 1 END,
                  timestamp DESC
                """,
                params,
            ).fetchall()

        out: Dict[str, Any] = {}
        for r in rows:
            d = dict(r)
            key = str(d.get("event_key") or "").strip()
            if not key or key in out:
                continue
            out[key] = self._coerce_event_value(d.get("value"))
        return out

    @staticmethod
    def _coerce_event_value(value: Any) -> Any:
        if value is None:
            return None
        s = str(value).strip()
        l = s.lower()
        if l in {"true", "1", "yes", "y"}:
            return True
        if l in {"false", "0", "no", "n"}:
            return False
        return s

    def get_active_theses(self) -> List[TradeThesis]:
        """Fetch all theses with status='active'."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_theses WHERE status = ? ORDER BY created_at DESC",
                (ThesisStatus.ACTIVE.value,),
            ).fetchall()
        return [self._row_to_thesis(r) for r in rows]

    def get_active_thesis_by_ticker(self, ticker: str) -> Optional[TradeThesis]:
        """Fetch newest active thesis for a ticker."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trade_theses "
                "WHERE ticker = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (ticker.upper(), ThesisStatus.ACTIVE.value),
            ).fetchone()
        return self._row_to_thesis(row) if row else None

    def get_theses_by_ticker(self, ticker: str) -> List[TradeThesis]:
        """Fetch all theses for a ticker, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_theses WHERE ticker = ? ORDER BY created_at DESC",
                (ticker.upper(),),
            ).fetchall()
        return [self._row_to_thesis(r) for r in rows]

    def get_all_theses(self, limit: int = 100) -> List[TradeThesis]:
        """Fetch recent theses, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_theses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_thesis(r) for r in rows]

    def close_thesis(self, thesis_id: str, status: ThesisStatus) -> None:
        """Mark a thesis as closed with the given status."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE trade_theses SET status = ?, closed_at = ? WHERE id = ?",
                (status.value, datetime.utcnow().isoformat(), thesis_id),
            )

    def get_open_position_for_ticker(
        self, executor: Any, ticker: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a ticker position from executor portfolio summary if available.

        Returns a normalized dict or None.
        """
        if executor is None:
            return None
        try:
            portfolio = executor.get_portfolio_summary()
        except Exception as e:
            logger.warning("Failed to fetch portfolio summary for %s: %s", ticker, e)
            return None

        ticker_u = str(ticker or "").upper()
        for pos in portfolio.get("positions", []) or []:
            if str(pos.get("symbol", "")).upper() == ticker_u:
                return pos
        return None

    def deduplicate_active_theses(self, executor: Any = None) -> Dict[str, Any]:
        """
        Merge duplicate active theses by ticker, keeping one canonical active record.

        Canonical selection preference:
        1) thesis with non-empty order_id
        2) newest created_at
        """
        report: Dict[str, Any] = {
            "tickers_checked": 0,
            "tickers_deduplicated": 0,
            "closed_theses": 0,
            "details": [],
        }

        active = self.get_active_theses()
        by_ticker: Dict[str, List[TradeThesis]] = {}
        for thesis in active:
            by_ticker.setdefault(thesis.ticker.upper(), []).append(thesis)

        for ticker, theses in by_ticker.items():
            report["tickers_checked"] += 1
            if len(theses) <= 1:
                continue

            canonical = max(
                theses,
                key=lambda t: (1 if t.order_id else 0, t.created_at or ""),
            )
            others = [t for t in theses if t.id != canonical.id]

            live_pos = self.get_open_position_for_ticker(executor, ticker)
            if live_pos:
                avg_entry = _safe_float(live_pos.get("avg_entry_price"))
                qty = _safe_float(live_pos.get("qty"))
                if avg_entry and avg_entry > 0:
                    canonical.entry_price = avg_entry
                    canonical.entry_price_source = "broker_avg_entry"
                    canonical.entry_price_pending = False
                    canonical.last_reconciled_at = datetime.utcnow().isoformat()
                if qty is not None:
                    canonical.quantity = int(abs(qty))
            else:
                weighted_qty = 0.0
                weighted_cost = 0.0
                for t in theses:
                    q = _safe_float(t.quantity)
                    p = _safe_float(t.entry_price)
                    if q and q > 0 and p and p > 0:
                        weighted_qty += q
                        weighted_cost += q * p
                if weighted_qty > 0:
                    canonical.entry_price = round(weighted_cost / weighted_qty, 6)
                    canonical.quantity = int(abs(weighted_qty))
                    canonical.entry_price_source = (
                        canonical.entry_price_source or "submission_quote"
                    )

            self.save_thesis(canonical)
            for t in others:
                self.close_thesis(t.id, ThesisStatus.CLOSED)

            report["tickers_deduplicated"] += 1
            report["closed_theses"] += len(others)
            report["details"].append(
                {
                    "ticker": ticker,
                    "kept": canonical.id,
                    "closed": [t.id for t in others],
                }
            )
            logger.info(
                "Deduplicated %s active theses for %s (kept=%s, closed=%s)",
                len(theses),
                ticker,
                canonical.id,
                ",".join([t.id for t in others]),
            )

        return report

    # ------------------------------------------------------------------
    # PositionSnapshot
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot: PositionSnapshot) -> str:
        """Insert a position snapshot. Returns the snapshot ID."""
        d = snapshot.to_dict()
        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO position_snapshots ({col_names}) VALUES ({placeholders})",
                [d[c] for c in cols],
            )
        return snapshot.id

    def get_snapshots(
        self, thesis_id: str, limit: int = 100
    ) -> List[PositionSnapshot]:
        """Fetch snapshots for a thesis, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM position_snapshots WHERE thesis_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (thesis_id, limit),
            ).fetchall()
        return [PositionSnapshot.from_dict(dict(r)) for r in rows]

    def get_latest_snapshot(self, thesis_id: str) -> Optional[PositionSnapshot]:
        """Fetch the most recent snapshot for a thesis."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM position_snapshots WHERE thesis_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (thesis_id,),
            ).fetchone()
        return PositionSnapshot.from_dict(dict(row)) if row else None

    def get_extreme_snapshots(self, thesis_id: str) -> Dict[str, Optional[float]]:
        """Get max adverse and favorable excursion across all snapshots for a thesis."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    MIN(max_adverse_excursion_pct) as worst_mae,
                    MAX(max_favorable_excursion_pct) as best_mfe
                FROM position_snapshots
                WHERE thesis_id = ?
                """,
                (thesis_id,),
            ).fetchone()
        if row:
            return {
                "max_adverse_excursion_pct": row["worst_mae"],
                "max_favorable_excursion_pct": row["best_mfe"],
            }
        return {"max_adverse_excursion_pct": None, "max_favorable_excursion_pct": None}

    # ------------------------------------------------------------------
    # JournalAlert
    # ------------------------------------------------------------------

    def save_alert(self, alert: JournalAlert) -> str:
        """Insert a journal alert. Returns the alert ID."""
        d = alert.to_dict()
        # SQLite stores booleans as int
        d["acknowledged"] = int(d["acknowledged"])
        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO journal_alerts ({col_names}) VALUES ({placeholders})",
                [d[c] for c in cols],
            )
        logger.info(
            f"Alert [{alert.alert_type}] for {alert.ticker}: {alert.message}"
        )
        return alert.id

    def get_alerts(
        self,
        thesis_id: Optional[str] = None,
        unacknowledged_only: bool = False,
        limit: int = 50,
    ) -> List[JournalAlert]:
        """Fetch alerts, optionally filtered."""
        conditions = []
        params: list = []

        if thesis_id:
            conditions.append("thesis_id = ?")
            params.append(thesis_id)
        if unacknowledged_only:
            conditions.append("acknowledged = 0")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM journal_alerts {where} ORDER BY timestamp DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        alerts = []
        for r in rows:
            d = dict(r)
            d["acknowledged"] = bool(d["acknowledged"])
            alerts.append(JournalAlert.from_dict(d))
        return alerts

    def acknowledge_alert(self, alert_id: str) -> None:
        """Mark an alert as acknowledged."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE journal_alerts SET acknowledged = 1 WHERE id = ?",
                (alert_id,),
            )

    def update_alert_action_taken(self, alert_id: str, action_taken: str) -> None:
        """Update action_taken for an existing alert."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE journal_alerts SET action_taken = ? WHERE id = ?",
                (action_taken, alert_id),
            )

    def has_recent_alert(
        self, thesis_id: str, alert_type: AlertType, within_hours: float = 4.0
    ) -> bool:
        """Check if a similar alert was already fired recently (dedup)."""
        cutoff = datetime.utcnow().timestamp() - (within_hours * 3600)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM journal_alerts
                WHERE thesis_id = ? AND alert_type = ?
                AND datetime(timestamp) > datetime(?, 'unixepoch')
                """,
                (thesis_id, alert_type.value, cutoff),
            ).fetchone()
        return (row["cnt"] if row else 0) > 0

    # ------------------------------------------------------------------
    # JournalActionDecision / JournalActionExecution
    # ------------------------------------------------------------------

    def save_action_decision(self, decision: JournalActionDecision) -> str:
        """Insert or update an action decision."""
        d = decision.to_dict()
        d["dry_run"] = int(bool(d.get("dry_run")))
        d["gates_passed"] = int(bool(d.get("gates_passed")))
        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO journal_action_decisions ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                [d[c] for c in cols],
            )
        return decision.id

    def save_action_execution(self, execution: JournalActionExecution) -> str:
        """Insert or update an action execution record."""
        d = execution.to_dict()
        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO journal_action_executions ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                [d[c] for c in cols],
            )
        return execution.id

    def get_action_decisions(
        self,
        thesis_id: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 50,
    ) -> List[JournalActionDecision]:
        """Fetch recent action decisions with optional filters."""
        conditions = []
        params: list[Any] = []
        if thesis_id:
            conditions.append("thesis_id = ?")
            params.append(thesis_id)
        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker.upper())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM journal_action_decisions {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        out: List[JournalActionDecision] = []
        for r in rows:
            d = dict(r)
            d["dry_run"] = bool(d.get("dry_run"))
            d["gates_passed"] = bool(d.get("gates_passed"))
            out.append(JournalActionDecision.from_dict(d))
        return out

    def get_action_executions(
        self,
        decision_id: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 50,
    ) -> List[JournalActionExecution]:
        """Fetch recent action executions with optional filters."""
        conditions = []
        params: list[Any] = []
        if decision_id:
            conditions.append("decision_id = ?")
            params.append(decision_id)
        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker.upper())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM journal_action_executions {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        return [JournalActionExecution.from_dict(dict(r)) for r in rows]

    def count_action_executions_for_day(self, day_utc: Optional[date] = None) -> int:
        """Count action executions for a UTC day."""
        target = (day_utc or datetime.utcnow().date()).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM journal_action_executions "
                "WHERE date(created_at) = date(?)",
                (target,),
            ).fetchone()
        return int(row["cnt"] if row else 0)

    def has_recent_action_decision(
        self,
        thesis_id: str,
        reason_code: ActionReasonCode,
        within_minutes: float = 30.0,
    ) -> bool:
        """Check if the same reason was recently decided for this thesis."""
        cutoff = datetime.utcnow().timestamp() - (float(within_minutes) * 60.0)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM journal_action_decisions
                WHERE thesis_id = ? AND reason_code = ?
                AND datetime(created_at) > datetime(?, 'unixepoch')
                """,
                (thesis_id, reason_code.value, cutoff),
            ).fetchone()
        return int(row["cnt"] if row else 0) > 0

    # ------------------------------------------------------------------
    # TradeOutcome
    # ------------------------------------------------------------------

    def save_outcome(self, outcome: TradeOutcome) -> str:
        """Insert or update a trade outcome. Returns the outcome ID."""
        d = outcome.to_dict()
        # Convert booleans to int for SQLite
        for key in (
            "thesis_correct",
            "catalyst_materialized",
            "target_reached",
            "stop_triggered",
            "time_stop_triggered",
        ):
            if d[key] is not None:
                d[key] = int(d[key])

        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO trade_outcomes ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                [d[c] for c in cols],
            )
        logger.info(f"Saved outcome {outcome.id} for {outcome.ticker}")
        return outcome.id

    def get_outcome(self, thesis_id: str) -> Optional[TradeOutcome]:
        """Fetch outcome for a thesis."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trade_outcomes WHERE thesis_id = ?", (thesis_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in (
            "thesis_correct",
            "catalyst_materialized",
            "target_reached",
            "stop_triggered",
            "time_stop_triggered",
        ):
            if d[key] is not None:
                d[key] = bool(d[key])
        return TradeOutcome.from_dict(d)

    def get_all_outcomes(self, limit: int = 100) -> List[TradeOutcome]:
        """Fetch recent outcomes, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_outcomes ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        outcomes = []
        for r in rows:
            d = dict(r)
            for key in (
                "thesis_correct",
                "catalyst_materialized",
                "target_reached",
                "stop_triggered",
                "time_stop_triggered",
            ):
                if d[key] is not None:
                    d[key] = bool(d[key])
            outcomes.append(TradeOutcome.from_dict(d))
        return outcomes

    # ------------------------------------------------------------------
    # Aggregate queries (for performance analytics)
    # ------------------------------------------------------------------

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Compute aggregate performance metrics across all closed trades.

        Returns dict with: total_trades, win_rate, avg_return, avg_holding_days,
        avg_alpha, total_pl, best_trade, worst_trade, avg_risk_multiple.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN realized_pl_pct > 0 THEN 1 ELSE 0 END) as winners,
                    AVG(realized_pl_pct) as avg_return_pct,
                    AVG(holding_days) as avg_holding_days,
                    AVG(alpha_pct) as avg_alpha_pct,
                    SUM(realized_pl) as total_pl,
                    MAX(realized_pl_pct) as best_trade_pct,
                    MIN(realized_pl_pct) as worst_trade_pct,
                    AVG(risk_multiple) as avg_risk_multiple,
                    AVG(capture_ratio) as avg_capture_ratio,
                    AVG(max_adverse_excursion_pct) as avg_mae,
                    AVG(max_favorable_excursion_pct) as avg_mfe
                FROM trade_outcomes
                """
            ).fetchone()

        if not row or row["total_trades"] == 0:
            return {"total_trades": 0}

        total = row["total_trades"]
        winners = row["winners"] or 0

        return {
            "total_trades": total,
            "winners": winners,
            "losers": total - winners,
            "win_rate": winners / total if total > 0 else 0,
            "avg_return_pct": row["avg_return_pct"],
            "avg_holding_days": row["avg_holding_days"],
            "avg_alpha_pct": row["avg_alpha_pct"],
            "total_pl": row["total_pl"],
            "best_trade_pct": row["best_trade_pct"],
            "worst_trade_pct": row["worst_trade_pct"],
            "avg_risk_multiple": row["avg_risk_multiple"],
            "avg_capture_ratio": row["avg_capture_ratio"],
            "avg_mae": row["avg_mae"],
            "avg_mfe": row["avg_mfe"],
        }

    # ------------------------------------------------------------------
    # TradeLesson CRUD
    # ------------------------------------------------------------------

    def save_lesson(self, lesson: TradeLesson) -> str:
        """Insert or update a trade lesson. Returns the lesson ID."""
        d = lesson.to_dict()
        # Serialize lists and dicts to JSON
        d["what_worked"] = json.dumps(d["what_worked"]) if d["what_worked"] else None
        d["what_failed"] = json.dumps(d["what_failed"]) if d["what_failed"] else None
        d["agent_accuracy"] = json.dumps(d["agent_accuracy"]) if d["agent_accuracy"] else None
        d["tags"] = json.dumps(d["tags"]) if d["tags"] else None
        # Convert booleans to int for SQLite
        if d["regime_correct"] is not None:
            d["regime_correct"] = int(d["regime_correct"])
        if d["catalyst_materialized"] is not None:
            d["catalyst_materialized"] = int(d["catalyst_materialized"])

        cols = list(d.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")

        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO trade_lessons ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                [d[c] for c in cols],
            )
        logger.info(f"Saved lesson {lesson.id} for {lesson.ticker}: {lesson.category}")
        return lesson.id

    def get_lesson(self, lesson_id: str) -> Optional[TradeLesson]:
        """Fetch a single lesson by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trade_lessons WHERE id = ?", (lesson_id,)
            ).fetchone()
        return self._row_to_lesson(row) if row else None

    def get_lessons_by_thesis(self, thesis_id: str) -> List[TradeLesson]:
        """Fetch lessons for a thesis."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lessons WHERE thesis_id = ? ORDER BY created_at DESC",
                (thesis_id,),
            ).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def get_lessons_by_category(self, category: str, limit: int = 50) -> List[TradeLesson]:
        """Fetch lessons by category."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lessons WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def get_all_lessons(self, limit: int = 100) -> List[TradeLesson]:
        """Fetch recent lessons, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lessons ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def get_lesson_categories(self) -> Dict[str, int]:
        """Get count of lessons per category."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM trade_lessons GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
        return {r["category"]: r["cnt"] for r in rows}

    def _row_to_lesson(self, row) -> TradeLesson:
        """Convert a database row to a TradeLesson."""
        d = dict(row)
        # Parse JSON fields
        if d.get("what_worked"):
            d["what_worked"] = json.loads(d["what_worked"])
        else:
            d["what_worked"] = []
        if d.get("what_failed"):
            d["what_failed"] = json.loads(d["what_failed"])
        else:
            d["what_failed"] = []
        if d.get("agent_accuracy"):
            d["agent_accuracy"] = json.loads(d["agent_accuracy"])
        else:
            d["agent_accuracy"] = {}
        if d.get("tags"):
            d["tags"] = json.loads(d["tags"])
        else:
            d["tags"] = []
        # Convert int back to bool
        if d.get("regime_correct") is not None:
            d["regime_correct"] = bool(d["regime_correct"])
        if d.get("catalyst_materialized") is not None:
            d["catalyst_materialized"] = bool(d["catalyst_materialized"])
        return TradeLesson.from_dict(d)

    def _row_to_thesis(self, row) -> TradeThesis:
        """Convert a database row to a TradeThesis with type normalization."""
        d = dict(row)
        if d.get("entry_price_pending") is not None:
            d["entry_price_pending"] = bool(d["entry_price_pending"])
        return TradeThesis.from_dict(d)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
