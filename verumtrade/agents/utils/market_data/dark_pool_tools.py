"""
Dark Pool / Off-Exchange Data Tools for Verumtrade

Provides dark pool and off-exchange trading data using free FINRA sources:
1. FINRA ATS Transparency Data - Weekly volume by dark pool per symbol
2. FINRA Short Volume - Daily short sale volume (includes dark pools)
3. FINRA OTC Transparency - Off-exchange trading data

Data Latency:
- ATS Data: 2-week delay for Tier 1 stocks (S&P 500, Russell 1000)
- Short Volume: Published next trading day (T+1)

For swing trading (1-8 week horizon), this delayed data is still 
valuable for identifying institutional accumulation/distribution patterns.

Requirements:
- requests (pip install requests)
- pandas (pip install pandas)
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Annotated, Optional, Dict, Any, List
import io
import asyncio

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _get_finra_short_volume(symbol: str, date: datetime) -> Optional[Dict[str, Any]]:
    """
    Fetch daily short volume data from FINRA.
    
    FINRA publishes daily short sale volume files at:
    https://cdn.finra.org/equity/regsho/daily/
    """
    import requests
    
    # Try multiple date formats (FINRA uses YYYYMMDD)
    date_str = date.strftime("%Y%m%d")
    
    # FINRA short volume files
    urls = [
        # Consolidated NMS short volume
        f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date_str}.txt",
        # Alternative: by exchange
        f"https://cdn.finra.org/equity/regsho/daily/FNRAshvol{date_str}.txt",
    ]
    
    for url in urls:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                # Parse pipe-delimited file
                lines = response.text.strip().split('\n')
                
                # Find header line
                header_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith("Date|Symbol"):
                        header_idx = i
                        break
                
                # Parse data
                for line in lines[header_idx + 1:]:
                    parts = line.split('|')
                    if len(parts) >= 5 and parts[1].upper() == symbol.upper():
                        return {
                            "date": parts[0],
                            "symbol": parts[1],
                            "short_volume": int(parts[2]) if parts[2] else 0,
                            "short_exempt_volume": int(parts[3]) if parts[3] else 0,
                            "total_volume": int(parts[4]) if parts[4] else 0,
                        }
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            continue
    
    return None


def _get_recent_short_volume(symbol: str, curr_date: datetime, lookback_days: int = 10) -> List[Dict[str, Any]]:
    """Get short volume for multiple recent days."""
    results = []
    
    for i in range(lookback_days):
        check_date = curr_date - timedelta(days=i)
        # Skip weekends
        if check_date.weekday() >= 5:
            continue
        
        data = _get_finra_short_volume(symbol, check_date)
        if data:
            results.append(data)
        
        # Stop after 5 successful data points
        if len(results) >= 5:
            break
    
    return results


@tool
async def get_dark_pool_short_volume(
    symbol: Annotated[str, "Stock ticker symbol (e.g., 'AAPL', 'NVDA')"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get daily short volume data from FINRA (includes dark pool short sales).
    
    FINRA publishes daily short sale volume for all NMS securities, covering
    both lit exchanges and dark pools/ATSs. This is the most timely free
    source for off-exchange trading activity (published T+1).
    
    Returns:
    - Short volume vs total volume ratio
    - Trend in short volume over recent days
    - Comparison to typical short volume levels
    
    Interpretation:
    - High short volume ratio (>50%) may indicate bearish sentiment or
      market makers hedging (context matters)
    - Rising short volume trend suggests increasing bearish positioning
    - Falling short volume may indicate short covering
    
    Note: Short volume ≠ short interest. Short volume is daily flow,
    short interest is outstanding short positions.
    """
    import requests
    
    symbol = symbol.upper().strip()
    
    try:
        target_date = _parse_date(curr_date)
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."
    
    # Get recent short volume data
    recent_data = await asyncio.to_thread(_get_recent_short_volume, symbol, target_date, lookback_days=14)
    
    if not recent_data:
        return f"""## Dark Pool Short Volume: {symbol}

**Status**: No FINRA short volume data found for recent dates.

This could mean:
- Data not yet published (FINRA publishes T+1)
- Weekend/holiday - no trading data
- Symbol not covered by FINRA short volume reporting

Try again tomorrow or check that {symbol} is an NMS security.
"""
    
    # Calculate metrics
    latest = recent_data[0]
    short_ratio = (latest["short_volume"] / latest["total_volume"] * 100) if latest["total_volume"] > 0 else 0
    
    # Historical average
    avg_short_ratio = 0
    if len(recent_data) > 1:
        ratios = [(d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0 
                  for d in recent_data]
        avg_short_ratio = sum(ratios) / len(ratios)
    
    # Trend analysis
    if len(recent_data) >= 3:
        recent_avg = sum(ratios[:2]) / 2
        older_avg = sum(ratios[2:min(5, len(ratios))]) / min(3, len(ratios) - 2)
        if recent_avg > older_avg * 1.1:
            trend = "📈 RISING - Short volume increasing"
        elif recent_avg < older_avg * 0.9:
            trend = "📉 FALLING - Short volume decreasing (potential covering)"
        else:
            trend = "➡️ STABLE - Short volume consistent"
    else:
        trend = "N/A - Insufficient history"
    
    # Interpretation
    if short_ratio > 60:
        interpretation = "⚠️ Very high short volume - significant bearish activity or heavy hedging"
    elif short_ratio > 50:
        interpretation = "🔴 Elevated short volume - above-average bearish activity"
    elif short_ratio > 40:
        interpretation = "⚪ Normal short volume range"
    else:
        interpretation = "🟢 Low short volume - less bearish activity than typical"
    
    # Format daily history
    history_lines = []
    for d in recent_data[:5]:
        ratio = (d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0
        history_lines.append(
            f"  {d['date']}: {d['short_volume']:,} / {d['total_volume']:,} ({ratio:.1f}%)"
        )
    
    return f"""## Dark Pool Short Volume: {symbol} ({curr_date})

### Latest Data ({latest['date']})
- **Short Volume**: {latest['short_volume']:,} shares
- **Total Volume**: {latest['total_volume']:,} shares
- **Short Ratio**: {short_ratio:.1f}%
- **5-Day Avg Short Ratio**: {avg_short_ratio:.1f}%

### Trend
{trend}

### Recent History (Short Vol / Total Vol)
{chr(10).join(history_lines)}

### Interpretation
{interpretation}

### Context
- Short volume includes both lit exchange and dark pool short sales
- High short volume can indicate bearish bets OR market maker hedging
- Compare to short interest (outstanding shorts) for full picture
- Data source: FINRA RegSHO Daily Short Volume
"""


@tool
async def get_off_exchange_volume_context(
    symbol: Annotated[str, "Stock ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get context about off-exchange (dark pool) trading for a symbol.
    
    Provides:
    - Estimated dark pool volume % (typically 40-50% of total volume)
    - Recent short volume trends as a proxy for off-exchange activity
    - General context about dark pool trading patterns
    
    Note: Real-time dark pool prints require paid data feeds.
    This tool provides context using free FINRA data.
    """
    import requests
    
    symbol = symbol.upper().strip()
    
    try:
        target_date = _parse_date(curr_date)
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."
    
    # Define blocking worker
    def fetch_data():
        # Get short volume as our main off-exchange proxy
        r_data = _get_recent_short_volume(symbol, target_date, lookback_days=14)
        
        # Get current stock info for context
        avg_vol = 0
        curr_px = 0
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info
            avg_vol = info.get("averageVolume", 0)
            curr_px = info.get("regularMarketPrice") or info.get("previousClose", 0)
        except Exception:
            pass
            
        return r_data, avg_vol, curr_px

    recent_data, avg_volume, current_price = await asyncio.to_thread(fetch_data)
    
    # Industry stats: ~50% of US equity volume trades off-exchange
    estimated_dark_pool_pct = 50  # Conservative estimate
    
    if not recent_data:
        return f"""## Off-Exchange Trading Context: {symbol}

### General Market Context
- Approximately {estimated_dark_pool_pct}% of US equity volume trades off-exchange (dark pools + internalization)
- Dark pools are used by institutions to minimize market impact on large orders
- High dark pool activity often indicates institutional accumulation or distribution

### Data Availability
No recent FINRA short volume data available for {symbol}.
- Check that the symbol is an NMS security
- Data may not be published yet (FINRA publishes T+1)

### For Detailed Dark Pool Data
Real-time dark pool prints require paid services like:
- Unusual Whales ($49/mo) - includes dark pool alerts
- FlowAlgo ($149/mo) - real-time dark pool scanner
"""
    
    # Calculate metrics from available data
    latest = recent_data[0]
    if avg_volume > 0:
        volume_vs_avg = (latest["total_volume"] / avg_volume) * 100
    else:
        volume_vs_avg = 100
    
    # Estimate dark pool volume (using industry average)
    est_dark_volume = int(latest["total_volume"] * (estimated_dark_pool_pct / 100))
    est_lit_volume = latest["total_volume"] - est_dark_volume
    
    # Notional values
    if current_price > 0:
        notional_total = latest["total_volume"] * current_price
        notional_dark = est_dark_volume * current_price
    else:
        notional_total = 0
        notional_dark = 0
    
    # Short volume trends for institutional activity proxy
    short_ratios = [(d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0 
                    for d in recent_data]
    avg_short = sum(short_ratios) / len(short_ratios) if short_ratios else 0
    
    # Interpretation based on available signals
    signals = []
    
    if volume_vs_avg > 150:
        signals.append("📈 Volume surge - potential institutional activity")
    elif volume_vs_avg < 50:
        signals.append("📉 Low volume - reduced institutional interest")
    
    if avg_short > 55:
        signals.append("🔴 Elevated short activity - bearish institutional flow")
    elif avg_short < 35:
        signals.append("🟢 Low short activity - less bearish pressure")
    
    if not signals:
        signals.append("⚪ Normal trading patterns - no strong institutional signals")
    
    return f"""## Off-Exchange Trading Context: {symbol} ({curr_date})

### Volume Breakdown (Estimated)
- **Total Volume**: {latest['total_volume']:,} shares
- **Est. Dark Pool Volume**: ~{est_dark_volume:,} shares ({estimated_dark_pool_pct}%)
- **Est. Lit Exchange Volume**: ~{est_lit_volume:,} shares ({100-estimated_dark_pool_pct}%)
- **Volume vs 20-Day Avg**: {volume_vs_avg:.0f}%

### Notional Values
- Total: ${notional_total/1_000_000:.1f}M
- Est. Dark Pool: ${notional_dark/1_000_000:.1f}M

### Institutional Activity Signals
{chr(10).join(f'- {s}' for s in signals)}

### Short Volume Context
- Average short ratio: {avg_short:.1f}%
- This proxies bearish off-exchange flow

### Why This Matters for Swing Trading
- High dark pool % = institutions building/exiting positions quietly
- Unusual volume + high short ratio = potential distribution
- Unusual volume + low short ratio = potential accumulation
- Watch for multi-day patterns, not single-day anomalies

### Data Limitations
- Real-time dark pool prints require paid subscriptions
- FINRA ATS transparency data has 2-4 week delay
- Short volume is best available free proxy (T+1)
"""


@tool
async def get_finra_ats_summary(
    symbol: Annotated[str, "Stock ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get FINRA ATS (Alternative Trading System) transparency summary.
    
    FINRA publishes weekly aggregate volume data for each dark pool.
    This data has a 2-4 week delay but shows which ATSs are most
    active in a particular stock.
    
    Note: This requires parsing FINRA's API which may have access
    limitations. Falls back to general context if data unavailable.
    """
    import requests
    
    symbol = symbol.upper().strip()
    
    # FINRA ATS Transparency API endpoint
    ats_url = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
    
    try:
        # Query for the symbol
        payload = {
            "compareFilters": [
                {
                    "fieldName": "issueSymbolIdentifier",
                    "fieldValue": symbol,
                    "compareType": "EQUAL"
                }
            ],
            "limit": 10,
            "sortFields": [{"fieldName": "weekStartDate", "order": "DESC"}]
        }
        
        response = await asyncio.to_thread(
            requests.post,
            ats_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if data and len(data) > 0:
                # Parse and format results
                weeks_data = []
                for record in data[:4]:  # Last 4 weeks
                    weeks_data.append({
                        "week": record.get("weekStartDate", "N/A"),
                        "ats": record.get("atsDisplayName", "Unknown ATS"),
                        "volume": record.get("totalWeeklyShareQuantity", 0),
                        "trades": record.get("totalWeeklyTradeCount", 0),
                    })
                
                # Format output
                if weeks_data:
                    week_lines = [
                        f"  {w['week']}: {w['volume']:,} shares via {w['ats']}"
                        for w in weeks_data
                    ]
                    
                    return f"""## FINRA ATS Transparency: {symbol}

### Recent Dark Pool Activity
{chr(10).join(week_lines)}

### Note
- Data has 2-4 week delay per FINRA rules
- Shows which ATSs (dark pools) handle this stock
- High volume concentration in specific ATSs may indicate institutional interest
"""
    
    except requests.exceptions.RequestException as e:
        logger.debug(f"FINRA ATS API error: {e}")
    except Exception as e:
        logger.debug(f"Error parsing FINRA ATS data: {e}")
    
    # Fallback response
    return f"""## FINRA ATS Transparency: {symbol}

### Data Access
Unable to retrieve real-time FINRA ATS data for {symbol}.

FINRA ATS transparency data is available at: https://ats.finra.org

### What This Data Shows
- Weekly volume breakdown by dark pool (ATS)
- Which venues handle the most volume for this stock
- Trends in dark pool activity over time

### Delay Schedule
- Tier 1 stocks (S&P 500, Russell 1000): 2-week delay
- Other NMS stocks: 4-week delay

### Alternative
Use `get_dark_pool_short_volume` for next-day (T+1) short volume data,
which provides a faster proxy for off-exchange activity.
"""
