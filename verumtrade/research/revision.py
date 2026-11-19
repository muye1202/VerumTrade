"""Thesis revision across runs: prior context in, versioned thesis and forecasts out."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Callable

from verumtrade.research.calculations import parse_closes_csv
from verumtrade.research.thesis_diff import (
    assess_dependency_impact,
    build_change_record,
    cited_source_map,
    evidence_snapshot,
)
from verumtrade.research.thesis_store import ThesisStore
from verumtrade.research.watch_contract import (
    build_watch_contract,
    evaluate_watch_contracts,
    expire_overdue_forecasts,
    forecasts_from_thesis,
)

logger = logging.getLogger(__name__)

RESOLVABLE_KINDS = {"event", "event_timing"}
RESOLUTION_OUTCOMES = {"true", "false", "ambiguous"}

_store: ThesisStore | None = None


def thesis_store_enabled(config: dict[str, Any] | None = None) -> bool:
    from verumtrade.dataflows.config import get_config

    return bool(((config or get_config()).get("thesis_store") or {}).get("enabled", False))


def get_thesis_store() -> ThesisStore:
    global _store
    if _store is None:
        _store = ThesisStore()
    return _store


def horizon_end_for(state: dict[str, Any]) -> str:
    from verumtrade.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec

    spec = get_time_horizon_spec(state.get("time_horizon"))
    start = date.fromisoformat(str(state.get("trade_date"))[:10])
    return (start + timedelta(days=int(spec.weeks_range[1] * 7))).isoformat()


def prepare_revision_context(state: dict[str, Any], store: ThesisStore) -> dict[str, Any]:
    ticker = str(state.get("company_of_interest") or "").upper()
    as_of = str(state.get("trade_date") or "")[:10]
    prior = store.latest_version(ticker, as_of)
    impact = assess_dependency_impact(prior, list(state.get("evidence_ledger") or []))
    unresolved = store.unresolved_forecasts(ticker, as_of)
    open_forecasts = [
        {
            "forecast_id": f["forecast_id"],
            "kind": f["kind"],
            "proposition": f["proposition"],
            "horizon_end": f["horizon_end"],
            "resolution_rule": f["resolution_rule"],
            "status": "due" if (f.get("horizon_end") or "") <= as_of else "open",
        }
        for f in unresolved
        if f["kind"] in RESOLVABLE_KINDS and f["as_of"] < as_of
    ]
    contracts = evaluate_watch_contracts(
        store.watch_contracts(ticker, as_of=as_of), {f["forecast_id"] for f in unresolved}, as_of
    )
    return {
        "prior": (
            {k: prior.get(k) for k in ("thesis_id", "version", "as_of", "action", "execution_mode", "thesis", "levels", "cited_sources")}
            if prior
            else None
        ),
        "impact": impact,
        "open_forecasts": open_forecasts,
        "watch_contracts": contracts,
    }


def format_revision_context(context: dict[str, Any] | None) -> str:
    if not context or not context.get("prior"):
        return "PRIOR THESIS: none (this is the first thesis for this ticker)."
    prior = context["prior"]
    impact = context.get("impact") or {}
    lines = [
        f"PRIOR THESIS (version {prior['version']}, as of {prior['as_of']}; decision {prior['action']}/{prior['execution_mode']}):",
        json.dumps(prior.get("thesis") or {}, ensure_ascii=False)[:5000],
        "",
        f"DEPENDENCY IMPACT (evidence refresh: {impact.get('refresh_status')}):",
        f"- affected (cited evidence changed or was restated): {impact.get('affected') or 'none'}",
        f"- unverified (cited evidence not refreshed this run): {impact.get('unverified') or 'none'}",
        f"- unaffected: {impact.get('unaffected') or 'none'}",
        "Revise affected components; keep unaffected premises and scenarios unless new evidence contradicts them.",
        "Do not treat a failed or partial refresh as confirmation that nothing changed.",
    ]
    for contract in context.get("watch_contracts") or []:
        lines.append(f"- watch contract {contract['contract_id']}: {contract['status']} ({contract['reason']})")
    if context.get("open_forecasts"):
        lines.append("")
        lines.append(
            "OPEN FORECASTS from earlier versions. Resolve only those the cited evidence settles, by adding "
            '"forecast_resolutions": [{"forecast_id": "...", "outcome": "true|false|ambiguous", '
            '"evidence_ids": ["E-..."], "notes": "..."}] to the JSON. Do not change the forecasts themselves.'
        )
        for forecast in context["open_forecasts"]:
            lines.append(
                f"- {forecast['forecast_id']} [{forecast['status']}, by {forecast['horizon_end']}]: "
                f"{forecast['proposition']} | rule: {forecast['resolution_rule']}"
            )
    return "\n".join(lines)


def validate_forecast_resolutions(thesis: dict[str, Any], context: dict[str, Any] | None) -> list[str]:
    allowed = {f["forecast_id"] for f in (context or {}).get("open_forecasts") or []}
    violations = []
    for idx, item in enumerate(thesis.get("forecast_resolutions") or []):
        label = f"forecast_resolutions[{idx}]"
        if not isinstance(item, dict):
            violations.append(f"{label} must be an object")
            continue
        if item.get("forecast_id") not in allowed:
            violations.append(f"{label}.forecast_id is not an open forecast")
        if item.get("outcome") not in RESOLUTION_OUTCOMES:
            violations.append(f"{label}.outcome must be one of {sorted(RESOLUTION_OUTCOMES)}")
        if item.get("outcome") in {"true", "false"} and not item.get("evidence_ids"):
            violations.append(f"{label} resolves true/false and must cite the evidence that settles it")
    return violations


def _load_closes(ticker: str, start: str, end: str, fetch: Callable[..., str] | None) -> dict[str, float]:
    if fetch is None:
        from verumtrade.dataflows.interface import route_to_vendor_raw

        raw = route_to_vendor_raw("get_stock_data", ticker, start, end)
    else:
        raw = fetch(ticker, start, end)
    dates, closes = parse_closes_csv(str(raw))
    return dict(zip(dates, closes))


def _close_on_or_before(closes: dict[str, float], day: str) -> float | None:
    eligible = [d for d in closes if d and d <= day]
    return closes[max(eligible)] if eligible else None


def resolve_price_reaction_forecasts(
    store: ThesisStore,
    ticker: str,
    as_of: str,
    *,
    fetch: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve conditional price-reaction forecasts from closes, only after their condition resolved."""
    forecasts = store.forecasts(ticker, as_of=as_of)
    outcomes = {r["forecast_id"]: r["outcome"] for r in store.resolutions([f["forecast_id"] for f in forecasts], as_of=as_of)}
    recorded = []
    closes: dict[str, float] | None = None
    for forecast in forecasts:
        fid = forecast["forecast_id"]
        if forecast["kind"] != "conditional_price_reaction" or fid in outcomes:
            continue
        event_id = fid.removesuffix("-reaction") + "-event"
        condition = outcomes.get(event_id)
        if condition in {"false", "expired_unresolved", "void"}:
            recorded.append(store.record_resolution(
                fid, outcome="void", as_of=as_of, resolver="system",
                notes=f"condition {event_id} resolved {condition}; the reaction forecast does not apply",
            ))
            continue
        if condition != "true" or (forecast.get("horizon_end") or "9999") > as_of:
            continue
        if forecast.get("interval_low") is None or forecast.get("interval_high") is None:
            continue
        if closes is None:
            closes = _load_closes(ticker, forecast["as_of"], as_of, fetch)
        start_close = _close_on_or_before(closes, forecast["as_of"])
        end_close = _close_on_or_before(closes, forecast["horizon_end"])
        if not start_close or not end_close:
            continue
        realized = end_close / start_close - 1.0
        inside = forecast["interval_low"] <= realized <= forecast["interval_high"]
        recorded.append(store.record_resolution(
            fid,
            outcome="true" if inside else "false",
            as_of=as_of,
            resolver="system",
            evidence=[{"kind": "price_closes", "start_close": start_close, "end_close": end_close,
                       "realized_return": round(realized, 4)}],
            notes=f"realized {realized:.2%} vs interval [{forecast['interval_low']}, {forecast['interval_high']}]",
        ))
    return recorded


