"""
Options Flow Scanner for OpenTrace

Identifies unusual options activity using Yahoo Finance free data.
Scans options chains for high volume/open interest ratios which often
indicate informed trading activity.

Note: This is end-of-day data, not real-time flow. For swing trading
horizons (1-8 weeks), EOD options analysis is still valuable for
identifying building positions.

Requirements:
- yfinance (pip install yfinance)
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Annotated, List, Dict, Any, Optional
from dataclasses import dataclass
import asyncio

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert value to finite float; fallback for None/NaN/invalid."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert value to int with NaN-safe fallback."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return int(f) if math.isfinite(f) else default


@dataclass
class UnusualContract:
    """Represents an options contract with unusual activity."""
    symbol: str
    strike: float
    expiry: str
    contract_type: str  # 'call' or 'put'
    volume: int
    open_interest: int
    vol_oi_ratio: float
    implied_volatility: float
    last_price: float
    bid: float
    ask: float
    in_the_money: bool
    days_to_expiry: int
    notional_value: float  # volume * last_price * 100


def _get_options_chain(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch options chain data from Yahoo Finance."""
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")
    
    ticker = yf.Ticker(symbol)
    
    try:
        expirations = ticker.options
        if not expirations:
            return None
        
        return {
            "ticker": ticker,
            "expirations": expirations,
            "info": ticker.info,
        }
    except Exception as e:
        logger.warning(f"Failed to get options chain for {symbol}: {e}")
        return None


def _analyze_chain_for_unusual(
    ticker,
    expiry: str,
    current_price: float,
    vol_oi_threshold: float = 2.0,
    min_volume: int = 100,
    min_oi: int = 10,
) -> List[UnusualContract]:
    """Analyze a single expiration for unusual activity."""
    unusual = []
    
    try:
        chain = ticker.option_chain(expiry)
    except Exception as e:
        logger.warning(f"Failed to get chain for {expiry}: {e}")
        return unusual
    
    expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
    days_to_expiry = (expiry_date - datetime.now()).days
    
    for opt_type, data in [("call", chain.calls), ("put", chain.puts)]:
        if data.empty:
            continue
        
        for _, row in data.iterrows():
            volume = _safe_int(row.get("volume", 0), default=0)
            oi = _safe_int(row.get("openInterest", 0), default=0)
            
            # Skip low activity contracts
            if volume < min_volume or oi < min_oi:
                continue
            
            vol_oi_ratio = volume / oi if oi > 0 else float('inf')
            
            # Flag unusual activity
            if vol_oi_ratio >= vol_oi_threshold:
                strike = _safe_float(row.get("strike", 0), default=0.0)
                last_price = _safe_float(row.get("lastPrice", 0), default=0.0)
                
                unusual.append(UnusualContract(
                    symbol=ticker.ticker,
                    strike=strike,
                    expiry=expiry,
                    contract_type=opt_type,
                    volume=volume,
                    open_interest=oi,
                    vol_oi_ratio=vol_oi_ratio,
                    implied_volatility=_safe_float(row.get("impliedVolatility", 0), default=0.0),
                    last_price=last_price,
                    bid=_safe_float(row.get("bid", 0), default=0.0),
                    ask=_safe_float(row.get("ask", 0), default=0.0),
                    in_the_money=bool(row.get("inTheMoney", False)),
                    days_to_expiry=days_to_expiry,
                    notional_value=volume * last_price * 100,
                ))
    
    return unusual


