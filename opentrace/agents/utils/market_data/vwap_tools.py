"""
Intraday VWAP Tools for OpenTrace

Provides VWAP positioning analysis using Alpaca's free-tier market data API.
Alpaca includes VWAP in all bar responses at no additional cost.

Requirements:
- APCA_API_KEY_ID and APCA_API_SECRET_KEY environment variables
  (or ALPACA_API_KEY and ALPACA_SECRET_KEY)
"""

import os
import logging
import math
from datetime import datetime, timedelta
from typing import Annotated, Optional, Dict, Any, List
import asyncio

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _get_alpaca_client():
    """Get Alpaca historical data client."""
    try:
        from alpaca.data import StockHistoricalDataClient
    except ImportError:
        raise ImportError(
            "alpaca-py not installed. Run: pip install alpaca-py"
        )
    
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        raise ValueError(
            "Alpaca credentials not found. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "or ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables."
        )
    
    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def _parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _resolve_stock_feed():
    """
    Resolve Alpaca stock data feed from env, defaulting to IEX for free-tier compatibility.

    Supported values: IEX, SIP, OTC (case-insensitive).
    """
    try:
        from alpaca.data.enums import DataFeed
    except Exception:
        return None

    requested = (os.getenv("ALPACA_STOCK_FEED") or "IEX").strip().upper()
    if requested in {"IEX", "SIP", "OTC"} and hasattr(DataFeed, requested):
        return getattr(DataFeed, requested)

    logger.warning(
        "Unsupported ALPACA_STOCK_FEED='%s'; defaulting to IEX.",
        requested,
    )
    return getattr(DataFeed, "IEX", None)


def _is_recent_sip_subscription_error(exc: Exception) -> bool:
    """Detect Alpaca 403 errors when SIP access is not included in the subscription."""
    msg = str(exc).lower()
    return "subscription does not permit querying recent sip data" in msg


def _get_stock_bars_with_feed_fallback(client, request_cls, **request_kwargs):
    """
    Fetch stock bars with configured feed; retry with IEX if SIP access is denied.
    """
    feed = _resolve_stock_feed()
    request = request_cls(feed=feed, **request_kwargs) if feed else request_cls(**request_kwargs)

    try:
        return client.get_stock_bars(request)
    except Exception as e:
        if not _is_recent_sip_subscription_error(e):
            raise

        try:
            from alpaca.data.enums import DataFeed
        except Exception:
            raise

        iex_feed = getattr(DataFeed, "IEX", None)
        if iex_feed is None or feed == iex_feed:
            raise

        logger.warning("SIP bars unavailable for current subscription; retrying with IEX feed.")
        retry_request = request_cls(feed=iex_feed, **request_kwargs)
        return client.get_stock_bars(retry_request)


def _compute_session_vwap(bars: List[Dict[str, Any]]) -> float:
    """
    Compute session VWAP from a list of bars.
    
    VWAP = Î£(Price Ã— Volume) / Î£(Volume)
    where Price = (High + Low + Close) / 3 (typical price)
    """
    if not bars:
        return 0.0
    
    total_pv = 0.0
    total_volume = 0.0
    
    for bar in bars:
        typical_price = (bar["high"] + bar["low"] + bar["close"]) / 3
        volume = bar["volume"]
        total_pv += typical_price * volume
        total_volume += volume
    
    return total_pv / total_volume if total_volume > 0 else 0.0


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Coerce numeric values to finite float; return default for None/NaN/invalid."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce numeric values to int; return default for None/NaN/invalid."""
    f = _safe_float(value, default=None)
    return int(f) if f is not None else default


