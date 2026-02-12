from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from typing import List, Dict, Any, Annotated
import json
from datetime import datetime, timedelta
from tradingagents.agents.utils.agent_runtime.agent_utils import (
    get_stock_data,
    get_indicators,
    get_global_news,
)


@tool
def scan_sector_performance(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Days to look back"] = 30,
) -> str:
    """
    Scan major sector ETFs to identify which sectors are showing momentum.
    Returns performance data for major sector ETFs.
    """
    sectors = {
        "XLK": "Technology",
        "XLF": "Financials", 
        "XLE": "Energy",
        "XLV": "Healthcare",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLI": "Industrials",
        "XLB": "Materials",
        "XLRE": "Real Estate",
        "XLU": "Utilities",
        "XLC": "Communication Services"
    }
    
    from datetime import datetime, timedelta
    end_date = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=look_back_days)
    
    from tradingagents.dataflows.interface import route_to_vendor
    
    results = []
    for ticker, name in sectors.items():
        try:
            # Get price data
            data = route_to_vendor(
                "get_stock_data",
                ticker,
                start_date.strftime("%Y-%m-%d"),
                curr_date
            )
            
            # Parse to get first and last close price
            lines = [l for l in data.split('\n') if l.strip() and not l.startswith('#')]
            if len(lines) > 2:
                header = lines[0]
                first_line = lines[1].split(',')
                last_line = lines[-1].split(',')
                
                # Find Close column
                close_idx = header.split(',').index('Close')
                first_close = float(first_line[close_idx])
                last_close = float(last_line[close_idx])
                
                pct_change = ((last_close - first_close) / first_close) * 100
                
                results.append({
                    "sector": name,
                    "ticker": ticker,
                    "return_pct": round(pct_change, 2)
                })
        except Exception as e:
            continue
    
    # Sort by performance
    results.sort(key=lambda x: x["return_pct"], reverse=True)
    
    output = f"## Sector Performance (Last {look_back_days} days to {curr_date}):\n\n"
    output += "| Rank | Sector | Ticker | Return % |\n"
    output += "|------|--------|--------|----------|\n"
    for i, r in enumerate(results, 1):
        output += f"| {i} | {r['sector']} | {r['ticker']} | {r['return_pct']:+.2f}% |\n"
    
    return output


