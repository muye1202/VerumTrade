from datetime import date

from fastapi import APIRouter, HTTPException

from verumtrade.research.revision import get_thesis_store, thesis_store_enabled
from verumtrade.research.watch_contract import evaluate_watch_contracts

router = APIRouter()


@router.get("/theses/{ticker}")
def get_thesis_history(ticker: str, as_of: str | None = None):
    """Thesis versions, change records, frozen forecasts with resolutions, and watch contracts."""
    if not thesis_store_enabled():
        raise HTTPException(status_code=404, detail="Thesis store is disabled")
    as_of = as_of or date.today().isoformat()
    store = get_thesis_store()
    versions = [v for v in store.versions(ticker) if v["as_of"] <= as_of]
    if not versions:
        raise HTTPException(status_code=404, detail=f"No thesis recorded for {ticker.upper()}")

    forecasts = store.forecasts(ticker, as_of=as_of)
    resolutions = {r["forecast_id"]: r for r in store.resolutions([f["forecast_id"] for f in forecasts], as_of=as_of)}
    contracts = store.watch_contracts(ticker, as_of=as_of)
    unresolved = {f["forecast_id"] for f in forecasts if f["forecast_id"] not in resolutions}
    statuses = {c["contract_id"]: c for c in evaluate_watch_contracts(contracts, unresolved, as_of)}
    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "versions": [
            {
                "thesis_id": v["thesis_id"],
                "version": v["version"],
                "as_of": v["as_of"],
                "created_at": v["created_at"],
                "action": v["action"],
                "execution_mode": v["execution_mode"],
                "thesis": v["thesis"],
                "levels": v["levels"],
                "change_record": v["change_record"],
            }
            for v in versions
        ],
        "status_events": store.status_events(versions[-1]["thesis_id"]),
        "forecasts": [
            {
                **{k: f[k] for k in ("forecast_id", "version", "kind", "proposition", "horizon_end",
                                     "resolution_rule", "probability", "interval_low", "interval_high", "basis",
                                     "created_at", "content_hash", "integrity_ok")},
                "resolution": resolutions.get(f["forecast_id"]),
            }
            for f in forecasts
        ],
        "watch_contracts": [{**c, **statuses.get(c["contract_id"], {})} for c in contracts],
        "scorecard": store.forecast_scorecard(ticker, as_of=as_of),
    }
