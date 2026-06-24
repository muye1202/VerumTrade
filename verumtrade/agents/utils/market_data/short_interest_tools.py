"""
Short Interest Tools for Verumtrade

Provides short interest and short squeeze analysis using free data sources:
1. Yahoo Finance - Short % of float, shares short, days to cover
2. FINRA Short Volume - Daily short sale volume (T+1)
3. FINRA Short Interest - Twice-monthly short interest (2-week delay)

These tools help identify:
- Crowded shorts vulnerable to squeezes
- Short covering rallies
- Bearish positioning trends

Requirements:
- yfinance (pip install yfinance)
- requests (pip install requests)
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Annotated, Optional, Dict, Any, List
import asyncio

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _get_yahoo_short_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Get short interest data from Yahoo Finance."""
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")
    
    ticker = yf.Ticker(symbol)
    info = ticker.info
    
    if not info:
        return None
    
    # Extract short interest fields
    shares_short = info.get("sharesShort", 0) or 0
    shares_short_prior = info.get("sharesShortPriorMonth", 0) or 0
    short_ratio = info.get("shortRatio", 0) or 0  # Days to cover
    short_pct_float = info.get("shortPercentOfFloat", 0) or 0
    shares_outstanding = info.get("sharesOutstanding", 0) or 0
    float_shares = info.get("floatShares", 0) or 0
    avg_volume = info.get("averageVolume", 0) or 0
    
    # Calculate short % of outstanding if not provided
    if short_pct_float == 0 and shares_short > 0 and float_shares > 0:
        short_pct_float = shares_short / float_shares
    
    # Short interest change
    if shares_short_prior > 0:
        short_change_pct = ((shares_short - shares_short_prior) / shares_short_prior) * 100
    else:
        short_change_pct = 0
    
    return {
        "shares_short": shares_short,
        "shares_short_prior": shares_short_prior,
        "short_change_pct": short_change_pct,
        "short_ratio": short_ratio,
        "short_pct_float": short_pct_float * 100,  # Convert to percentage
        "shares_outstanding": shares_outstanding,
        "float_shares": float_shares,
        "avg_volume": avg_volume,
    }


def _get_finra_short_volume(symbol: str, date: datetime) -> Optional[Dict[str, Any]]:
    """Fetch daily short volume from FINRA."""
    import requests
    
    date_str = date.strftime("%Y%m%d")
    url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date_str}.txt"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            
            for line in lines:
                if line.startswith("Date|"):
                    continue
                parts = line.split('|')
                if len(parts) >= 5 and parts[1].upper() == symbol.upper():
                    return {
                        "date": parts[0],
                        "short_volume": int(parts[2]) if parts[2] else 0,
                        "total_volume": int(parts[4]) if parts[4] else 0,
                    }
    except Exception as e:
        logger.debug(f"FINRA short volume error: {e}")
    
    return None


def _get_recent_short_volume(symbol: str, curr_date: datetime, days: int = 10) -> List[Dict[str, Any]]:
    """Get multiple days of short volume data."""
    results = []
    
    for i in range(days):
        check_date = curr_date - timedelta(days=i)
        if check_date.weekday() >= 5:  # Skip weekends
            continue
        
        data = _get_finra_short_volume(symbol, check_date)
        if data:
            results.append(data)
        
        if len(results) >= 5:
            break
    
    return results


