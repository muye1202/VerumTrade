from __future__ import annotations
"""
Feature Matrix:
Data structures and tools to define the system feature matrix contract utilized throughout discovery.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .pipeline_models import FeatureRow
from .pipeline_cache import load_cache_value, save_cache_value, stable_key
from .pipeline_utils import parse_price_volume_csv


def build_ohlcv_cache(
    universe: List[str],
    trade_date: str,
    lookback_days: int = 380,
    max_workers: int = 8,
    cache_config: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Fetch and cache OHLCV csv payloads for a ticker universe."""
    from tradingagents.dataflows.interface import route_to_vendor

    symbols = sorted({
        str(t).strip().upper()
        for t in (universe or [])
        if str(t).strip()
    })
    out: Dict[str, str] = {}
    metrics: Dict[str, Any] = {
        "symbols": len(symbols),
        "vendor_calls_estimate": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "rows_with_data": 0,
    }
    if not symbols:
        return out, metrics

    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=max(30, int(lookback_days)))).strftime("%Y-%m-%d")

    cache_cfg = cache_config or {}

    def _fetch(symbol: str) -> Tuple[str, Optional[str], bool, bool]:
        cache_key = stable_key(
            {
                "type": "feature_matrix_ohlcv",
                "symbol": symbol,
                "start": start_date,
                "end": trade_date,
            }
        )
        cached, hit = load_cache_value(
            namespace="feature_matrix_ohlcv",
            key=cache_key,
            cache_config=cache_cfg,
        )
        if hit and isinstance(cached, str) and cached.strip():
            return symbol, cached, False, True
        try:
            raw_csv = route_to_vendor("get_stock_data", symbol, start_date, trade_date)
            payload = str(raw_csv or "")
            if payload.strip():
                save_cache_value(
                    namespace="feature_matrix_ohlcv",
                    key=cache_key,
                    value=payload,
                    cache_config=cache_cfg,
                )
            return symbol, payload, True, False
        except Exception:
            return symbol, None, True, False

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_fetch, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            raw = None
            vendor_called = False
            cache_hit = False
            try:
                _, raw, vendor_called, cache_hit = future.result()
            except Exception:
                raw = None
                vendor_called = True
            if vendor_called:
                metrics["vendor_calls_estimate"] = int(metrics.get("vendor_calls_estimate", 0)) + 1
                metrics["cache_misses"] = int(metrics.get("cache_misses", 0)) + 1
            if cache_hit:
                metrics["cache_hits"] = int(metrics.get("cache_hits", 0)) + 1
            if raw:
                out[symbol] = raw
                prices, _ = parse_price_volume_csv(raw)
                if prices:
                    metrics["rows_with_data"] = int(metrics.get("rows_with_data", 0)) + 1
    return out, metrics


def feature_rows_from_ohlcv_cache(
    ohlcv_cache: Dict[str, str],
) -> List[FeatureRow]:
    """Convert OHLCV cache payloads into canonical feature rows."""
    rows: List[FeatureRow] = []
    for ticker, raw_csv in sorted((ohlcv_cache or {}).items()):
        prices, volumes = parse_price_volume_csv(str(raw_csv or ""))
        rows.append(
            FeatureRow(
                ticker=str(ticker).strip().upper(),
                prices=prices,
                volumes=volumes,
                indicators={},
                data_quality_flags=[] if prices else ["missing_ohlcv"],
            )
        )
    return rows