@tool
async def get_unusual_options_activity(
    symbol: Annotated[str, "Stock ticker symbol (e.g., 'AAPL', 'NVDA')"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    max_expirations: Annotated[int, "Max number of expiration dates to scan"] = 6,
    vol_oi_threshold: Annotated[float, "Minimum volume/OI ratio to flag as unusual"] = 2.0,
) -> str:
    """
    Scan options chain for unusual activity using free Yahoo Finance data.
    
    Identifies contracts where today's volume significantly exceeds open interest,
    which often indicates new position building by informed traders.
    
    Returns:
    - Contracts with high volume/OI ratios
    - Breakdown by calls vs puts (sentiment indicator)
    - Premium concentration by strike/expiry
    - Overall bullish/bearish flow assessment
    
    Limitations:
    - End-of-day data only (not real-time flow)
    - Cannot distinguish sweeps/blocks from regular trades
    - Best used for identifying building positions, not intraday signals
    
    For swing trading, this is useful for:
    - Confirming directional bias before entry
    - Identifying strikes where large players are positioned
    - Spotting potential gamma squeeze setups
    """
    try:
        import yfinance as yf
    except ImportError:
        return "Error: yfinance not installed. Run: pip install yfinance"
    
    symbol = symbol.upper().strip()
    
    chain_data = await asyncio.to_thread(_get_options_chain, symbol)
    if not chain_data:
        return f"No options data available for {symbol}. The stock may not have listed options."
    
    ticker = chain_data["ticker"]
    expirations = chain_data["expirations"][:max_expirations]
    info = chain_data["info"]
    
    current_price = _safe_float(
        info.get("regularMarketPrice", info.get("previousClose", 0)),
        default=0.0,
    )
    if not current_price:
        return f"Could not determine current price for {symbol}."
    
    # Scan all expirations for unusual activity
    all_unusual: List[UnusualContract] = []
    
    async def analyze_expiry(expiry):
        return await asyncio.to_thread(_analyze_chain_for_unusual,
            ticker=ticker,
            expiry=expiry,
            current_price=current_price,
            vol_oi_threshold=vol_oi_threshold,
        )

    results = await asyncio.gather(*(analyze_expiry(exp) for exp in expirations))
    for res in results:
        all_unusual.extend(res)
    
    if not all_unusual:
        return f"""## Options Activity Scan: {symbol} ({curr_date})

**Current Price**: ${current_price:.2f}
**Expirations Scanned**: {len(expirations)}

### Result
No unusual options activity detected (vol/OI ratio < {vol_oi_threshold}x).

This could mean:
- Normal trading activity, no significant new positioning
- Low options volume day
- Informed traders may be waiting for a catalyst
"""
    
    # Sort by notional value (biggest trades first)
    all_unusual.sort(key=lambda x: x.notional_value, reverse=True)
    
    # Aggregate statistics
    total_call_volume = sum(c.volume for c in all_unusual if c.contract_type == "call")
    total_put_volume = sum(c.volume for c in all_unusual if c.contract_type == "put")
    total_call_notional = sum(c.notional_value for c in all_unusual if c.contract_type == "call")
    total_put_notional = sum(c.notional_value for c in all_unusual if c.contract_type == "put")
    
    call_count = len([c for c in all_unusual if c.contract_type == "call"])
    put_count = len([c for c in all_unusual if c.contract_type == "put"])
    
    # Sentiment assessment
    if total_call_notional > total_put_notional * 2:
        sentiment = "🟢 STRONGLY BULLISH"
        sentiment_detail = "Call premium significantly exceeds put premium"
    elif total_call_notional > total_put_notional * 1.3:
        sentiment = "🟢 BULLISH"
        sentiment_detail = "Call premium exceeds put premium"
    elif total_put_notional > total_call_notional * 2:
        sentiment = "🔴 STRONGLY BEARISH"
        sentiment_detail = "Put premium significantly exceeds call premium"
    elif total_put_notional > total_call_notional * 1.3:
        sentiment = "🔴 BEARISH"
        sentiment_detail = "Put premium exceeds call premium"
    else:
        sentiment = "⚪ NEUTRAL"
        sentiment_detail = "Balanced call/put activity"
    
    # Format top unusual contracts (limit to 10)
    top_contracts = all_unusual[:10]
    contract_lines = []
    for c in top_contracts:
        itm_flag = "ITM" if c.in_the_money else "OTM"
        contract_lines.append(
            f"  {c.contract_type.upper()} ${c.strike:.0f} {c.expiry} ({c.days_to_expiry}d) | "
            f"Vol: {c.volume:,} | OI: {c.open_interest:,} | "
            f"Ratio: {c.vol_oi_ratio:.1f}x | "
            f"${c.notional_value/1000:.0f}K | {itm_flag}"
        )
    
    # Expiry concentration
    expiry_volume: Dict[str, int] = {}
    for c in all_unusual:
        expiry_volume[c.expiry] = expiry_volume.get(c.expiry, 0) + c.volume
    
    top_expiries = sorted(expiry_volume.items(), key=lambda x: x[1], reverse=True)[:3]
    expiry_lines = [f"  {exp}: {vol:,} contracts" for exp, vol in top_expiries]
    
    # Strike concentration (find clusters)
    strikes_by_volume: Dict[float, int] = {}
    for c in all_unusual:
        strikes_by_volume[c.strike] = strikes_by_volume.get(c.strike, 0) + c.volume
    
    top_strikes = sorted(strikes_by_volume.items(), key=lambda x: x[1], reverse=True)[:5]
    strike_lines = [f"  ${strike:.0f}: {vol:,} contracts" for strike, vol in top_strikes]
    
    return f"""## Unusual Options Activity: {symbol} ({curr_date})

### Current Context
- **Stock Price**: ${current_price:.2f}
- **Expirations Scanned**: {len(expirations)}
- **Unusual Contracts Found**: {len(all_unusual)}

### Flow Summary
- **Sentiment**: {sentiment}
- {sentiment_detail}

| Metric | Calls | Puts |
|--------|-------|------|
| Unusual Contracts | {call_count} | {put_count} |
| Volume | {total_call_volume:,} | {total_put_volume:,} |
| Notional Premium | ${total_call_notional/1000:,.0f}K | ${total_put_notional/1000:,.0f}K |

### Top Unusual Contracts (by notional value)
{chr(10).join(contract_lines)}

### Volume Concentration by Expiry
{chr(10).join(expiry_lines)}

### Volume Concentration by Strike
{chr(10).join(strike_lines)}

### Trading Interpretation
- **Primary signal**: {sentiment} bias based on unusual options positioning
- **Key levels**: Watch strikes with heavy volume ({', '.join(f'${s[0]:.0f}' for s in top_strikes[:3])})
- **Timeframe**: Activity concentrated in {top_expiries[0][0] if top_expiries else 'N/A'} expiry

### Caveats
- This is EOD data, not real-time flow
- Cannot distinguish opening vs closing trades
- High vol/OI may indicate hedging, not directional bets
"""


@tool
async def get_options_sentiment_summary(
    symbol: Annotated[str, "Stock ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get a quick put/call ratio and options sentiment summary.
    
    Uses aggregate options data to provide:
    - Put/Call volume ratio
    - Put/Call open interest ratio
    - Implied volatility context
    - Overall sentiment read
    
    Faster than full unusual activity scan when you just need
    a directional sentiment read.
    """
    try:
        import yfinance as yf
    except ImportError:
        return "Error: yfinance not installed. Run: pip install yfinance"
    
    symbol = symbol.upper().strip()
    # Define blocking worker for yfinance calls
    def fetch_sentiment_data():
        ticker = yf.Ticker(symbol)
        try:
            expirations = ticker.options
            if not expirations:
                return "No options data available"
        except Exception as e:
            return f"Error fetching options: {e}"
        
        info = ticker.info
        current_price = info.get("regularMarketPrice") or info.get("previousClose", 0)
        
        tc_vol, tp_vol, tc_oi, tp_oi = 0.0, 0.0, 0.0, 0.0
        ivs = []
        
        for expiry in expirations[:4]:
            try:
                chain = ticker.option_chain(expiry)
                if not chain.calls.empty:
                    tc_vol += _safe_float(chain.calls["volume"].sum())
                    tc_oi += _safe_float(chain.calls["openInterest"].sum())
                    atm_calls = chain.calls[abs(chain.calls["strike"] - current_price) < current_price * 0.05]
                    if not atm_calls.empty:
                        ivs.extend([v for v in (_safe_float(x, float("nan")) for x in atm_calls["impliedVolatility"]) if math.isfinite(v)])
                if not chain.puts.empty:
                    tp_vol += _safe_float(chain.puts["volume"].sum())
                    tp_oi += _safe_float(chain.puts["openInterest"].sum())
            except Exception:
                continue
                
        return {
            "tc_vol": tc_vol, "tp_vol": tp_vol, "tc_oi": tc_oi, "tp_oi": tp_oi, "ivs": ivs
        }

    data = await asyncio.to_thread(fetch_sentiment_data)
    if isinstance(data, str):
        return data

    total_call_volume = data["tc_vol"]
    total_put_volume = data["tp_vol"]
    total_call_oi = data["tc_oi"]
    total_put_oi = data["tp_oi"]
    iv_samples = data["ivs"]

    # Calculate ratios
    pc_volume_ratio = total_put_volume / total_call_volume if total_call_volume > 0 else 0
    pc_oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 0
    avg_iv = sum(iv_samples) / len(iv_samples) * 100 if iv_samples else 0
    
    # Interpret P/C ratio
    # Contrarian: High P/C often bullish (fear = buying opportunity)
    # Confirmation: Very low P/C may indicate complacency
    if pc_volume_ratio > 1.5:
        sentiment = "🟡 ELEVATED PUT ACTIVITY"
        interpretation = "High put volume may indicate hedging or fear - contrarian bullish signal"
    elif pc_volume_ratio > 1.0:
        sentiment = "⚪ SLIGHTLY BEARISH"
        interpretation = "Puts outpacing calls, mild caution in the market"
    elif pc_volume_ratio < 0.5:
        sentiment = "🟡 ELEVATED CALL ACTIVITY"
        interpretation = "Very high call volume may indicate complacency - watch for pullback"
    elif pc_volume_ratio < 0.7:
        sentiment = "🟢 BULLISH"
        interpretation = "Calls outpacing puts, market expects upside"
    else:
        sentiment = "⚪ NEUTRAL"
        interpretation = "Balanced put/call activity"
    
    return f"""## Options Sentiment: {symbol} ({curr_date})

### Ratios
- **Put/Call Volume Ratio**: {pc_volume_ratio:.2f}
- **Put/Call OI Ratio**: {pc_oi_ratio:.2f}
- **Avg ATM IV**: {avg_iv:.1f}%

### Aggregate Volume
- Total Call Volume: {_safe_int(total_call_volume):,}
- Total Put Volume: {_safe_int(total_put_volume):,}
- Total Call OI: {_safe_int(total_call_oi):,}
- Total Put OI: {_safe_int(total_put_oi):,}

### Sentiment Read
**{sentiment}**
{interpretation}

### Quick Guide
- P/C Ratio > 1.0: More puts than calls (bearish activity or hedging)
- P/C Ratio < 0.7: More calls than puts (bullish activity)
- Extreme readings (>1.5 or <0.5) can be contrarian indicators
"""