@tool
async def get_short_interest_data(
    symbol: Annotated[str, "Stock ticker symbol (e.g., 'AAPL', 'GME')"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Get comprehensive short interest data for a stock.
    
    Combines data from:
    - Yahoo Finance: Short % of float, shares short, days to cover
    - FINRA: Daily short volume (T+1)
    
    Returns:
    - Short interest as % of float
    - Days to cover (short ratio)
    - Month-over-month change in short interest
    - Recent daily short volume trends
    - Squeeze potential assessment
    
    Key thresholds:
    - >20% short float: High squeeze potential
    - >10% short float: Elevated short interest
    - >4 days to cover: Significant covering pressure possible
    
    This helps identify:
    - Crowded shorts vulnerable to covering rallies
    - Bearish sentiment levels
    - Potential short squeeze setups
    """
    symbol = symbol.upper().strip()
    
    try:
        target_date = _parse_date(curr_date)
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."
    
    # Fetch data concurrently
    async def fetch_yahoo():
        try:
            return await asyncio.to_thread(_get_yahoo_short_data, symbol)
        except Exception as e:
            logger.warning(f"Yahoo Finance error for {symbol}: {e}")
            return None

    async def fetch_finra():
        return await asyncio.to_thread(_get_recent_short_volume, symbol, target_date)

    yahoo_data, short_volume_data = await asyncio.gather(fetch_yahoo(), fetch_finra())
    
    if not yahoo_data and not short_volume_data:
        return f"""## Short Interest Analysis: {symbol}

**Status**: No short interest data available.

This could mean:
- Symbol not covered by Yahoo Finance short interest
- FINRA short volume not yet published
- Very low short interest (not material)

Try a different symbol or check back later.
"""
    
    # Build output sections
    sections = []
    
    # Yahoo Finance short interest
    if yahoo_data:
        short_pct = yahoo_data["short_pct_float"]
        days_cover = yahoo_data["short_ratio"]
        shares_short = yahoo_data["shares_short"]
        short_change = yahoo_data["short_change_pct"]
        
        # Squeeze potential assessment
        if short_pct > 30:
            squeeze_risk = "🔴 EXTREME - Very high squeeze potential"
            squeeze_detail = "Shorts are extremely crowded. Any positive catalyst could trigger violent covering."
        elif short_pct > 20:
            squeeze_risk = "🟠 HIGH - Significant squeeze potential"
            squeeze_detail = "Heavy short positioning. Vulnerable to covering rallies on good news."
        elif short_pct > 10:
            squeeze_risk = "🟡 MODERATE - Elevated short interest"
            squeeze_detail = "Above-average shorts. Watch for covering on momentum."
        elif short_pct > 5:
            squeeze_risk = "⚪ LOW - Normal short levels"
            squeeze_detail = "Shorts not crowded. Unlikely to see squeeze dynamics."
        else:
            squeeze_risk = "🟢 MINIMAL - Low short interest"
            squeeze_detail = "Very few shorts. Not a factor in price action."
        
        # Days to cover interpretation
        if days_cover > 7:
            cover_pressure = "🔴 HIGH - Would take >1 week for shorts to cover"
        elif days_cover > 4:
            cover_pressure = "🟠 MODERATE - Multiple days needed to cover"
        elif days_cover > 2:
            cover_pressure = "🟡 LOW - Can cover relatively quickly"
        else:
            cover_pressure = "🟢 MINIMAL - Easy to cover"
        
        # Short interest trend
        if short_change > 10:
            trend = "📈 INCREASING - Shorts adding to positions"
        elif short_change > 0:
            trend = "➡️ SLIGHTLY UP - Modest short increase"
        elif short_change < -10:
            trend = "📉 DECREASING - Shorts covering"
        elif short_change < 0:
            trend = "➡️ SLIGHTLY DOWN - Modest covering"
        else:
            trend = "➡️ FLAT - No significant change"
        
        sections.append(f"""### Short Interest (Yahoo Finance)
| Metric | Value | Assessment |
|--------|-------|------------|
| Short % of Float | {short_pct:.1f}% | {squeeze_risk.split(' - ')[0]} |
| Shares Short | {shares_short:,} | - |
| Days to Cover | {days_cover:.1f} | {cover_pressure.split(' - ')[0]} |
| MoM Change | {short_change:+.1f}% | {trend.split(' - ')[0]} |

**Squeeze Assessment**: {squeeze_risk}
{squeeze_detail}

**Covering Pressure**: {cover_pressure}

**Trend**: {trend}""")
    
    # FINRA Short Volume
    if short_volume_data:
        latest = short_volume_data[0]
        short_vol = latest["short_volume"]
        total_vol = latest["total_volume"]
        short_ratio = (short_vol / total_vol * 100) if total_vol > 0 else 0
        
        # Calculate average
        avg_ratio = 0
        if len(short_volume_data) > 1:
            ratios = [(d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0 
                      for d in short_volume_data]
            avg_ratio = sum(ratios) / len(ratios)
        
        # Trend
        if len(short_volume_data) >= 3:
            recent = sum(ratios[:2]) / 2
            older = sum(ratios[2:]) / len(ratios[2:]) if len(ratios) > 2 else recent
            if recent > older * 1.1:
                vol_trend = "📈 Rising short volume"
            elif recent < older * 0.9:
                vol_trend = "📉 Falling short volume (covering?)"
            else:
                vol_trend = "➡️ Stable"
        else:
            vol_trend = "N/A"
        
        # Daily history
        history_lines = []
        for d in short_volume_data[:5]:
            ratio = (d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0
            history_lines.append(f"  {d['date']}: {ratio:.1f}% ({d['short_volume']:,} / {d['total_volume']:,})")
        
        sections.append(f"""### Daily Short Volume (FINRA, T+1)
- **Latest Short Ratio**: {short_ratio:.1f}%
- **5-Day Avg Short Ratio**: {avg_ratio:.1f}%
- **Trend**: {vol_trend}

#### Recent History
{chr(10).join(history_lines)}

*Note: Short volume ≠ short interest. This is daily flow, not outstanding positions.*""")
    
    # Trading implications
    implications = []
    
    if yahoo_data:
        if yahoo_data["short_pct_float"] > 15:
            implications.append("⚠️ High short interest creates squeeze risk on any positive catalyst")
        if yahoo_data["short_change_pct"] > 10:
            implications.append("🔴 Shorts are adding - bearish momentum may continue")
        if yahoo_data["short_change_pct"] < -10:
            implications.append("🟢 Shorts are covering - may support price")
        if yahoo_data["short_ratio"] > 5:
            implications.append("⚠️ High days-to-cover means covering could extend over days")
    
    if short_volume_data and len(short_volume_data) >= 3:
        ratios = [(d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0 
                  for d in short_volume_data]
        if ratios[0] > 55:
            implications.append("🔴 Very high daily short volume - active bearish trading")
        elif ratios[0] < 35:
            implications.append("🟢 Low daily short volume - less selling pressure")
    
    if not implications:
        implications.append("⚪ Short metrics within normal ranges")
    
    sections.append(f"""### Trading Implications
{chr(10).join(f'- {i}' for i in implications)}""")
    
    return f"""## Short Interest Analysis: {symbol} ({curr_date})

{chr(10).join(sections)}

---
*Data sources: Yahoo Finance (short interest, ~1-2 week lag), FINRA (daily short volume, T+1)*
"""


@tool
async def get_squeeze_candidates_assessment(
    symbol: Annotated[str, "Stock ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Assess short squeeze potential for a stock.
    
    Evaluates key squeeze factors:
    1. Short % of float (>20% = high)
    2. Days to cover (>4 = high)
    3. Short interest trend (rising/falling)
    4. Daily short volume patterns
    5. Float size (small float = more volatile)
    
    Returns a squeeze score and detailed assessment.
    
    Note: This is for educational purposes. Short squeezes are
    highly speculative and risky. Past squeeze setups don't
    guarantee future squeeze events.
    """
    symbol = symbol.upper().strip()
    
    try:
        target_date = _parse_date(curr_date)
    except ValueError:
        return f"Error: Invalid date format '{curr_date}'. Use yyyy-mm-dd."
    
    # Get data concurrently
    async def fetch_yahoo():
        try:
            return await asyncio.to_thread(_get_yahoo_short_data, symbol)
        except Exception:
            return None

    async def fetch_finra():
        return await asyncio.to_thread(_get_recent_short_volume, symbol, target_date)

    yahoo_data, short_volume_data = await asyncio.gather(fetch_yahoo(), fetch_finra())
    
    if not yahoo_data:
        return f"""## Squeeze Assessment: {symbol}

**Status**: Insufficient data for squeeze analysis.

Yahoo Finance short interest data not available for this symbol.
This may indicate:
- Very low/no short interest
- Symbol not covered by Yahoo
- Data temporarily unavailable
"""
    
    # Scoring system (0-100)
    score = 0
    factors = []
    
    # Factor 1: Short % of float (0-30 points)
    short_pct = yahoo_data["short_pct_float"]
    if short_pct > 30:
        score += 30
        factors.append(f"✅ Short % of float: {short_pct:.1f}% (EXTREME, +30)")
    elif short_pct > 20:
        score += 25
        factors.append(f"✅ Short % of float: {short_pct:.1f}% (HIGH, +25)")
    elif short_pct > 15:
        score += 18
        factors.append(f"✅ Short % of float: {short_pct:.1f}% (ELEVATED, +18)")
    elif short_pct > 10:
        score += 10
        factors.append(f"⚪ Short % of float: {short_pct:.1f}% (MODERATE, +10)")
    else:
        factors.append(f"❌ Short % of float: {short_pct:.1f}% (LOW, +0)")
    
    # Factor 2: Days to cover (0-25 points)
    days_cover = yahoo_data["short_ratio"]
    if days_cover > 8:
        score += 25
        factors.append(f"✅ Days to cover: {days_cover:.1f} (VERY HIGH, +25)")
    elif days_cover > 5:
        score += 18
        factors.append(f"✅ Days to cover: {days_cover:.1f} (HIGH, +18)")
    elif days_cover > 3:
        score += 10
        factors.append(f"⚪ Days to cover: {days_cover:.1f} (MODERATE, +10)")
    else:
        factors.append(f"❌ Days to cover: {days_cover:.1f} (LOW, +0)")
    
    # Factor 3: Short interest trend (0-20 points)
    short_change = yahoo_data["short_change_pct"]
    if short_change > 15:
        score += 20
        factors.append(f"✅ SI trend: +{short_change:.1f}% MoM (RISING FAST, +20)")
    elif short_change > 5:
        score += 12
        factors.append(f"✅ SI trend: +{short_change:.1f}% MoM (RISING, +12)")
    elif short_change < -10:
        # Covering reduces squeeze potential
        factors.append(f"❌ SI trend: {short_change:.1f}% MoM (COVERING, +0)")
    else:
        score += 5
        factors.append(f"⚪ SI trend: {short_change:+.1f}% MoM (STABLE, +5)")
    
    # Factor 4: Float size (0-15 points) - smaller float = more volatile
    float_shares = yahoo_data["float_shares"]
    if float_shares > 0:
        if float_shares < 20_000_000:
            score += 15
            factors.append(f"✅ Float: {float_shares/1_000_000:.1f}M shares (SMALL, +15)")
        elif float_shares < 50_000_000:
            score += 10
            factors.append(f"✅ Float: {float_shares/1_000_000:.1f}M shares (MEDIUM, +10)")
        elif float_shares < 100_000_000:
            score += 5
            factors.append(f"⚪ Float: {float_shares/1_000_000:.1f}M shares (LARGE, +5)")
        else:
            factors.append(f"❌ Float: {float_shares/1_000_000:.1f}M shares (VERY LARGE, +0)")
    
    # Factor 5: Daily short volume trend (0-10 points)
    if short_volume_data and len(short_volume_data) >= 3:
        ratios = [(d["short_volume"] / d["total_volume"] * 100) if d["total_volume"] > 0 else 0 
                  for d in short_volume_data]
        recent_avg = sum(ratios[:2]) / 2
        
        if recent_avg > 55:
            score += 10
            factors.append(f"✅ Daily short vol: {recent_avg:.0f}% (VERY HIGH, +10)")
        elif recent_avg > 48:
            score += 5
            factors.append(f"⚪ Daily short vol: {recent_avg:.0f}% (ELEVATED, +5)")
        else:
            factors.append(f"❌ Daily short vol: {recent_avg:.0f}% (NORMAL, +0)")
    else:
        factors.append("⚪ Daily short vol: N/A (no data)")
    
    # Overall assessment
    if score >= 75:
        grade = "🔴 EXTREME SQUEEZE SETUP"
        assessment = "This stock has multiple factors that could trigger a violent short squeeze. However, extreme setups can persist for extended periods before materializing."
    elif score >= 55:
        grade = "🟠 HIGH SQUEEZE POTENTIAL"
        assessment = "Elevated squeeze risk. A positive catalyst could trigger meaningful short covering."
    elif score >= 35:
        grade = "🟡 MODERATE SQUEEZE POTENTIAL"
        assessment = "Some squeeze factors present, but not at extreme levels. Watch for catalysts."
    elif score >= 20:
        grade = "⚪ LOW SQUEEZE POTENTIAL"
        assessment = "Limited squeeze factors. Shorts not particularly crowded."
    else:
        grade = "🟢 MINIMAL SQUEEZE POTENTIAL"
        assessment = "Very low short interest. Squeeze dynamics unlikely to be a factor."
    
    return f"""## Short Squeeze Assessment: {symbol} ({curr_date})

### Squeeze Score: {score}/100
**Rating**: {grade}

### Scoring Breakdown
{chr(10).join(factors)}

### Assessment
{assessment}

### Key Metrics Summary
| Metric | Value | Squeeze Impact |
|--------|-------|----------------|
| Short % Float | {short_pct:.1f}% | {'High' if short_pct > 15 else 'Low'} |
| Days to Cover | {days_cover:.1f} | {'High' if days_cover > 4 else 'Low'} |
| SI Change MoM | {short_change:+.1f}% | {'Rising' if short_change > 5 else 'Falling' if short_change < -5 else 'Flat'} |
| Float Size | {float_shares/1_000_000:.1f}M | {'Small' if float_shares < 50_000_000 else 'Large'} |

### Risk Warning
⚠️ Short squeeze trades are highly speculative. Characteristics that make a stock "squeezable" also make it extremely volatile and risky. High short interest often reflects legitimate bearish concerns about the company. This assessment is for informational purposes only.
"""
