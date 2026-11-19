"""Append-only store of thesis versions, frozen forecasts, resolutions and watch contracts.

Nothing here is updated in place. A revised thesis is a new version that points to
the one it supersedes; a forecast is frozen (and hashed) when made and is resolved
by a separate resolution record; status changes are events. That keeps the
original prediction auditable after the outcome is known.

Point-in-time reads: ``latest_version(ticker, as_of)`` only returns versions
written for an as-of date on or before the requested one, so a historical replay
never sees a thesis written later.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS thesis_versions (
        thesis_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        as_of TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_id TEXT,
        supersedes_version INTEGER,
        action TEXT,
        execution_mode TEXT,
        thesis_json TEXT NOT NULL,
        validation_json TEXT,
        levels_json TEXT,
        cited_sources_json TEXT,
        evidence_snapshot_json TEXT,
        change_record_json TEXT,
        PRIMARY KEY (thesis_id, version)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_thesis_ticker_asof ON thesis_versions (ticker, as_of)",
    """CREATE TABLE IF NOT EXISTS thesis_status_events (
        thesis_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        at TEXT NOT NULL,
        reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS forecasts (
        forecast_id TEXT PRIMARY KEY,
        thesis_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        kind TEXT NOT NULL,
        proposition TEXT NOT NULL,
        condition TEXT,
        horizon_end TEXT,
        resolution_rule TEXT,
        resolution_source TEXT,
        probability REAL,
        interval_low REAL,
        interval_high REAL,
        basis TEXT,
        created_at TEXT NOT NULL,
        as_of TEXT NOT NULL,
        frozen_json TEXT NOT NULL,
        content_hash TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_forecast_ticker ON forecasts (ticker, horizon_end)",
    """CREATE TABLE IF NOT EXISTS forecast_resolutions (
        forecast_id TEXT NOT NULL,
        resolved_at TEXT NOT NULL,
        as_of TEXT NOT NULL,
        outcome TEXT NOT NULL,
        evidence_json TEXT,
        resolver TEXT NOT NULL,
        brier REAL,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS watch_contracts (
        contract_id TEXT PRIMARY KEY,
        thesis_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        event TEXT NOT NULL,
        window_start TEXT,
        window_end TEXT,
        source_to_monitor TEXT,
        forecast_ids_json TEXT,
        consequences_json TEXT,
        created_at TEXT NOT NULL,
        as_of TEXT NOT NULL
    )""",
]

FORECAST_OUTCOMES = {"true", "false", "ambiguous", "expired_unresolved", "void"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True, default=str)


def _load(value: Any) -> Any:
    return json.loads(value) if value else None


def forecast_hash(forecast: dict[str, Any]) -> str:
    frozen = {k: forecast.get(k) for k in sorted(forecast) if k not in {"content_hash", "created_at"}}
    return hashlib.sha256(_dump(frozen).encode("utf-8")).hexdigest()


def default_thesis_store_path() -> str:
    from verumtrade.dataflows.config import get_config

    config = get_config()
    settings = config.get("thesis_store") or {}
    if settings.get("path"):
        return str(settings["path"])
    return os.path.join(str(config.get("results_dir") or "./results"), "thesis_store.db")


class ThesisStore:
    _lock = threading.Lock()

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_thesis_store_path()
        self._memory = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        if self._memory is None:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._connect() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory is not None:
            self._memory.row_factory = sqlite3.Row
            with self._memory:
                yield self._memory
            return
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- Thesis versions ---------------------------------------------------

    def latest_version(self, ticker: str, as_of: str) -> dict[str, Any] | None:
        """Most recent version for ``ticker`` written for an as-of date on or before ``as_of``."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM thesis_versions WHERE ticker = ? AND as_of <= ?
                   ORDER BY as_of DESC, created_at DESC, version DESC LIMIT 1""",
                (ticker.upper(), str(as_of)[:10]),
            ).fetchone()
        return self._version_row(row) if row else None

    def versions(self, ticker: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM thesis_versions WHERE ticker = ? ORDER BY as_of, version", (ticker.upper(),)
            ).fetchall()
        return [self._version_row(row) for row in rows]

    @staticmethod
    def _version_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for key in ("thesis_json", "validation_json", "levels_json", "cited_sources_json",
                    "evidence_snapshot_json", "change_record_json"):
            record[key.removesuffix("_json")] = _load(record.pop(key))
        return record

    def append_version(
        self,
        *,
        ticker: str,
        as_of: str,
        thesis: dict[str, Any],
        validation: dict[str, Any],
        levels: dict[str, Any],
        cited_sources: dict[str, str],
        evidence_snapshot: dict[str, dict[str, Any]],
        change_record: dict[str, Any] | None,
        prior: dict[str, Any] | None,
        run_id: str = "",
    ) -> dict[str, Any]:
        ticker = ticker.upper()
        thesis_id = (prior or {}).get("thesis_id") or f"TH-{ticker}-{str(as_of)[:10]}"
        version = int((prior or {}).get("version") or 0) + 1
        decision = thesis.get("conditional_decision") or {}
        created = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO thesis_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    thesis_id, version, ticker, str(as_of)[:10], created, run_id,
                    (prior or {}).get("version"), str(decision.get("action") or ""),
                    str(decision.get("execution_mode") or ""), _dump(thesis), _dump(validation),
                    _dump(levels), _dump(cited_sources), _dump(evidence_snapshot), _dump(change_record),
                ),
            )
            conn.execute(
                "INSERT INTO thesis_status_events VALUES (?,?,?,?,?)",
                (thesis_id, version, "active", created, "new version" if prior else "initial thesis"),
            )
            if prior:
                conn.execute(
                    "INSERT INTO thesis_status_events VALUES (?,?,?,?,?)",
                    (thesis_id, prior["version"], "superseded", created, f"superseded by version {version}"),
                )
        return {"thesis_id": thesis_id, "version": version, "created_at": created}

    def add_status_event(self, thesis_id: str, version: int, status: str, reason: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO thesis_status_events VALUES (?,?,?,?,?)", (thesis_id, version, status, _now(), reason)
            )

    def status_events(self, thesis_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM thesis_status_events WHERE thesis_id = ? ORDER BY at, version", (thesis_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Forecasts -----------------------------------------------------------

    def add_forecasts(self, forecasts: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as conn:
            for forecast in forecasts:
                frozen = dict(forecast)
                frozen["content_hash"] = forecast_hash(frozen)
                conn.execute(
                    """INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        frozen["forecast_id"], frozen["thesis_id"], frozen["version"], frozen["ticker"].upper(),
                        frozen["kind"], frozen["proposition"], frozen.get("condition"), frozen.get("horizon_end"),
                        frozen.get("resolution_rule"), frozen.get("resolution_source"), frozen.get("probability"),
                        frozen.get("interval_low"), frozen.get("interval_high"), frozen.get("basis"),
                        frozen.get("created_at") or _now(), str(frozen["as_of"])[:10], _dump(frozen),
                        frozen["content_hash"],
                    ),
                )

    def forecasts(self, ticker: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM forecasts WHERE ticker = ?", [ticker.upper()]
        if as_of:
            query += " AND as_of <= ?"
            params.append(str(as_of)[:10])
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY as_of, forecast_id", params).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            record["frozen"] = _load(record.pop("frozen_json"))
            record["integrity_ok"] = forecast_hash(record["frozen"]) == record["content_hash"]
            out.append(record)
        return out

    def resolutions(self, forecast_ids: list[str] | None = None, *, as_of: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM forecast_resolutions WHERE 1=1", []
        if forecast_ids is not None:
            query += f" AND forecast_id IN ({','.join('?' for _ in forecast_ids) or 'NULL'})"
            params.extend(forecast_ids)
        if as_of:
            query += " AND as_of <= ?"
            params.append(str(as_of)[:10])
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY resolved_at", params).fetchall()
        return [{**dict(row), "evidence": _load(dict(row).get("evidence_json"))} for row in rows]

    def unresolved_forecasts(self, ticker: str, as_of: str) -> list[dict[str, Any]]:
        forecasts = self.forecasts(ticker, as_of=as_of)
        resolved = {r["forecast_id"] for r in self.resolutions([f["forecast_id"] for f in forecasts], as_of=as_of)}
        return [f for f in forecasts if f["forecast_id"] not in resolved]

    def record_resolution(
        self,
        forecast_id: str,
        *,
        outcome: str,
        as_of: str,
        resolver: str,
        evidence: list[dict[str, Any]] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if outcome not in FORECAST_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(FORECAST_OUTCOMES)}")
        with self._connect() as conn:
            row = conn.execute("SELECT probability FROM forecasts WHERE forecast_id = ?", (forecast_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown forecast {forecast_id}")
            existing = conn.execute(
                "SELECT outcome FROM forecast_resolutions WHERE forecast_id = ?", (forecast_id,)
            ).fetchone()
        if existing is not None:
            raise ValueError(f"forecast {forecast_id} is already resolved ({existing['outcome']})")
        probability = row["probability"]
        brier = None
        if probability is not None and outcome in {"true", "false"}:
            brier = round((float(probability) - (1.0 if outcome == "true" else 0.0)) ** 2, 4)
        record = {
            "forecast_id": forecast_id,
            "resolved_at": _now(),
            "as_of": str(as_of)[:10],
            "outcome": outcome,
            "evidence": evidence or [],
            "resolver": resolver,
            "brier": brier,
            "notes": notes,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO forecast_resolutions VALUES (?,?,?,?,?,?,?,?)",
                (forecast_id, record["resolved_at"], record["as_of"], outcome, _dump(record["evidence"]),
                 resolver, brier, notes),
            )
        return record

    # --- Watch contracts -----------------------------------------------------

    def add_watch_contract(self, contract: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watch_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    contract["contract_id"], contract["thesis_id"], contract["version"], contract["ticker"].upper(),
                    contract["event"], contract.get("window_start"), contract.get("window_end"),
                    contract.get("source_to_monitor"), _dump(contract.get("forecast_ids") or []),
                    _dump(contract.get("consequences") or []), _now(), str(contract["as_of"])[:10],
                ),
            )

    def watch_contracts(self, ticker: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM watch_contracts WHERE ticker = ?", [ticker.upper()]
        if as_of:
            query += " AND as_of <= ?"
            params.append(str(as_of)[:10])
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY as_of, version", params).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            record["forecast_ids"] = _load(record.pop("forecast_ids_json")) or []
            record["consequences"] = _load(record.pop("consequences_json")) or []
            out.append(record)
        return out

    # --- Scorecard -----------------------------------------------------------

    def forecast_scorecard(self, ticker: str | None = None, *, as_of: str | None = None) -> dict[str, Any]:
        """Forecast resolution quality, kept separate from trade returns."""
        query, params = "SELECT f.kind, f.probability, r.outcome, r.brier FROM forecasts f JOIN forecast_resolutions r ON r.forecast_id = f.forecast_id WHERE 1=1", []
        if ticker:
            query += " AND f.ticker = ?"
            params.append(ticker.upper())
        if as_of:
            query += " AND r.as_of <= ?"
            params.append(str(as_of)[:10])
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        by_outcome: dict[str, int] = {}
        for row in rows:
            by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + 1
        briers = [row["brier"] for row in rows if row["brier"] is not None]
        return {
            "resolved": len(rows),
            "by_outcome": by_outcome,
            "scored_with_probability": len(briers),
            "mean_brier": round(sum(briers) / len(briers), 4) if briers else None,
            "note": "Forecast quality is scored separately from trade returns; a few dozen correlated forecasts do not establish calibration.",
        }
