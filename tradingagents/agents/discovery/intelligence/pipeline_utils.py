from __future__ import annotations
"""
Pipeline Utilities:
General technical and numerical utilities required across different stages of the discovery pipeline.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .pipeline_cache import (
    load_cache_value,
    save_cache_value,
    stable_key,
)


def strip_markdown_json_fence(text: str) -> str:
    content = text.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content


def parse_json_dict(text: str) -> Optional[Dict[str, Any]]:
    content = strip_markdown_json_fence(text)
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        try:
            parsed = json.loads(json_match.group())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ohlcv_rows(raw_csv: str) -> List[Dict[str, Any]]:
    """Parse canonical OHLCV CSV payload into row dicts."""
    lines = [l for l in str(raw_csv).split("\n") if l.strip() and not l.startswith("#")]
    if len(lines) < 2:
        return []

    header = [h.strip() for h in lines[0].split(",")]
    idx = {name: i for i, name in enumerate(header)}
    if "Close" not in idx:
        return []

    out: List[Dict[str, Any]] = []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]

        def _get(name: str) -> Optional[str]:
            i = idx.get(name)
            if i is None or i >= len(parts):
                return None
            return parts[i]

        row: Dict[str, Any] = {}
        dt = _get("Date")
        if dt is not None:
            row["Date"] = dt

        for field in ("Open", "High", "Low", "Close", "Volume"):
            raw_val = _get(field)
            row[field] = safe_float(raw_val) if raw_val is not None else None
        out.append(row)
    return out


def parse_ohlc_rows(raw_csv: str) -> List[Dict[str, float]]:
    """Extract rows with valid High/Low/Close numeric fields."""
    out: List[Dict[str, float]] = []
    for row in parse_ohlcv_rows(raw_csv):
        high = row.get("High")
        low = row.get("Low")
        close = row.get("Close")
        if high is None or low is None or close is None:
            continue
        out.append(
            {
                "high": float(high),
                "low": float(low),
                "close": float(close),
            }
        )
    return out


def parse_daily_dollar_volumes(raw_csv: str) -> List[float]:
    """Extract daily close*volume values from OHLCV CSV payload."""
    out: List[float] = []
    for row in parse_ohlcv_rows(raw_csv):
        close = row.get("Close")
        volume = row.get("Volume")
        if close is None or volume is None:
            continue
        close_val = float(close)
        volume_val = float(volume)
        if close_val <= 0 or volume_val < 0:
            continue
        out.append(close_val * volume_val)
    return out


def parse_price_volume_csv(raw_csv: str) -> Tuple[List[float], List[float]]:
    prices: List[float] = []
    volumes: List[float] = []
    for row in parse_ohlcv_rows(raw_csv):
        close_val = row.get("Close")
        if close_val is None:
            continue
        prices.append(float(close_val))
        vol_val = row.get("Volume")
        if vol_val is not None:
            volumes.append(float(vol_val))
    return prices, volumes


def linear_regression_slope(values: List[float]) -> float:
    """Slope of linear regression over a sequence where x is the index."""
    n = float(len(values))
    if n < 2:
        return 0.0
    x_mean = (n - 1.0) / 2.0
    y_mean = sum(values) / n
    numerator = 0.0
    denominator = 0.0
    for i, y in enumerate(values):
        dx = i - x_mean
        numerator += dx * (y - y_mean)
        denominator += dx * dx
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_obv_series(prices: List[float], volumes: List[float], window: Optional[int] = None) -> List[float]:
    """Compute OBV time series from aligned price and volume arrays."""
    n = min(len(prices), len(volumes))
    if window is not None and window > 0:
        n = min(n, int(window))
    if n <= 0:
        return []
    p = prices[-n:]
    v = volumes[-n:]
    out = [0.0]
    for i in range(1, n):
        if p[i] > p[i - 1]:
            out.append(out[-1] + v[i])
        elif p[i] < p[i - 1]:
            out.append(out[-1] - v[i])
        else:
            out.append(out[-1])
    return out


def compute_obv_slope(prices: List[float], volumes: List[float], window: int = 10) -> float:
    """OBV trend slope over the requested trailing window."""
    if window <= 1:
        return 0.0
    if len(prices) < window or len(volumes) < window:
        return 0.0
    return linear_regression_slope(compute_obv_series(prices, volumes, window=window))


def extract_indicator_value(raw_text: str) -> Optional[float]:
    if not raw_text:
        return None

    lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
    for line in reversed(lines):
        if ":" in line:
            val_str = line.split(":")[-1].strip()
            try:
                return float(val_str)
            except ValueError:
                pass
        try:
            return float(line)
        except ValueError:
            continue

    match = re.search(r"[-+]?\d+\.?\d*", raw_text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_linear(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    pct = (value - low) / (high - low)
    return 100.0 * clamp(pct, 0.0, 1.0)


def compute_return_pct(prices: List[float], period: int) -> Optional[float]:
    """Return percent change over *period* sessions using period+1 bars."""
    if period <= 0 or len(prices) < period + 1:
        return None
    prev = prices[-(period + 1)]
    cur = prices[-1]
    if prev == 0:
        return None
    return ((cur - prev) / prev) * 100.0


def fetch_alpaca_tradeable_assets(
    trade_date: Optional[str] = None,
    min_avg_dollar_volume_20d: float = 10_000_000.0,
    dollar_volume_lookback_days: int = 20,
    max_workers: int = 6,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> List[str]:
    from .universe_prefilters import (
        filter_by_avg_daily_dollar_volume,
    )

    symbols = fetch_alpaca_primary_us_equities(
        trade_date=trade_date,
        cache_config=cache_config,
        metrics=metrics,
    )
    symbols = filter_by_avg_daily_dollar_volume(
        symbols=symbols,
        trade_date=trade_date,
        min_avg_dollar_volume_20d=min_avg_dollar_volume_20d,
        lookback_days=dollar_volume_lookback_days,
        max_workers=max_workers,
        cache_config=cache_config,
        metrics=metrics,
    )
    if not symbols:
        raise RuntimeError(
            "Alpaca tradable asset universe is empty after ADV prefilter "
            f"(ADV{dollar_volume_lookback_days} >= {min_avg_dollar_volume_20d:,.0f}). "
            "Verify data access, date window, and liquidity threshold."
        )
    return symbols


def fetch_alpaca_primary_us_equities(
    trade_date: Optional[str] = None,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> List[str]:
    try:
        from alpaca.trading.client import TradingClient  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Alpaca universe fetch requires 'alpaca-py'. Install it to enable numeric filtering."
        ) from e

    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret_key = (
        os.getenv("APCA_API_SECRET_KEY")
        or os.getenv("ALPACA_API_SECRET")
        or os.getenv("ALPACA_SECRET_KEY")
    )
    if not api_key or not secret_key:
        raise RuntimeError(
            "Missing Alpaca credentials for universe scan. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY."
        )

    cache_key = stable_key(
        {
            "type": "tradeable_primary_us_equities",
            "trade_date": str(trade_date or ""),
        }
    )
    cached, hit = load_cache_value(
        namespace="stage0_tradeable_primary_us_equities",
        key=cache_key,
        cache_config=cache_config,
        metrics=metrics,
    )
    if hit and isinstance(cached, list):
        return [str(s).strip().upper() for s in cached if str(s).strip()]

    try:
        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
        assets = client.get_all_assets()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Alpaca tradable assets: {e}") from e

    from .universe_prefilters import filter_tradeable_primary_us_equities

    exchange_filtered_symbols = filter_tradeable_primary_us_equities(assets)
    if not exchange_filtered_symbols:
        raise RuntimeError(
            "Alpaca tradable asset universe is empty after exchange/class prefilter "
            "(tradable, active, us_equity, NYSE/NASDAQ). Verify API access and account permissions."
        )
    save_cache_value(
        namespace="stage0_tradeable_primary_us_equities",
        key=cache_key,
        value=exchange_filtered_symbols,
        cache_config=cache_config,
    )
    return exchange_filtered_symbols