@tool
def screen_technical_breakouts(
    universe: Annotated[List[str], "List of ticker symbols to screen"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """
    Screen a list of tickers for technical breakout signals:
    - Price above 50-day and 200-day moving averages
    - Recent momentum (20-day return > 5%)
    - Volume above average
    """
    from tradingagents.dataflows.interface import route_to_vendor
    from datetime import datetime, timedelta
    
    end_date = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=250)  # Get enough history for 200 MA
    
    candidates = []
    
    for ticker in universe:
        try:
            # Get indicators
            sma_50 = route_to_vendor(
                "get_indicators",
                ticker,
                "close_50_sma",
                curr_date,
                5  # Just need recent values
            )
            
            sma_200 = route_to_vendor(
                "get_indicators", 
                ticker,
                "close_200_sma",
                curr_date,
                5
            )
            
            # Get price data for momentum
            price_data = route_to_vendor(
                "get_stock_data",
                ticker,
                (end_date - timedelta(days=30)).strftime("%Y-%m-%d"),
                curr_date
            )
            
            # Parse to get current price vs 20 days ago
            lines = [l for l in price_data.split('\n') if l.strip() and not l.startswith('#')]
            if len(lines) > 20:
                header = lines[0]
                close_idx = header.split(',').index('Close')
                
                current_price = float(lines[-1].split(',')[close_idx])
                price_20d_ago = float(lines[-20].split(',')[close_idx])
                
                momentum_20d = ((current_price - price_20d_ago) / price_20d_ago) * 100
                
                # Extract latest SMA values
                sma_50_val = float(sma_50.split('\n')[-2].split(':')[1].strip())
                sma_200_val = float(sma_200.split('\n')[-2].split(':')[1].strip())
                
                # Check breakout criteria
                if (current_price > sma_50_val and 
                    current_price > sma_200_val and 
                    momentum_20d > 5):
                    
                    candidates.append({
                        "ticker": ticker,
                        "price": current_price,
                        "sma_50": sma_50_val,
                        "sma_200": sma_200_val,
                        "momentum_20d": round(momentum_20d, 2)
                    })
        except Exception as e:
            continue
    
    # Sort by momentum
    candidates.sort(key=lambda x: x["momentum_20d"], reverse=True)
    
    output = f"## Technical Breakout Candidates ({curr_date}):\n\n"
    if candidates:
        output += "| Ticker | Price | 50 SMA | 200 SMA | 20d Momentum |\n"
        output += "|--------|-------|--------|---------|-------------|\n"
        for c in candidates[:15]:  # Top 15
            output += f"| {c['ticker']} | ${c['price']:.2f} | ${c['sma_50']:.2f} | ${c['sma_200']:.2f} | {c['momentum_20d']:+.2f}% |\n"
    else:
        output += "No breakout candidates found.\n"
    
    return output


@tool
def scan_news_catalysts(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Days to look back"] = 3,
) -> str:
    """
    Scan recent news for potential stock catalysts like earnings beats,
    FDA approvals, mergers, analyst upgrades, etc.
    """
    from tradingagents.dataflows.interface import route_to_vendor
    
    news = route_to_vendor(
        "get_global_news",
        curr_date,
        look_back_days,
        limit=20
    )
    
    return f"## Recent Market News & Catalysts:\n\n{news}"


def create_discovery_agent(llm):
    """
    Create an agent that discovers promising stocks to analyze.
    Uses technical screening, news analysis, and sector rotation to find candidates.
    """
    
    def discovery_node(state):
        current_date = state["trade_date"]
        universe = state.get("universe", [])  # User-provided universe
        
        # Tools for discovery
        tools = [
            scan_sector_performance,
            screen_technical_breakouts,
            scan_news_catalysts,
        ]
        
        system_message = """You are an expert quantitative analyst and stock screener. Your mission is to discover the most promising stocks for detailed analysis.

## Your Objective
Identify 3-5 stocks with the highest potential for profitable trading based on:

1. **Technical Momentum** - Stocks breaking out, showing strong trends
2. **Sector Rotation** - Stocks in sectors with positive momentum
3. **News Catalysts** - Stocks with recent positive news or events
4. **Quality Filters** - Only liquid, established companies

## Discovery Process

**Step 1: Understand the Market Context**
- Scan sector performance to identify which sectors are hot
- Review recent market news for themes and catalysts

**Step 2: Screen for Technical Signals**
If a stock universe is provided, screen it for:
- Price above both 50-day and 200-day moving averages (uptrend)
- Recent momentum (20-day return > 5%)
- Strong relative performance vs sector

**Step 3: Synthesize Findings**
Combine technical signals + sector trends + news catalysts to identify:
- Which stocks have multiple positive signals aligning?
- Which sectors are attracting capital?
- What themes are emerging from news?

**Step 4: Generate Candidate List**
Select 3-5 tickers with the strongest conviction based on:
- Multiple positive signals (technical + fundamental + news)
- Clear catalyst or momentum
- Alignment with sector rotation

## Quality Filters (CRITICAL)
Only recommend stocks that meet:
- Market cap > $1 billion (avoid penny stocks)
- Average daily volume > 500k shares (ensure liquidity)
- Listed on major exchanges (NYSE, NASDAQ)
- No stocks in distress or bankruptcy risk

## Output Format
Provide your analysis as:

1. **Market Context Summary** (2-3 sentences on current market environment)

2. **Top Sector Themes** (Which 2-3 sectors show strength and why?)

3. **Recommended Stocks** (3-5 tickers):

For each stock, provide:
- **Ticker**: SYMBOL
- **Sector**: Sector name
- **Conviction**: High/Medium (why this stock?)
- **Key Signals**: Technical/News/Fundamental reasons
- **Catalyst**: What's driving the opportunity?

4. **Final Recommendation**: Your top pick and why.

## Example Output Structure

**Market Context**: Technology sector showing strong momentum post-earnings season. Energy rotating out as oil prices decline.

**Top Sectors**: 
1. Technology - AI adoption driving growth
2. Healthcare - Biotech approvals accelerating

**Recommended Stocks**:

1. **NVDA** (Technology)
   - Conviction: High
   - Signals: Price above MAs, 20d momentum +15%, sector leading
   - Catalyst: AI infrastructure demand, recent earnings beat

2. **MRNA** (Healthcare)
   - Conviction: Medium
   - Signals: Breakout from consolidation, sector rotation
   - Catalyst: New vaccine approval expected

**Top Pick**: NVDA - Strongest technical setup with clear fundamental catalyst

---

Now, use the available tools to discover promising opportunities. Be methodical and data-driven."""

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            ("human", f"""Current Date: {current_date}

Stock Universe: {universe if universe else "Use your tools to find the best opportunities across the market."}

Please discover 3-5 of the most promising stocks for detailed analysis."""),
            MessagesPlaceholder(variable_name="messages"),
        ])
        
        chain = prompt | llm.bind_tools(tools)
        
        # Run discovery
        result = chain.invoke(state.get("messages", []))
        
        # Extract recommended tickers from the response
        discovered_tickers = extract_tickers_from_response(result.content)
        
        return {
            "messages": [result],
            "discovery_report": result.content,
            "discovered_tickers": discovered_tickers,
        }
    
    return discovery_node


def extract_tickers_from_response(response_text: str) -> List[str]:
    """
    Extract ticker symbols from LLM response.
    Looks for patterns like "**TICKER**" or stock symbols in context.
    """
    import re
    
    # Pattern 1: Markdown bold tickers like **NVDA**
    tickers = re.findall(r'\*\*([A-Z]{1,5})\*\*', response_text)
    
    # Pattern 2: Standalone uppercase words 1-5 chars
    if not tickers:
        words = response_text.split()
        tickers = [w.strip('.,()[]') for w in words 
                   if w.isupper() and 1 <= len(w) <= 5 and w.isalpha()]
    
    # Deduplicate while preserving order
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)
    
    return unique_tickers[:5]  # Limit to top 5