def persist_thesis_run(
    state: dict[str, Any],
    store: ThesisStore,
    *,
    price_fetch: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Append this run's thesis version, forecasts, watch contract and resolutions."""
    ticker = str(state.get("company_of_interest") or "").upper()
    as_of = str(state.get("trade_date") or "")[:10]
    validation = state.get("swing_thesis_validation") or {}
    thesis = state.get("swing_thesis") or {}
    ledger = list(state.get("evidence_ledger") or [])
    prior = store.latest_version(ticker, as_of)
    summary: dict[str, Any] = {"ticker": ticker, "as_of": as_of, "persisted": False}

    if not validation.get("valid") or not thesis:
        if prior:
            store.add_status_event(
                prior["thesis_id"], prior["version"], "not_revised",
                f"run on {as_of} produced no valid thesis; prior version stands unconfirmed",
            )
        summary["reason"] = "no valid thesis"
        summary["expired_forecasts"] = expire_overdue_forecasts(store, ticker, as_of)
        return summary

    cited = cited_source_map(thesis, ledger)
    snapshot = evidence_snapshot(cited, ledger)
    levels = state.get("thesis_levels") or {}
    change_record = state.get("thesis_change_record") or build_change_record(
        prior, thesis, impact=assess_dependency_impact(prior, ledger), levels=levels, cited=cited
    )
    created = store.append_version(
        ticker=ticker,
        as_of=as_of,
        thesis=thesis,
        validation=validation,
        levels={k: levels.get(k) for k in ("reference_price", "target_price", "invalidation_price", "reward_risk")},
        cited_sources=cited,
        evidence_snapshot=snapshot,
        change_record=change_record,
        prior=prior,
        run_id=str(state.get("run_id") or ""),
    )

    resolutions = []
    evidence_by_id = {str(item.get("evidence_id")): item for item in ledger}
    for item in thesis.get("forecast_resolutions") or []:
        try:
            resolutions.append(store.record_resolution(
                item["forecast_id"],
                outcome=item["outcome"],
                as_of=as_of,
                resolver="thesis_compiler",
                evidence=[
                    {"evidence_id": eid, "source_ref": (evidence_by_id.get(eid) or {}).get("source_ref"),
                     "claim": str((evidence_by_id.get(eid) or {}).get("claim") or "")[:300]}
                    for eid in item.get("evidence_ids") or []
                ],
                notes=str(item.get("notes") or ""),
            ))
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping forecast resolution %s: %s", item.get("forecast_id"), exc)

    horizon_end = horizon_end_for(state)
    forecasts = forecasts_from_thesis(
        thesis,
        thesis_id=created["thesis_id"],
        version=created["version"],
        ticker=ticker,
        as_of=as_of,
        horizon_end=horizon_end,
        price_context=state.get("price_context") or {},
    )
    store.add_forecasts(forecasts)
    contract = build_watch_contract(
        thesis, forecasts, thesis_id=created["thesis_id"], version=created["version"],
        ticker=ticker, as_of=as_of, horizon_end=horizon_end,
    )
    store.add_watch_contract(contract)
    expired = expire_overdue_forecasts(store, ticker, as_of)
    try:
        reactions = resolve_price_reaction_forecasts(store, ticker, as_of, fetch=price_fetch)
    except Exception as exc:  # price data is optional for persistence
        logger.warning("Price-reaction resolution skipped: %s", exc)
        reactions = []
    summary.update(
        {
            "persisted": True,
            **created,
            "verdict": change_record.get("verdict"),
            "forecasts_created": len(forecasts),
            "contract_id": contract["contract_id"],
            "resolutions_recorded": len(resolutions),
            "reaction_resolutions": len(reactions),
            "expired_forecasts": expired,
        }
    )
    return summary