@tool
async def get_intraday_vwap_position(
    symbol: Annotated[str, "Stock ticker symbol (e.g., 'AAPL', 'NVDA')"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get intraday VWAP positioning analysis for a stock.

    Uses Alpaca's free-tier market data API which includes VWAP in bar responses.

    Returns:
    - Current price vs session VWAP (above/below and % deviation)
    - Time spent above/below VWAP during session
    - Volume analysis (current day vs recent average)
    - VWAP slope/trend during session
    - Trading interpretation for swing entries

    This helps identify:
    - Entry timing (institutions buy below VWAP, sell above)
    - Intraday trend strength
    - Volume confirmation of price moves
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return "Error: alpaca-py not installed. Run: pip install alpaca-py"

    try:
        client = _get_alpaca_client()
    except ValueError as e:
        return f"Error: {e}"

    symbol = symbol.upper().strip()

    try:
        target_date = _parse_date(curr_date)
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."

    try:
        # Get 5-minute bars for the target day
        # Alpaca free tier provides IEX data with VWAP included
        bars_response = await asyncio.to_thread(
            _get_stock_bars_with_feed_fallback,
            client=client,
            request_cls=StockBarsRequest,
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=target_date.replace(hour=4, minute=0),  # Pre-market start
            end=target_date.replace(hour=20, minute=0),   # After-hours end
        )

        if symbol not in bars_response or not bars_response[symbol]:
            return f"No intraday data available for {symbol} on {curr_date}. Market may be closed or data not yet available."

        bars = bars_response[symbol]

        # Convert to list of dicts for processing
        bar_list = []
        for bar in bars:
            open_px = _safe_float(getattr(bar, "open", None))
            high_px = _safe_float(getattr(bar, "high", None))
            low_px = _safe_float(getattr(bar, "low", None))
            close_px = _safe_float(getattr(bar, "close", None))
            if None in (open_px, high_px, low_px, close_px):
                continue

            bar_list.append({
                "timestamp": bar.timestamp,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": _safe_int(getattr(bar, "volume", None), default=0),
                "vwap": _safe_float(getattr(bar, "vwap", None), default=None),
            })

        if not bar_list:
            return f"No bar data for {symbol} on {curr_date}."

        # Current values (last bar)
        current_bar = bar_list[-1]
        current_price = current_bar["close"]

        # Session VWAP - use Alpaca's VWAP if available, otherwise compute
        if current_bar["vwap"]:
            session_vwap = current_bar["vwap"]
        else:
            session_vwap = _compute_session_vwap(bar_list)

        # VWAP deviation
        if session_vwap > 0:
            vwap_deviation = ((current_price - session_vwap) / session_vwap) * 100
        else:
            vwap_deviation = 0.0

        # Position relative to VWAP
        if vwap_deviation > 0.5:
            position = "ABOVE"
            position_emoji = "🟢"
        elif vwap_deviation < -0.5:
            position = "BELOW"
            position_emoji = "🔴"
        else:
            position = "AT"
            position_emoji = "⚪"

        # Time above/below VWAP analysis
        bars_above = 0
        bars_below = 0
        max_above = 0.0
        max_below = 0.0

        for bar in bar_list:
            bar_vwap = bar["vwap"] if bar["vwap"] else session_vwap
            if bar_vwap > 0:
                bar_dev = ((bar["close"] - bar_vwap) / bar_vwap) * 100
                if bar_dev > 0:
                    bars_above += 1
                    max_above = max(max_above, bar_dev)
                else:
                    bars_below += 1
                    max_below = max(max_below, abs(bar_dev))

        total_bars = bars_above + bars_below
        pct_above = (bars_above / total_bars * 100) if total_bars > 0 else 50

        # Session volume
        session_volume = sum(bar["volume"] for bar in bar_list)

        # VWAP trend (compare first half vs second half of session)
        mid_idx = len(bar_list) // 2
        if mid_idx > 0 and bar_list[0]["vwap"] and bar_list[-1]["vwap"]:
            first_vwap = bar_list[0]["vwap"]
            last_vwap = bar_list[-1]["vwap"]
            vwap_change = ((last_vwap - first_vwap) / first_vwap) * 100 if first_vwap > 0 else 0
            if vwap_change > 0.1:
                vwap_trend = f"Rising (+{vwap_change:.2f}%)"
            elif vwap_change < -0.1:
                vwap_trend = f"Falling ({vwap_change:.2f}%)"
            else:
                vwap_trend = "Flat"
        else:
            vwap_trend = "N/A"

        # Price range context
        session_high = max(bar["high"] for bar in bar_list)
        session_low = min(bar["low"] for bar in bar_list)
        session_range = session_high - session_low
        range_pct = (session_range / session_low * 100) if session_low > 0 else 0

        # Where is current price in the day's range?
        if session_range > 0:
            range_position = ((current_price - session_low) / session_range) * 100
        else:
            range_position = 50

        # Interpretation for swing trading
        interpretations = []

        if position == "BELOW" and pct_above < 40:
            interpretations.append("Price persistently below VWAP - distribution pattern, institutions likely selling")
        elif position == "ABOVE" and pct_above > 60:
            interpretations.append("Price persistently above VWAP - accumulation pattern, institutions likely buying")

        if position == "BELOW" and vwap_deviation < -1.5:
            interpretations.append("Significantly extended below VWAP - potential mean reversion bounce candidate")
        elif position == "ABOVE" and vwap_deviation > 1.5:
            interpretations.append("Significantly extended above VWAP - may face resistance, wait for pullback")

        if range_position < 25:
            interpretations.append("Trading near session lows - weakness, but potential support test")
        elif range_position > 75:
            interpretations.append("Trading near session highs - strength, but may face resistance")

        if not interpretations:
            interpretations.append("Neutral positioning - no strong VWAP signal")

        interpretation_text = "\n".join(f"• {i}" for i in interpretations)

        return f"""## Intraday VWAP Analysis: {symbol} ({curr_date})

### Current Position
- **Price**: ${current_price:.2f}
- **Session VWAP**: ${session_vwap:.2f}
- **Position**: {position_emoji} {position} VWAP ({vwap_deviation:+.2f}%)

### Session Dynamics
- Time above VWAP: {pct_above:.0f}% of session
- Max extension above: +{max_above:.2f}%
- Max extension below: -{max_below:.2f}%
- VWAP trend: {vwap_trend}

### Price Context
- Session range: ${session_low:.2f} - ${session_high:.2f} ({range_pct:.1f}%)
- Current in range: {range_position:.0f}% (0%=low, 100%=high)
- Session volume: {session_volume:,} shares

### Trading Interpretation
{interpretation_text}

### Entry Guidance
- **For longs**: Prefer entries below VWAP (current: {'✅ favorable' if position == 'BELOW' else '⚠️ extended'})
- **For shorts**: Prefer entries above VWAP (current: {'✅ favorable' if position == 'ABOVE' else '⚠️ extended'})
"""

    except Exception as e:
        logger.exception(f"Error fetching VWAP data for {symbol}")
        return f"Error fetching VWAP data for {symbol}: {str(e)}"


@tool
async def get_multi_day_vwap_context(
    symbol: Annotated[str, "Stock ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    lookback_days: Annotated[int, "Number of days to look back"] = 5,
) -> str:
    """
    Get multi-day VWAP context for swing trading decisions.
    
    Analyzes VWAP patterns over multiple days to identify:
    - Trend consistency (price consistently above/below VWAP)
    - Volume trends
    - VWAP anchored levels (approximate weekly/monthly VWAP)
    
    Useful for:
    - Confirming trend direction before entry
    - Identifying VWAP-based support/resistance zones
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return "Error: alpaca-py not installed. Run: pip install alpaca-py"
    
    try:
        client = _get_alpaca_client()
    except ValueError as e:
        return f"Error: {e}"
    
    symbol = symbol.upper().strip()
    
    try:
        end_date = _parse_date(curr_date)
        start_date = end_date - timedelta(days=lookback_days + 5)  # Extra days for market closures
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."
    
    try:
        # Get daily bars with VWAP
        bars_response = await asyncio.to_thread(
            _get_stock_bars_with_feed_fallback,
            client=client,
            request_cls=StockBarsRequest,
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start_date,
            end=end_date + timedelta(days=1),
        )
        
        if symbol not in bars_response or not bars_response[symbol]:
            return f"No daily data available for {symbol}."
        
        bars = list(bars_response[symbol])[-lookback_days:]  # Last N trading days
        
        if len(bars) < 2:
            return f"Insufficient data for {symbol}. Need at least 2 trading days."
        
        # Analyze each day
        daily_analysis = []
        days_above_vwap = 0
        total_volume = 0
        
        for bar in bars:
            close = _safe_float(getattr(bar, "close", None))
            if close is None:
                continue

            vwap = _safe_float(getattr(bar, "vwap", None), default=close)
            volume = _safe_int(getattr(bar, "volume", None), default=0)
            
            if vwap > 0:
                deviation = ((close - vwap) / vwap) * 100
            else:
                deviation = 0
            
            position = "above" if deviation > 0.1 else ("below" if deviation < -0.1 else "at")
            if position == "above":
                days_above_vwap += 1
            
            total_volume += volume
            
            daily_analysis.append({
                "date": bar.timestamp.strftime("%Y-%m-%d"),
                "close": close,
                "vwap": vwap,
                "deviation": deviation,
                "position": position,
                "volume": volume,
            })

        if len(daily_analysis) < 2:
            return (
                f"Insufficient valid daily bars for {symbol} after filtering missing/NaN values."
            )
        
        # Summary stats
        avg_volume = total_volume / len(daily_analysis)
        pct_days_above = (days_above_vwap / len(daily_analysis)) * 100
        
        # Multi-day VWAP approximation (volume-weighted average of daily VWAPs)
        total_vwap_volume = sum(d["vwap"] * d["volume"] for d in daily_analysis)
        multi_day_vwap = total_vwap_volume / total_volume if total_volume > 0 else 0
        
        # Current positioning vs multi-day VWAP
        current_close = daily_analysis[-1]["close"]
        multi_day_deviation = ((current_close - multi_day_vwap) / multi_day_vwap * 100) if multi_day_vwap > 0 else 0
        
        # Trend assessment
        if pct_days_above >= 80:
            trend = "🟢 Strong Bullish - Price consistently above VWAP"
        elif pct_days_above >= 60:
            trend = "🟢 Bullish - Price mostly above VWAP"
        elif pct_days_above <= 20:
            trend = "🔴 Strong Bearish - Price consistently below VWAP"
        elif pct_days_above <= 40:
            trend = "🔴 Bearish - Price mostly below VWAP"
        else:
            trend = "⚪ Neutral - Mixed VWAP positioning"
        
        # Format daily details
        daily_details = "\n".join([
            f"  {d['date']}: ${d['close']:.2f} ({d['position']} VWAP by {d['deviation']:+.1f}%)"
            for d in daily_analysis
        ])
        
        return f"""## Multi-Day VWAP Context: {symbol}

### Summary ({len(daily_analysis)} trading days)
- Days closing above VWAP: {days_above_vwap}/{len(daily_analysis)} ({pct_days_above:.0f}%)
- {lookback_days}-Day Volume-Weighted VWAP: ${multi_day_vwap:.2f}
- Current price vs multi-day VWAP: {multi_day_deviation:+.2f}%
- Trend Assessment: {trend}

### Daily Breakdown
{daily_details}

### Swing Trading Implications
- **Trend confirmation**: {'✅ Bullish bias confirmed' if pct_days_above >= 60 else '✅ Bearish bias confirmed' if pct_days_above <= 40 else '⚠️ No clear trend'}
- **Multi-day VWAP as support/resistance**: ${multi_day_vwap:.2f}
- **Volume trend**: Avg {avg_volume:,.0f} shares/day
"""

    except Exception as e:
        logger.exception(f"Error fetching multi-day VWAP for {symbol}")
        return f"Error: {str(e)}"
