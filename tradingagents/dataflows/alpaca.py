from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional


class AlpacaConnectionError(RuntimeError):
    """Raised when Alpaca cannot be used (missing deps/creds/connectivity)."""


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key: str
    secret_key: str
    data_url: Optional[str] = None


def _get_alpaca_credentials() -> AlpacaCredentials:
    """
    Resolve Alpaca credentials from environment variables.

    Supports common Alpaca env var names:
    - APCA_API_KEY_ID / APCA_API_SECRET_KEY (official)
    - ALPACA_API_KEY / ALPACA_API_SECRET (fallback)
    - APCA_API_DATA_URL / ALPACA_DATA_URL (optional override)
    """
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")

    if not api_key or not secret_key:
        raise AlpacaConnectionError(
            "Missing Alpaca credentials. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "(or ALPACA_API_KEY and ALPACA_API_SECRET)."
        )

    data_url = os.getenv("APCA_API_DATA_URL") or os.getenv("ALPACA_DATA_URL")
    return AlpacaCredentials(api_key=api_key, secret_key=secret_key, data_url=data_url)


def _alpaca_client():
    """
    Create a StockHistoricalDataClient.

    We import alpaca-py lazily so the repo still works without Alpaca installed,
    allowing interface-level fallback to yfinance/others.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
    except Exception as e:  # pragma: no cover
        raise AlpacaConnectionError(
            "Alpaca provider requires the 'alpaca-py' package. Install it to enable Alpaca market data."
        ) from e

    creds = _get_alpaca_credentials()

    try:
        # alpaca-py supports url_override for non-default data endpoints.
        if creds.data_url:
            return StockHistoricalDataClient(
                api_key=creds.api_key,
                secret_key=creds.secret_key,
                url_override=creds.data_url,
            )
        return StockHistoricalDataClient(api_key=creds.api_key, secret_key=creds.secret_key)
    except Exception as e:
        raise AlpacaConnectionError(f"Failed to initialize Alpaca client: {e}") from e


def _fetch_alpaca_daily_bars_df(symbol: str, start_date: str, end_date: str):
    """
    Fetch daily bars from Alpaca and return a pandas DataFrame with Yahoo-like columns.
    """
    try:
        import pandas as pd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise AlpacaConnectionError("pandas is required to process Alpaca bar data.") from e

    try:
        from alpaca.data.requests import StockBarsRequest  # type: ignore
        from alpaca.data.timeframe import TimeFrame  # type: ignore
    except Exception as e:  # pragma: no cover
        raise AlpacaConnectionError(
            "Alpaca provider requires the 'alpaca-py' package. Install it to enable Alpaca market data."
        ) from e

    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Alpaca's end is effectively exclusive; add a day so we include the end_date bar.
    end_dt = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=timezone.utc)

    client = _alpaca_client()

    try:
        req = StockBarsRequest(
            symbol_or_symbols=[symbol.upper()],
            timeframe=TimeFrame.Day,
            start=start_dt,
            end=end_dt,
            adjustment=None,
        )
        bars = client.get_stock_bars(req)
    except Exception as e:
        raise AlpacaConnectionError(f"Alpaca request failed: {e}") from e

    try:
        df = bars.df
    except Exception as e:
        raise AlpacaConnectionError(f"Alpaca returned an unexpected bars payload: {e}") from e

    if df is None or len(df) == 0:
        return None

    # alpaca-py commonly returns a MultiIndex (symbol, timestamp). Normalize it.
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
        # columns typically: symbol, timestamp, open, high, low, close, volume, trade_count, vwap
        timestamp_col = "timestamp" if "timestamp" in df.columns else None
        if timestamp_col is None:
            # Best effort: take the last index level name if present.
            timestamp_col = df.columns[1] if len(df.columns) > 1 else "timestamp"
        df["Date"] = pd.to_datetime(df[timestamp_col], utc=True).dt.tz_convert(None)
    else:
        df = df.reset_index()
        ts_col = df.columns[0]
        df["Date"] = pd.to_datetime(df[ts_col], utc=True).dt.tz_convert(None)

    # Rename to match existing downstream expectations (Yahoo-style capitalization).
    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "trade_count": "TradeCount",
        "vwap": "VWAP",
        "symbol": "Symbol",
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Keep a stable, readable column order while preserving extra fields Alpaca may add later.
    preferred = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "TradeCount", "VWAP"]
    remaining = [c for c in df.columns if c not in preferred]
    df = df[preferred + remaining]

    df = df.sort_values("Date")
    df = df.set_index("Date")
    df.index.name = "Date"

    # Round price-like columns for readability.
    for col in ("Open", "High", "Low", "Close", "VWAP"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df


def fetch_stock_bars_df_alpaca(symbol: str, start_date: str, end_date: str):
    """Public helper for getting daily bar data as a DataFrame (used by indicator utilities)."""
    return _fetch_alpaca_daily_bars_df(symbol=symbol, start_date=start_date, end_date=end_date)


def get_stock_data_alpaca(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve daily OHLCV bars from Alpaca.

    Returns a CSV string similar to the yfinance output, but may include additional
    columns provided by Alpaca (e.g., VWAP, TradeCount).
    """
    try:
        df = _fetch_alpaca_daily_bars_df(symbol, start_date, end_date)
    except AlpacaConnectionError:
        raise
    except Exception as e:
        raise AlpacaConnectionError(str(e)) from e

    if df is None or df.empty:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    header += f"# Vendor: alpaca\n"
    header += f"# Total records: {len(df)}\n"
    header += f"# Data retrieved on: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"

    return header + df.to_csv()
