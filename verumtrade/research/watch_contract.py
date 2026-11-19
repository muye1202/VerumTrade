"""Watch contracts: what to monitor next, and the precommitted forecasts it will resolve.

A first report creates a contract naming the next relevant event, the window it
should fall in, the source that will settle it, the forecasts it resolves, and what
each outcome means for the decision. Event forecasts and conditional price-reaction
forecasts are recorded separately so a sound business call is never confused with a
correct price call.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from verumtrade.research.thesis_store import ThesisStore

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
EXPIRY_GRACE_DAYS = 5


def _dates(text: Any) -> list[str]:
    found = []
    for match in _ISO_DATE.findall(str(text or "")):
        try:
            found.append(date.fromisoformat(match).isoformat())
        except ValueError:
            continue
    return found


def catalyst_window(thesis: dict[str, Any], *, as_of: str, horizon_end: str) -> tuple[str, str]:
    dates = _dates((thesis.get("catalyst") or {}).get("expected_window"))
    if not dates:
        return str(as_of)[:10], horizon_end
    return min(dates), max(dates)


def _reaction_interval(scenario: dict[str, Any], price_context: dict[str, Any]) -> tuple[Any, Any, str]:
    reaction = scenario.get("price_reaction") or {}
    if reaction.get("basis") == "historical_distribution":
        quantiles = price_context.get("quantiles") or {}
        return (
            quantiles.get(str(reaction.get("low"))),
            quantiles.get(str(reaction.get("high"))),
            f"historical_distribution {reaction.get('low')}..{reaction.get('high')} of {price_context.get('calc_id')}",
        )
    return reaction.get("low_pct"), reaction.get("high_pct"), f"assumption: {reaction.get('assumption')}"


def forecasts_from_thesis(
    thesis: dict[str, Any],
    *,
    thesis_id: str,
    version: int,
    ticker: str,
    as_of: str,
    horizon_end: str,
    price_context: dict[str, Any],
) -> list[dict[str, Any]]:
    prefix = f"{thesis_id}-v{version}"
    catalyst = thesis.get("catalyst") or {}
    window_start, window_end = catalyst_window(thesis, as_of=as_of, horizon_end=horizon_end)
    forecasts: list[dict[str, Any]] = []
    if str(catalyst.get("event") or "").strip() and "none" not in str(catalyst.get("event")).lower():
        forecasts.append(
            {
                "forecast_id": f"{prefix}-catalyst-timing",
                "kind": "event_timing",
                "proposition": f"{catalyst['event']} occurs between {window_start} and {window_end}",
                "horizon_end": window_end,
                "resolution_rule": "true if the event is confirmed inside the window; false if confirmed outside it",
                "resolution_source": catalyst.get("resolution_source") or "",
                "probability": None,
                "basis": None,
            }
        )
    for scenario in thesis.get("scenarios") or []:
        sid = str(scenario.get("scenario_id") or "")
        resolves = (_dates(scenario.get("resolves_by")) or [window_end])[0]
        probability = scenario.get("probability") or {}
        forecasts.append(
            {
                "forecast_id": f"{prefix}-{sid}-event",
                "kind": "event",
                "proposition": str(scenario.get("event_outcome") or ""),
                "horizon_end": resolves,
                "resolution_rule": (
                    f"true if: {scenario.get('event_outcome')}; does not apply if: {scenario.get('invalidated_if')}"
                ),
                "resolution_source": catalyst.get("resolution_source") or "",
                "probability": probability.get("value") if isinstance(probability, dict) else None,
                "basis": probability.get("basis") if isinstance(probability, dict) else None,
                "scenario_id": sid,
            }
        )
        low, high, basis = _reaction_interval(scenario, price_context)
        forecasts.append(
            {
                "forecast_id": f"{prefix}-{sid}-reaction",
                "kind": "conditional_price_reaction",
                "proposition": f"If {sid} occurs, the holding-period return falls in [{low}, {high}]",
                "condition": f"{sid}: {scenario.get('event_outcome')}",
                "horizon_end": horizon_end,
                "resolution_rule": "evaluated only if the condition resolved true; return measured from the as-of close",
                "resolution_source": "daily closes",
                "interval_low": low,
                "interval_high": high,
                "probability": None,
                "basis": basis,
                "scenario_id": sid,
            }
        )
    for forecast in forecasts:
        forecast.update({"thesis_id": thesis_id, "version": version, "ticker": ticker.upper(), "as_of": str(as_of)[:10]})
    return forecasts


def build_watch_contract(
    thesis: dict[str, Any],
    forecasts: list[dict[str, Any]],
    *,
    thesis_id: str,
    version: int,
    ticker: str,
    as_of: str,
    horizon_end: str,
) -> dict[str, Any]:
    catalyst = thesis.get("catalyst") or {}
    window_start, window_end = catalyst_window(thesis, as_of=as_of, horizon_end=horizon_end)
    decision = thesis.get("conditional_decision") or {}
    consequences = [
        {
            "if": f"{s.get('scenario_id')} ({s.get('kind')}): {s.get('event_outcome')}",
            "then": "re-evaluate the thesis; the scenario's invalidation condition is: "
            + str(s.get("invalidated_if") or "unspecified"),
        }
        for s in thesis.get("scenarios") or []
    ]
    consequences.extend(
        {"if": str(c.get("if")), "then": str(c.get("then_action"))}
        for c in decision.get("conditions") or []
        if isinstance(c, dict) and c.get("if")
    )
    consequences.extend({"if": f"falsifier observed: {f}", "then": "withdraw or revise the thesis"} for f in thesis.get("falsifiers") or [])
    return {
        "contract_id": f"{thesis_id}-v{version}-watch",
        "thesis_id": thesis_id,
        "version": version,
        "ticker": ticker.upper(),
        "event": str(catalyst.get("event") or "periodic review"),
        "window_start": window_start,
        "window_end": window_end,
        "source_to_monitor": str(catalyst.get("resolution_source") or ""),
        "forecast_ids": [f["forecast_id"] for f in forecasts],
        "consequences": consequences,
        "as_of": str(as_of)[:10],
    }


def evaluate_watch_contracts(
    contracts: list[dict[str, Any]],
    unresolved_forecast_ids: set[str],
    as_of: str,
) -> list[dict[str, Any]]:
    """Status of each contract at ``as_of``: pending, due, expired (event slipped), or settled."""
    today = str(as_of)[:10]
    out = []
    for contract in contracts:
        open_ids = [fid for fid in contract.get("forecast_ids") or [] if fid in unresolved_forecast_ids]
        start, end = contract.get("window_start") or today, contract.get("window_end") or today
        if not open_ids:
            status, reason = "settled", "all linked forecasts are resolved"
        elif today > end:
            status = "expired"
            reason = f"window ended {end} with {len(open_ids)} unresolved forecast(s); the catalyst may have slipped beyond the horizon"
        elif today >= start:
            status, reason = "due", f"inside the watch window {start}..{end}"
        else:
            status, reason = "pending", f"window opens {start}"
        out.append({"contract_id": contract["contract_id"], "status": status, "reason": reason, "open_forecast_ids": open_ids})
    return out


def expire_overdue_forecasts(store: ThesisStore, ticker: str, as_of: str, grace_days: int = EXPIRY_GRACE_DAYS) -> list[str]:
    """Record ``expired_unresolved`` for forecasts well past their horizon with no resolution."""
    cutoff = (date.fromisoformat(str(as_of)[:10]) - timedelta(days=grace_days)).isoformat()
    expired = []
    for forecast in store.unresolved_forecasts(ticker, as_of):
        if forecast.get("horizon_end") and forecast["horizon_end"] < cutoff:
            store.record_resolution(
                forecast["forecast_id"],
                outcome="expired_unresolved",
                as_of=as_of,
                resolver="system",
                notes=f"no resolution recorded within {grace_days} days after {forecast['horizon_end']}",
            )
            expired.append(forecast["forecast_id"])
    return expired
