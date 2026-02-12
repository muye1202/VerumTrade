from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Optional
import asyncio

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@dataclass(frozen=True)
class _PriceCols:
    date: str = "Date"
    open: str = "Open"
    high: str = "High"
    low: str = "Low"
    close: str = "Close"
    volume: str = "Volume"


def _strip_header_comments(text: str) -> str:
    lines = []
    for line in str(text).splitlines():
        if line.lstrip().startswith("#"):
            continue
        if not line.strip():
            continue
        lines.append(line)
    return "\n".join(lines)


def _parse_ohlcv_csv(text: str):
    try:
        import pandas as pd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("pandas is required to parse OHLCV data.") from e

    csv_body = _strip_header_comments(text)
    if not csv_body.strip():
        raise ValueError("Empty OHLCV payload.")

    df = pd.read_csv(io.StringIO(csv_body))

    # Normalize a few known schemas:
    # - yfinance: index column is a date; after to_csv it becomes unnamed first column.
    # - alpaca provider: explicit Date column exists.
    if "Date" not in df.columns:
        first = df.columns[0]
        if str(first).lower() in {"date", "datetime", "timestamp", "time"}:
            df = df.rename(columns={first: "Date"})
        else:
            # yfinance CSV usually has an unnamed first column for the index.
            df = df.rename(columns={first: "Date"})

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")

    # Standardize column capitalization when possible (some vendors may vary).
    rename_map = {}
    for col in df.columns:
        lc = str(col).strip().lower()
        if lc == "open":
            rename_map[col] = "Open"
        elif lc == "high":
            rename_map[col] = "High"
        elif lc == "low":
            rename_map[col] = "Low"
        elif lc in {"close", "adj close", "adj_close", "adjusted_close"}:
            # Prefer Close if present; otherwise map adjusted close to Close.
            if "Close" not in df.columns:
                rename_map[col] = "Close"
        elif lc == "volume":
            rename_map[col] = "Volume"
    if rename_map:
        df = df.rename(columns=rename_map)

    df = df.set_index("Date")
    df.index.name = "Date"
    return df


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    try:
        return f"{x * 100:.2f}%"
    except Exception:
        return "N/A"


