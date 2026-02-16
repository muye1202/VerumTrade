from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .universe_prefilters import (
    filter_by_avg_daily_dollar_volume,
    filter_tradeable_primary_us_equities,
)
from .stage0_cache import (
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


def parse_price_volume_csv(raw_csv: str) -> Tuple[List[float], List[float]]:
    lines = [l for l in str(raw_csv).split("\n") if l.strip() and not l.startswith("#")]
    if len(lines) < 3:
        return [], []

    header = [h.strip() for h in lines[0].split(",")]
    if "Close" not in header:
        return [], []
    close_idx = header.index("Close")
    vol_idx = header.index("Volume") if "Volume" in header else None

    prices: List[float] = []
    volumes: List[float] = []
    for line in lines[1:]:
        parts = line.split(",")
        if close_idx >= len(parts):
            continue
        close_val = safe_float(parts[close_idx])
        if close_val is None:
            continue
        prices.append(close_val)
        if vol_idx is not None and vol_idx < len(parts):
            vol_val = safe_float(parts[vol_idx])
            if vol_val is not None:
                volumes.append(vol_val)
    return prices, volumes


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


def fetch_alpaca_tradeable_assets(
    trade_date: Optional[str] = None,
    min_avg_dollar_volume_20d: float = 10_000_000.0,
    dollar_volume_lookback_days: int = 20,
    max_workers: int = 6,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> List[str]:
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
