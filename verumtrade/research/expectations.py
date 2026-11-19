"""Expectation baselines: what the market could have known to expect, as of a date.

Kinds of baseline are kept separate (management guidance, observed consensus
snapshot, individual analyst statement, price-implied scenario, the framework's
own forecast). A consensus baseline exists only if an estimate observation was
recorded on or before the as-of date. Without one, the surprise is reported as
not measurable rather than inferred from positive news or momentum.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypedDict

from verumtrade.dataflows.estimate_revisions_db import EstimateRevisionsDB

BaselineType = Literal[
    "management_guidance",
    "consensus_snapshot",
    "analyst_statement",
    "price_implied",
    "framework_forecast",
    "none",
]
BASELINE_TYPES = {
    "management_guidance",
    "consensus_snapshot",
    "analyst_statement",
    "price_implied",
    "framework_forecast",
    "none",
}

YF_PERIODS = {"0q": "current_quarter", "+1q": "next_quarter", "0y": "current_fiscal_year", "+1y": "next_fiscal_year"}


class ExpectationSnapshot(TypedDict, total=False):
    ticker: str
    as_of: str
    consensus_available: bool
    observations: list[dict[str, Any]]
    notes: list[str]


def build_expectation_snapshot(ticker: str, as_of: str, db: EstimateRevisionsDB | None = None) -> ExpectationSnapshot:
    try:
        observations = (db or EstimateRevisionsDB()).observations_as_of(ticker, as_of)
    except Exception as exc:  # unreadable store means no baseline, not an error in the run
        observations = []
        notes = [f"estimate store unavailable: {type(exc).__name__}"]
    else:
        notes = []
    if not observations:
        notes.append(
            "No consensus estimate was recorded on or before the as-of date; earnings surprise versus "
            "consensus cannot be measured. Management guidance cited from a filing or release may still "
            "serve as a baseline."
        )
    return {
        "ticker": str(ticker).upper(),
        "as_of": as_of,
        "consensus_available": bool(observations),
        "observations": observations,
        "notes": notes,
    }


def record_current_estimates(
    ticker: str,
    *,
    db: EstimateRevisionsDB | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
    observed_at: str | None = None,
) -> int:
    """Snapshot the provider's *current* consensus with today's timestamp. Returns rows stored.

    Call this prospectively (live runs, scheduled jobs). It must never be used to fill a
    historical date: the observation time is always the real time of retrieval.
    """
    stamp = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = (fetch or _fetch_yfinance_estimates)(ticker)
    store = db or EstimateRevisionsDB()
    stored = 0
    for metric, table in (("eps", data.get("earnings_estimate")), ("revenue", data.get("revenue_estimate"))):
        for period_code, row in (table or {}).items():
            if not isinstance(row, dict) or row.get("avg") is None:
                continue
            store.store_observation(
                {
                    "ticker": ticker,
                    "observed_at": stamp,
                    "provider": "yfinance",
                    "metric": metric,
                    "metric_definition": "diluted EPS (provider basis)" if metric == "eps" else "total revenue",
                    "fiscal_period": YF_PERIODS.get(period_code, period_code),
                    "currency": data.get("currency"),
                    "value": row.get("avg"),
                    "low": row.get("low"),
                    "high": row.get("high"),
                    "contributor_count": row.get("numberOfAnalysts"),
                    "source_version": "yfinance_estimates_v1",
                }
            )
            stored += 1
    return stored


def _fetch_yfinance_estimates(ticker: str) -> dict[str, Any]:
    import yfinance as yf

    obj = yf.Ticker(ticker)
    out: dict[str, Any] = {"currency": (getattr(obj, "fast_info", {}) or {}).get("currency")}
    for name in ("earnings_estimate", "revenue_estimate"):
        frame = getattr(obj, name, None)
        if frame is not None and not getattr(frame, "empty", True):
            out[name] = {str(idx): row.to_dict() for idx, row in frame.iterrows()}
    return out