def _num(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return "N/A"


def _last_n_series(df, col: str, n: int):
    s = df[col].dropna()
    if len(s) < 2:
        return None
    return s.iloc[-n:] if len(s) >= n else s


@tool
async def get_price_action_summary(
    symbol: Annotated[str, "Ticker symbol, e.g. AAPL"],
    curr_date: Annotated[str, "Current trading date (YYYY-mm-dd)"],
    look_back_days: Annotated[int, "Calendar days to look back for context"] = 180,
    vol_window_days: Annotated[int, "Trading days used for vol/volume stats"] = 20,
) -> str:
    """
    Compute short-term (swing-trade) price/volatility/liquidity metrics from daily OHLCV.

    This tool fetches OHLCV via the configured core_stock_apis vendor and returns a compact
    snapshot suitable for 1–2 month holding-period decisions.
    """
    try:
        import pandas as pd  # type: ignore
    except Exception as e:  # pragma: no cover
        return f"Error: pandas is required for get_price_action_summary: {e}"
    try:
        import numpy as np  # type: ignore
    except Exception as e:  # pragma: no cover
        return f"Error: numpy is required for get_price_action_summary: {e}"

    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    except Exception as e:
        return f"Error: curr_date must be YYYY-mm-dd, got '{curr_date}': {e}"

    start_date = (curr_dt - timedelta(days=int(look_back_days))).strftime("%Y-%m-%d")
    end_date = curr_dt.strftime("%Y-%m-%d")

    try:
        raw = await asyncio.to_thread(route_to_vendor, "get_stock_data", symbol, start_date, end_date)
    except Exception as e:
        return f"Error: failed to fetch OHLCV for {symbol} ({start_date} → {end_date}): {e}"
    if isinstance(raw, str) and raw.lower().startswith("no data found"):
        return str(raw)

    try:
        df = _parse_ohlcv_csv(raw)
    except Exception as e:
        return f"Error: failed to parse OHLCV CSV for {symbol}: {e}"

    cols = _PriceCols()
    required = [cols.open, cols.high, cols.low, cols.close, cols.volume]
    missing = [c for c in required if c not in df.columns]
    if missing:
        available = ", ".join(map(str, df.columns))
        return f"Error: OHLCV missing columns {missing}. Available: {available}"

    df = df[[cols.open, cols.high, cols.low, cols.close, cols.volume]].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=[cols.close])

    if df.empty or len(df) < 5:
        return f"Not enough OHLCV data to compute metrics for {symbol} ({start_date} → {end_date})."

    close = df[cols.close]
    high = df[cols.high]
    low = df[cols.low]
    open_ = df[cols.open]
    volume = df[cols.volume]

    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else None

    # Trading-day returns for horizons that map to 1–2 months.
    def ret_n(n: int) -> Optional[float]:
        if len(close) <= n:
            return None
        return float(close.iloc[-1] / close.iloc[-(n + 1)] - 1.0)

    ret_5d = ret_n(5)
    ret_21d = ret_n(21)  # ~1 month
    ret_42d = ret_n(42)  # ~2 months
    ret_63d = ret_n(63)  # ~3 months (context)

    # Realized volatility (log returns) annualized.
    rel = close / close.shift(1)
    logret = np.log(rel.where(rel > 0))
    vol_window = min(int(vol_window_days), max(2, len(logret.dropna())))
    rv_annual = None
    if vol_window and len(logret.dropna()) >= 2:
        rv_annual = float(logret.dropna().tail(vol_window).std(ddof=1) * (252 ** 0.5))

    # ATR(14)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_14 = float(tr.rolling(14, min_periods=5).mean().iloc[-1])
    atr_pct = atr_14 / last_close if last_close else None

    # Moving averages for trend context.
    ema_10 = close.ewm(span=10, adjust=False).mean().iloc[-1]
    sma_50 = close.rolling(50, min_periods=10).mean().iloc[-1]
    sma_200 = close.rolling(200, min_periods=50).mean().iloc[-1] if len(close) >= 50 else None

    # Volume & gap risk.
    vol_nonnull = volume.dropna()
    vol_window = min(int(vol_window_days), len(vol_nonnull))
    last_vol = float(vol_nonnull.iloc[-1]) if len(vol_nonnull) else None
    vol_avg = float(vol_nonnull.tail(vol_window).mean()) if vol_window else None
    vol_rel = float(last_vol / vol_avg) if (last_vol is not None and vol_avg and vol_avg != 0) else None

    gap = (open_ - close.shift(1)).abs() / close.shift(1)
    gap_avg = float(gap.dropna().tail(vol_window).mean()) if vol_window and len(gap.dropna()) else None

    # Key levels (rolling highs/lows).
    def level_hi(n: int) -> Optional[float]:
        s = _last_n_series(df, cols.high, n)
        return float(s.max()) if s is not None else None

    def level_lo(n: int) -> Optional[float]:
        s = _last_n_series(df, cols.low, n)
        return float(s.min()) if s is not None else None

    hi_20 = level_hi(20)
    lo_20 = level_lo(20)
    hi_60 = level_hi(60)
    lo_60 = level_lo(60)

    # Drawdown from recent high (context for mean reversion / breakout potential).
    dd_60 = None
    if hi_60 and hi_60 != 0:
        dd_60 = float(last_close / hi_60 - 1.0)

    out = []
    out.append(f"## Price-action snapshot for {symbol.upper()} (daily)")
    out.append(f"- Window: {start_date} → {end_date} ({len(df)} trading days parsed)")
    out.append(f"- Last close: {_num(last_close)} (prev: {_num(prev_close)})")
    out.append(f"- Returns: 5D {_pct(ret_5d)} | 1M(21D) {_pct(ret_21d)} | 2M(42D) {_pct(ret_42d)} | 3M(63D) {_pct(ret_63d)}")
    out.append(f"- Realized vol (ann., ~{vol_window_days}D): {_pct(rv_annual)}")
    out.append(f"- ATR(14): {_num(atr_14)} ({_pct(atr_pct)} of price)")
    out.append(f"- Volume: last {_num(last_vol, 0)} | avg({_min(vol_window_days, len(vol_nonnull))}D) {_num(vol_avg, 0)} | rel {_num(vol_rel, 2)}")
    out.append(f"- Overnight gap (avg abs, ~{vol_window_days}D): {_pct(gap_avg)}")
    out.append(f"- Trend context: close vs EMA10 {_num(float(ema_10))}, SMA50 {_num(float(sma_50))}, SMA200 {_num(sma_200) if sma_200 is not None else 'N/A'}")
    out.append("")
    out.append("| Key level | Price | Notes |")
    out.append("|---|---:|---|")
    out.append(f"| 20D high | {_num(hi_20)} | near-term resistance / breakout trigger |")
    out.append(f"| 20D low | {_num(lo_20)} | near-term support / stop reference |")
    out.append(f"| 60D high | {_num(hi_60)} | medium-term resistance |")
    out.append(f"| 60D low | {_num(lo_60)} | medium-term support |")
    out.append(f"| Drawdown from 60D high | {_pct(dd_60)} | negative = below recent high |")
    return "\n".join(out)


def _min(a: int, b: int) -> int:
    try:
        return int(a) if int(a) < int(b) else int(b)
    except Exception:
        return a
