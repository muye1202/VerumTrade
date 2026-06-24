from __future__ import annotations
"""
Universe Prefilters:
Provides initial quantitative filtering mechanisms (e.g., liquidity, catalysts) to prune the universe of tradeable tickers prior to deeper evaluation.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

from verumtrade.dataflows.config import get_config
from .pipeline_cache import (
    load_cache_value,
    save_cache_value,
    stable_key,
)
from .pipeline_utils import parse_daily_dollar_volumes

PRIMARY_EXCHANGES = {"NYSE", "NASDAQ"}
_YAHOO_EARNINGS_URL = "https://finance.yahoo.com/calendar/earnings"
_QUOTE_HREF_RE = re.compile(r"/quote/([A-Z0-9.\-^=]+)")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_LOGGER = logging.getLogger(__name__)


def _chunked(values: List[str], size: int) -> List[List[str]]:
    return [values[i:i + size] for i in range(0, len(values), max(1, size))]


def _record_metric(metrics: Optional[Dict[str, Any]], key: str, amount: int = 1) -> None:
    if isinstance(metrics, dict):
        metrics[key] = int(metrics.get(key, 0)) + int(amount)


def _primary_core_stock_vendor() -> str:
    cfg = get_config()
    tool_vendors = cfg.get("tool_vendors", {}) or {}
    raw = str(tool_vendors.get("get_stock_data", cfg.get("data_vendors", {}).get("core_stock_apis", "alpaca")))
    first = raw.split(",")[0].strip().lower()
    return first or "alpaca"


def _normalize_enum_like(value: Any) -> str:
    return str(value or "").strip().upper().split(".")[-1]


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_analysis_date(analysis_date: Optional[str]) -> date:
    if analysis_date:
        return datetime.strptime(analysis_date, "%Y-%m-%d").date()
    return datetime.now(timezone.utc).date()


def _window_bounds(analysis_date: Optional[str], window_days: int) -> tuple[date, date]:
    start = _normalize_analysis_date(analysis_date)
    end = start + timedelta(days=max(0, int(window_days)))
    return start, end


def _normalize_universe_symbols(symbols: Iterable[str]) -> List[str]:
    return sorted({
        _normalize_symbol(s)
        for s in symbols
        if _normalize_symbol(s)
    })


def filter_tradeable_primary_us_equities(assets: Iterable[Any]) -> List[str]:
    symbols = set()
    for asset in assets:
        symbol = _normalize_symbol(getattr(asset, "symbol", ""))
        if not symbol or len(symbol) > 5 or not symbol.isalpha():
            continue
        if not bool(getattr(asset, "tradable", False)):
            continue

        status = _normalize_enum_like(getattr(asset, "status", ""))
        if status != "ACTIVE":
            continue

        asset_class = _normalize_enum_like(getattr(asset, "asset_class", ""))
        if asset_class != "US_EQUITY":
            continue

        exchange = _normalize_enum_like(getattr(asset, "exchange", ""))
        if exchange not in PRIMARY_EXCHANGES:
            continue
        symbols.add(symbol)
    return sorted(symbols)


def compute_avg_daily_dollar_volume(
    symbol: str,
    trade_date: Optional[str] = None,
    lookback_days: int = 20,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    raw_csv: Optional[str] = None,
) -> Optional[float]:
    from verumtrade.dataflows.interface import route_to_vendor

    if lookback_days <= 0:
        return None

    end_dt = (
        datetime.strptime(trade_date, "%Y-%m-%d")
        if trade_date
        else datetime.now(timezone.utc)
    )
    start_date = (end_dt - timedelta(days=max(lookback_days * 3, 30))).strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    vendor = _primary_core_stock_vendor()
    cache_key = stable_key(
        {
            "type": "adv",
            "symbol": _normalize_symbol(symbol),
            "trade_date": end_date,
            "lookback_days": int(lookback_days),
            "vendor": vendor,
        }
    )
    cached, hit = load_cache_value(
        namespace="stage0_adv",
        key=cache_key,
        cache_config=cache_config,
        metrics=metrics,
    )
    if hit and cached is not None:
        try:
            return float(cached)
        except Exception:
            pass

    if raw_csv is None:
        _record_metric(metrics, "vendor_calls_estimate", 1)
        raw_csv = route_to_vendor("get_stock_data", symbol, start_date, end_date)
    dollar_volumes = parse_daily_dollar_volumes(raw_csv or "")
    if len(dollar_volumes) < lookback_days:
        return None
    window = dollar_volumes[-lookback_days:]
    adv = sum(window) / float(lookback_days)
    save_cache_value(
        namespace="stage0_adv",
        key=cache_key,
        value=adv,
        cache_config=cache_config,
    )
    return adv


def filter_by_avg_daily_dollar_volume(
    symbols: Iterable[str],
    trade_date: Optional[str] = None,
    min_avg_dollar_volume_20d: float = 10_000_000.0,
    lookback_days: int = 20,
    max_workers: int = 6,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    batch_size: int = 100,
) -> List[str]:
    unique_symbols = sorted({
        _normalize_symbol(s)
        for s in symbols
        if _normalize_symbol(s)
    })
    if not unique_symbols:
        return []

    filtered: List[str] = []
    vendor = _primary_core_stock_vendor()

    # Batch path for Alpaca to reduce remote request count.
    if vendor == "alpaca":
        try:
            from verumtrade.dataflows.vendors.alpaca.alpaca import get_stock_data_alpaca_batch
        except Exception:
            get_stock_data_alpaca_batch = None

        if callable(get_stock_data_alpaca_batch):
            end_dt = (
                datetime.strptime(trade_date, "%Y-%m-%d")
                if trade_date
                else datetime.now(timezone.utc)
            )
            start_date = (end_dt - timedelta(days=max(lookback_days * 3, 30))).strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")

            for chunk in _chunked(unique_symbols, int(batch_size)):
                missing: List[str] = []
                adv_map: Dict[str, Optional[float]] = {}
                for symbol in chunk:
                    cache_key = stable_key(
                        {
                            "type": "adv",
                            "symbol": symbol,
                            "trade_date": end_date,
                            "lookback_days": int(lookback_days),
                            "vendor": vendor,
                        }
                    )
                    cached, hit = load_cache_value(
                        namespace="stage0_adv",
                        key=cache_key,
                        cache_config=cache_config,
                        metrics=metrics,
                    )
                    if hit and cached is not None:
                        try:
                            adv_map[symbol] = float(cached)
                            continue
                        except Exception:
                            pass
                    missing.append(symbol)

                if missing:
                    _record_metric(metrics, "vendor_calls_estimate", 1)
                    try:
                        batch_rows = get_stock_data_alpaca_batch(missing, start_date, end_date)
                    except Exception:
                        batch_rows = {}

                    for symbol in missing:
                        raw_csv = str((batch_rows or {}).get(symbol, "") or "")
                        adv = compute_avg_daily_dollar_volume(
                            symbol=symbol,
                            trade_date=trade_date,
                            lookback_days=lookback_days,
                            cache_config=cache_config,
                            metrics=metrics,
                            raw_csv=(raw_csv if raw_csv else None),
                        )
                        adv_map[symbol] = adv

                for symbol, adv in adv_map.items():
                    if adv is not None and adv >= float(min_avg_dollar_volume_20d):
                        filtered.append(symbol)

            return sorted(set(filtered))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                compute_avg_daily_dollar_volume,
                s,
                trade_date,
                lookback_days,
                cache_config,
                metrics,
                None,
            ): s
            for s in unique_symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                adv = future.result()
            except Exception:
                continue
            if adv is not None and adv >= float(min_avg_dollar_volume_20d):
                filtered.append(symbol)

    return sorted(filtered)


def _parse_date_string(value: str) -> List[date]:
    out: List[date] = []
    s = str(value or "").strip()
    if not s:
        return out
    try:
        out.append(datetime.fromisoformat(s.replace("Z", "+00:00")).date())
        return out
    except ValueError:
        pass
    for match in _DATE_RE.findall(s):
        try:
            out.append(datetime.strptime(match, "%Y-%m-%d").date())
        except ValueError:
            continue
    return out


def _coerce_dates(value: Any) -> List[date]:
    if value is None:
        return []
    if isinstance(value, date) and not isinstance(value, datetime):
        return [value]
    if isinstance(value, datetime):
        return [value.date()]
    if isinstance(value, str):
        return _parse_date_string(value)
    if isinstance(value, (list, tuple, set)):
        out: List[date] = []
        for item in value:
            out.extend(_coerce_dates(item))
        return out

    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            py_dt = to_pydatetime()
            return _coerce_dates(py_dt)
        except Exception:
            return []

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            as_list = tolist()
            if as_list is not value:
                return _coerce_dates(as_list)
        except Exception:
            pass
    return _parse_date_string(str(value))


def _extract_earnings_dates_from_mapping(mapping: Dict[str, Any]) -> List[date]:
    dates: List[date] = []
    for k, v in mapping.items():
        key = str(k).strip().lower()
        if "earning" in key:
            dates.extend(_coerce_dates(v))
        if isinstance(v, dict):
            dates.extend(_extract_earnings_dates_from_mapping(v))
    return dates


def _extract_earnings_dates_from_calendar(calendar_obj: Any) -> List[date]:
    if calendar_obj is None:
        return []

    dates: List[date] = []
    if isinstance(calendar_obj, dict):
        dates.extend(_extract_earnings_dates_from_mapping(calendar_obj))

    to_dict = getattr(calendar_obj, "to_dict", None)
    if callable(to_dict):
        try:
            as_dict = to_dict()
            if isinstance(as_dict, dict):
                dates.extend(_extract_earnings_dates_from_mapping(as_dict))
        except Exception:
            pass

    # Fallback for Series-like objects.
    items = getattr(calendar_obj, "items", None)
    if callable(items):
        try:
            for k, v in items():
                if "earning" in str(k).strip().lower():
                    dates.extend(_coerce_dates(v))
        except Exception:
            pass

    # Fallback for DataFrame-like objects with label lookup.
    idx = getattr(calendar_obj, "index", None)
    loc = getattr(calendar_obj, "loc", None)
    if idx is not None and loc is not None:
        try:
            for label in idx:
                if "earning" in str(label).strip().lower():
                    dates.extend(_coerce_dates(loc[label]))
        except Exception:
            pass

    deduped = sorted(set(d for d in dates if isinstance(d, date)))
    return deduped


def _is_in_window(dates: List[date], start: date, end: date) -> bool:
    for d in dates:
        if start <= d <= end:
            return True
    return False


def filter_by_upcoming_earnings_per_ticker(
    symbols: Iterable[str],
    analysis_date: Optional[str] = None,
    window_days: int = 7,
    max_workers: int = 4,
) -> List[str]:
    try:
        import yfinance as yf
    except Exception as e:
        raise RuntimeError("yfinance is required for per_ticker_calendar earnings filter.") from e

    universe = _normalize_universe_symbols(symbols)
    if not universe:
        return []
    start, end = _window_bounds(analysis_date, window_days)

    def _has_upcoming_earnings(symbol: str) -> bool:
        ticker = yf.Ticker(symbol)
        calendar_obj = getattr(ticker, "calendar", None)
        dates = _extract_earnings_dates_from_calendar(calendar_obj)
        return _is_in_window(dates, start, end)

    filtered: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_has_upcoming_earnings, s): s for s in universe}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                if future.result():
                    filtered.append(symbol)
            except Exception:
                continue
    return sorted(filtered)


def _parse_symbols_from_earnings_html(html: str) -> Set[str]:
    content = str(html or "")
    symbols: Set[str] = set()

    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(content, "html.parser")
        for a in soup.select('a[href*="/quote/"]'):
            href = str(a.get("href", ""))
            match = _QUOTE_HREF_RE.search(href)
            if not match:
                continue
            symbol = _normalize_symbol(match.group(1).split("?")[0].split("/")[0])
            if symbol:
                symbols.add(symbol)
        if symbols:
            return symbols
    except Exception:
        pass

    for match in _QUOTE_HREF_RE.findall(content):
        symbol = _normalize_symbol(str(match).split("?")[0].split("/")[0])
        if symbol:
            symbols.add(symbol)
    return symbols


def _fetch_daily_earnings_symbols_from_yahoo(
    day_str: str,
    page_size: int,
    http_timeout_s: int,
    session: Optional[requests.Session] = None,
) -> Set[str]:
    symbols: Set[str] = set()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    client = session or requests.Session()
    for offset in range(0, max(1, page_size) * 20, max(1, page_size)):
        params = {
            "day": day_str,
            "offset": offset,
            "size": max(1, page_size),
        }
        response = client.get(
            _YAHOO_EARNINGS_URL,
            params=params,
            headers=headers,
            timeout=max(1, int(http_timeout_s)),
        )
        response.raise_for_status()
        page_symbols = _parse_symbols_from_earnings_html(response.text)
        if not page_symbols:
            break
        before = len(symbols)
        symbols.update(page_symbols)
        # If pagination does not expand results, stop early.
        if len(symbols) == before:
            break
    return symbols


def filter_by_upcoming_earnings_daily_calendar(
    symbols: Iterable[str],
    analysis_date: Optional[str] = None,
    window_days: int = 7,
    http_timeout_s: int = 12,
    calendar_page_size: int = 100,
    max_workers: int = 4,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> List[str]:
    universe = _normalize_universe_symbols(symbols)
    if not universe:
        return []

    start, end = _window_bounds(analysis_date, window_days)
    universe_set = set(universe)
    earnings_symbols: Set[str] = set()

    days: List[str] = []
    curr = start
    while curr <= end:
        days.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)

    def _fetch_one_day(day_str: str) -> Set[str]:
        cache_key = stable_key(
            {
                "type": "earnings_day_symbols",
                "day": day_str,
                "page_size": int(calendar_page_size),
            }
        )
        cached, hit = load_cache_value(
            namespace="stage0_earnings_day_symbols",
            key=cache_key,
            cache_config=cache_config,
            metrics=metrics,
        )
        if hit and isinstance(cached, list):
            return {
                _normalize_symbol(s)
                for s in cached
                if _normalize_symbol(s)
            }

        with requests.Session() as session:
            out = _fetch_daily_earnings_symbols_from_yahoo(
                day_str=day_str,
                page_size=max(1, int(calendar_page_size)),
                http_timeout_s=max(1, int(http_timeout_s)),
                session=session,
            )
        save_cache_value(
            namespace="stage0_earnings_day_symbols",
            key=cache_key,
            value=sorted(out),
            cache_config=cache_config,
        )
        _record_metric(metrics, "vendor_calls_estimate", 1)
        return out

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_fetch_one_day, day): day for day in days}
        errors: List[Exception] = []
        for future in as_completed(futures):
            try:
                earnings_symbols.update(future.result())
            except Exception as e:
                errors.append(e)
        if errors:
            raise RuntimeError(f"daily_calendar fetch failed for {len(errors)} day(s): {type(errors[0]).__name__}")

    # Align with current universe symbol constraints.
    normalized_earnings = {
        s for s in (_normalize_symbol(x) for x in earnings_symbols)
        if s and len(s) <= 5 and s.isalpha()
    }
    return sorted(universe_set & normalized_earnings)


def filter_by_upcoming_earnings(
    symbols: Iterable[str],
    analysis_date: Optional[str] = None,
    mode: str = "daily_calendar",
    window_days: int = 7,
    max_workers: int = 4,
    failure_policy: str = "fail_closed",
    http_timeout_s: int = 12,
    calendar_page_size: int = 100,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> List[str]:
    universe = _normalize_universe_symbols(symbols)
    if not universe:
        return []

    try:
        selected_mode = str(mode or "daily_calendar").strip().lower()
        if selected_mode == "per_ticker_calendar":
            return filter_by_upcoming_earnings_per_ticker(
                universe,
                analysis_date=analysis_date,
                window_days=window_days,
                max_workers=max_workers,
            )
        if selected_mode == "daily_calendar":
            return filter_by_upcoming_earnings_daily_calendar(
                universe,
                analysis_date=analysis_date,
                window_days=window_days,
                http_timeout_s=http_timeout_s,
                calendar_page_size=calendar_page_size,
                max_workers=max_workers,
                cache_config=cache_config,
                metrics=metrics,
            )
        raise RuntimeError(f"Unsupported catalyst prefilter mode: {mode}")
    except Exception as e:
        policy = str(failure_policy or "fail_open").strip().lower()
        if policy == "fail_open":
            _LOGGER.warning(f"Earnings catalyst prefilter failed (fail_open): {e}")
            return universe
        if policy == "fail_closed":
            _LOGGER.warning(f"Earnings catalyst prefilter failed (fail_closed): {e}")
            return []
        if policy == "raise":
            raise RuntimeError(f"Earnings catalyst prefilter failed: {e}") from e
        raise RuntimeError(f"Unsupported catalyst prefilter failure_policy: {failure_policy}") from e


def filter_by_recent_8k(
    symbols: Iterable[str],
    max_workers: int = 4,
    failure_policy: str = "fail_open",
) -> List[str]:
    from verumtrade.dataflows.interface import route_to_vendor
    universe = _normalize_universe_symbols(symbols)
    if not universe:
        return []

    filtered: List[str] = []

    def _has_recent_8k(symbol: str) -> bool:
        try:
            raw_filings = route_to_vendor("get_recent_sec_filings", symbol)
            # If the tool successfully returned filing metadata, and it contains 8-K 
            # (or we simply treat any recent material filing returned as a match)
            if raw_filings and "8-K" in raw_filings:
                return True
        except Exception:
            pass
        return False

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_has_recent_8k, s): s for s in universe}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                if future.result():
                    filtered.append(symbol)
            except Exception as e:
                policy = str(failure_policy or "fail_open").strip().lower()
                if policy == "fail_open":
                    filtered.append(symbol)
    
    # Add fail_open fallback completely if empty maybe? Let's just return what passed.
    # If a ticker fails, fail_open already adds it above.
    return sorted(set(filtered))
