# tradingagents/agents/discovery/stock_recommender.py
"""
Stock Recommender Agent: Uses LLM deep-think with web search and news tools
to discover and recommend promising stocks for trading analysis.
"""

from typing import List, Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
import re
import logging

from .stock_screener import (
    scan_sector_performance,
    screen_technical_breakouts,
    scan_news_catalysts,
)

logger = logging.getLogger(__name__)


# Default stock universe for technical screening when no specific universe provided
DEFAULT_SCREENING_UNIVERSE = [
    # Large-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    # Financials
    "JPM", "BAC", "GS", "MS", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY",
    # Consumer
    "WMT", "HD", "MCD", "NKE", "SBUX", "COST",
    # Energy / Industrials
    "XOM", "CVX", "CAT", "BA", "GE", "UPS",
    # Semiconductors
    "AMD", "INTC", "AVGO", "QCOM", "MU",
]


RECOMMENDER_SYSTEM_PROMPT = """You are an expert quantitative analyst and stock recommender. Your mission is to discover the most promising stocks for detailed trading analysis.

## Your Objective

Identify 3-5 stocks with the highest potential based on:
1. **Market Context** - Current trends and themes from news/web search
2. **Sector Momentum** - Which sectors are outperforming
3. **Technical Signals** - Stocks showing breakout patterns
4. **Catalysts** - Upcoming events or recent news that could drive price action

## Discovery Process

**Step 1: Gather Intelligence**
- Use web search to find trending market themes, hot stocks, and emerging opportunities
- Scan global news for macro trends and sector rotation signals
- Look for specific catalysts (earnings, product launches, regulatory changes)

**Step 2: Analyze Sectors**
- Identify which sectors are showing momentum
- Find stocks in those strong sectors

**Step 3: Screen for Quality**
- Technical breakouts (price above moving averages, momentum)
- Liquid, established companies (avoid penny stocks)

**Step 4: Synthesize & Recommend**
Provide 3-5 top picks with clear reasoning

## Output Format

Structure your final recommendation as:

## Market Context
[2-3 sentences on current market environment and key themes]

## Top Sector Themes
1. [Sector 1] - [Brief reason]
2. [Sector 2] - [Brief reason]

## Recommended Stocks

### 1. **[TICKER]** - [Company Name]
- **Sector:** [Sector]
- **Conviction:** High/Medium
- **Why:** [Key reasons - technical, fundamental, catalyst]
- **Catalyst:** [What's driving the opportunity]

### 2. **[TICKER]** - [Company Name]
[Same format]

...

## Top Pick Summary
[TICKER1], [TICKER2], [TICKER3] (comma-separated tickers for easy parsing)

---

Use the available tools to gather data. Be thorough but efficient. Focus on actionable recommendations."""


def extract_recommended_tickers(response_text: str) -> List[str]:
    """Extract ticker symbols from the LLM recommendation response."""
    tickers = []

    # Pattern 1: Look for "Top Pick Summary" section with comma-separated tickers
    summary_match = re.search(
        r"Top Pick Summary[:\s]*\n?([A-Z, ]+)",
        response_text,
        re.IGNORECASE
    )
    if summary_match:
        raw = summary_match.group(1)
        tickers = [t.strip() for t in raw.split(",") if t.strip().isalpha() and len(t.strip()) <= 5]
        if tickers:
            return tickers[:5]

    # Pattern 2: Markdown bold tickers like **NVDA**
    bold_tickers = re.findall(r'\*\*([A-Z]{1,5})\*\*', response_text)
    if bold_tickers:
        seen = set()
        for t in bold_tickers:
            if t not in seen and t.isalpha():
                seen.add(t)
                tickers.append(t)
        if tickers:
            return tickers[:5]

    # Pattern 3: ### N. **TICKER** pattern
    header_tickers = re.findall(r'###\s*\d+\.\s*\*\*([A-Z]{1,5})\*\*', response_text)
    if header_tickers:
        return header_tickers[:5]

    # Pattern 4: Fallback - standalone uppercase 1-5 char words that look like tickers
    words = response_text.split()
    for w in words:
        clean = w.strip('.,()[]#*:')
        if (clean.isupper() and 1 <= len(clean) <= 5 and clean.isalpha() 
            and clean not in {"I", "A", "THE", "AND", "OR", "IS", "TO", "IN", "FOR", "OF", "ON"}):
            if clean not in tickers:
                tickers.append(clean)
    
    return tickers[:5]


