from __future__ import annotations

from collections import deque
from datetime import datetime, date, timezone
from dateutil.relativedelta import relativedelta

from .twelve_data_common import _make_api_request

VWMA_WINDOW = 20

INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.",
    "close_200_sma": "200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.",
    "close_10_ema": "10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.",
    "macd": "MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.",
    "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.",
    "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.",
    "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.",
    "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.",
    "atr": "ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.",
    "vwma": "VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.",
    "mfi": "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals.",
}

INDICATOR_SPECS = {
    "close_50_sma": {"endpoint": "sma", "value_key": "sma", "params": {"time_period": 50}},
    "close_200_sma": {"endpoint": "sma", "value_key": "sma", "params": {"time_period": 200}},
    "close_10_ema": {"endpoint": "ema", "value_key": "ema", "params": {"time_period": 10}},
    "macd": {"endpoint": "macd", "value_key": "macd", "params": {}},
    "macds": {"endpoint": "macd", "value_key": "macd_signal", "params": {}},
    "macdh": {"endpoint": "macd", "value_key": "macd_hist", "params": {}},
    "rsi": {"endpoint": "rsi", "value_key": "rsi", "params": {"time_period": 14}},
    "boll": {"endpoint": "bbands", "value_key": "middle_band", "params": {"time_period": 20}},
    "boll_ub": {"endpoint": "bbands", "value_key": "upper_band", "params": {"time_period": 20}},
    "boll_lb": {"endpoint": "bbands", "value_key": "lower_band", "params": {"time_period": 20}},
    "atr": {"endpoint": "atr", "value_key": "atr", "params": {"time_period": 14}},
    "mfi": {"endpoint": "mfi", "value_key": "mfi", "params": {"time_period": 14}},
}


def _parse_date(value: str | int | float | None) -> date | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # Unix epoch support
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc).date()
        except Exception:
            pass

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # Handle ISO-ish values
    try:
        normalized = s.replace("T", " ")
        if normalized.endswith("Z"):
            normalized = normalized[:-1]
        return datetime.fromisoformat(normalized).date()
    except Exception:
        return None


def _format_result(indicator: str, before_dt: datetime, curr_date: str, rows: list[tuple[date, str]]) -> str:
    if not rows:
        ind_string = "No data available for the specified date range.\n"
    else:
        ind_string = "".join(f"{d.strftime('%Y-%m-%d')}: {v}\n" for d, v in rows)

    return (
        f"## {indicator} values from {before_dt.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + ind_string
        + "\n\n"
        + INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )


def _get_vwma_values(symbol: str, curr_date: str, look_back_days: int, interval: str) -> list[tuple[date, str]]:
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = curr_date_dt - relativedelta(days=look_back_days)

    outputsize = max(look_back_days + VWMA_WINDOW + 30, 120)
    payload = _make_api_request(
        "time_series",
        {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "end_date": curr_date,
            "order": "ASC",
        },
    )
    values = payload.get("values", [])
    if not isinstance(values, list):
        return []

    rolling = deque()
    pv_sum = 0.0
    vol_sum = 0.0
    rows: list[tuple[date, str]] = []

    for row in values:
        if not isinstance(row, dict):
            continue
        day = _parse_date(row.get("datetime"))
        if day is None:
            continue

        close_raw = row.get("close")
        volume_raw = row.get("volume")
        vwma_value = "N/A"

        try:
            close = float(close_raw)
            volume = float(volume_raw)
            if volume < 0:
                raise ValueError("Negative volume")
            pv = close * volume
            rolling.append((pv, volume))
            pv_sum += pv
            vol_sum += volume

            if len(rolling) > VWMA_WINDOW:
                old_pv, old_vol = rolling.popleft()
                pv_sum -= old_pv
                vol_sum -= old_vol

            if len(rolling) == VWMA_WINDOW and vol_sum > 0:
                vwma_value = str(pv_sum / vol_sum)
        except Exception:
            vwma_value = "N/A"

        if before_dt.date() <= day <= curr_date_dt.date():
            rows.append((day, vwma_value))

    return rows


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    interval: str = "1day",
) -> str:
    """Returns Twelve Data technical indicator values over a time window."""
    indicator = indicator.strip().lower()
    if indicator == "sma":
        indicator = "close_50_sma"
    elif indicator == "ema":
        indicator = "close_10_ema"

    supported = list(INDICATOR_SPECS.keys()) + ["vwma"]
    if indicator not in supported:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {supported}"
        )

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = curr_date_dt - relativedelta(days=look_back_days)

    if indicator == "vwma":
        rows = _get_vwma_values(symbol, curr_date, look_back_days, interval)
        return _format_result(indicator, before_dt, curr_date, rows)

    spec = INDICATOR_SPECS[indicator]
    outputsize = max(look_back_days + 30, 120)
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "end_date": curr_date,
        "order": "ASC",
    }
    params.update(spec["params"])

    payload = _make_api_request(spec["endpoint"], params)
    values = payload.get("values", [])
    if not isinstance(values, list):
        return _format_result(indicator, before_dt, curr_date, [])

    rows: list[tuple[date, str]] = []
    value_key = spec["value_key"]
    for row in values:
        if not isinstance(row, dict):
            continue
        day = _parse_date(row.get("datetime"))
        if day is None:
            continue
        if not (before_dt.date() <= day <= curr_date_dt.date()):
            continue
        value = row.get(value_key)
        try:
            value_str = str(float(value))
        except Exception:
            value_str = "N/A"
        rows.append((day, value_str))

    return _format_result(indicator, before_dt, curr_date, rows)