class StockRecommenderAgent:
    """
    LLM-powered stock recommendation agent that uses:
    - Web search (for market trends and news)
    - Sector performance scanning
    - Technical breakout screening
    - News catalyst detection

    Works with GLM, OpenAI, and other LangChain-compatible LLMs.
    """

    def __init__(
        self,
        llm,
        enable_web_search: bool = True,
        screening_universe: Optional[List[str]] = None,
    ):
        """
        Initialize the stock recommender.

        Args:
            llm: LangChain-compatible LLM (ChatOpenAI, etc.)
            enable_web_search: Whether to enable web search via LLM tools
            screening_universe: List of tickers to screen for technicals
        """
        self.llm = llm
        self.enable_web_search = enable_web_search
        self.screening_universe = screening_universe or DEFAULT_SCREENING_UNIVERSE
        self.logger = logging.getLogger(self.__class__.__name__)

        # Tools for screening (LangChain tool format)
        self.screening_tools = [
            scan_sector_performance,
            screen_technical_breakouts,
            scan_news_catalysts,
        ]

    def _build_prompt(self, trade_date: str) -> ChatPromptTemplate:
        """Build the recommendation prompt."""
        return ChatPromptTemplate.from_messages([
            ("system", RECOMMENDER_SYSTEM_PROMPT),
            ("human", f"""Current Date: {trade_date}

Stock Universe for Technical Screening: {', '.join(self.screening_universe[:20])}

Please discover 3-5 of the most promising stocks for detailed trading analysis.

Use the available tools to:
1. Search the web for current market trends and hot stocks
2. Scan sector performance to identify which sectors are strong
3. Screen for technical breakouts in the stock universe
4. Check for recent news catalysts

Then synthesize your findings into clear recommendations."""),
            MessagesPlaceholder(variable_name="messages"),
        ])

    def _get_tools_config(self) -> List[Dict[str, Any]]:
        """
        Get tool configuration for the LLM.

        For GLM/Zhipu AI, includes web_search as a native tool.
        For other providers, relies on bound LangChain tools.
        """
        tools_config = []

        # Add web search for GLM (native tool)
        if self.enable_web_search:
            # GLM-specific web_search tool
            tools_config.append({
                "type": "web_search",
                "web_search": {
                    "enable": True,
                    "search_result": True,  # Return search results in response
                }
            })

        return tools_config

    def recommend(
        self,
        trade_date: str,
        max_iterations: int = 3,
    ) -> Dict[str, Any]:
        """
        Run the recommendation process.

        Args:
            trade_date: Target date for analysis (YYYY-MM-DD)
            max_iterations: Max tool-use iterations before forcing output

        Returns:
            Dict with:
                - tickers: List of recommended ticker symbols
                - report: Full recommendation report text
                - raw_messages: Message history for debugging
        """
        prompt = self._build_prompt(trade_date)

        # Bind tools to LLM
        llm_with_tools = self.llm.bind_tools(self.screening_tools)

        # For GLM with web search, we need to handle differently
        # since web_search is a native/built-in tool, not a LangChain tool
        extra_body = {}
        if self.enable_web_search:
            web_search_tools = self._get_tools_config()
            if web_search_tools:
                # GLM accepts tools in model_kwargs.extra_body
                extra_body["tools"] = web_search_tools

        messages = []
        chain = prompt | llm_with_tools

        # Initial invocation
        result = chain.invoke({"messages": messages})
        messages.append(result)

        # Tool execution loop
        iteration = 0
        while hasattr(result, "tool_calls") and result.tool_calls and iteration < max_iterations:
            iteration += 1
            
            # Execute each tool call
            for tool_call in result.tool_calls:
                tool_name = tool_call.get("name") or (tool_call.get("function") or {}).get("name")
                tool_args = tool_call.get("args") or (tool_call.get("function") or {}).get("arguments", {})
                
                if isinstance(tool_args, str):
                    import json
                    try:
                        tool_args = json.loads(tool_args)
                    except:
                        tool_args = {}

                self.logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

                # Find and execute the tool
                tool_result = f"Tool {tool_name} not found"
                for tool in self.screening_tools:
                    if tool.name == tool_name:
                        try:
                            tool_result = tool.invoke(tool_args)
                        except Exception as e:
                            tool_result = f"Error executing {tool_name}: {str(e)}"
                        break
                
                # Add tool result to messages
                from langchain_core.messages import ToolMessage
                tool_msg = ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tool_call.get("id", f"call_{iteration}"),
                )
                messages.append(tool_msg)

            # Continue the conversation
            result = chain.invoke({"messages": messages})
            messages.append(result)

        # Extract final response
        final_content = ""
        if hasattr(result, "content"):
            final_content = result.content
        elif isinstance(result, dict):
            final_content = result.get("content", str(result))
        else:
            final_content = str(result)

        # Extract tickers from response
        tickers = extract_recommended_tickers(final_content)

        return {
            "tickers": tickers,
            "report": final_content,
            "raw_messages": messages,
            "iterations": iteration,
        }


def create_stock_recommender(
    llm,
    enable_web_search: bool = True,
    screening_universe: Optional[List[str]] = None,
) -> StockRecommenderAgent:
    """Factory function to create a StockRecommenderAgent."""
    return StockRecommenderAgent(
        llm=llm,
        enable_web_search=enable_web_search,
        screening_universe=screening_universe,
    )
