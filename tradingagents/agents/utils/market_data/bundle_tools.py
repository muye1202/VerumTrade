from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict

from langchain_core.tools import tool

from tradingagents.agents.utils.agent_runtime.agent_utils import (
    get_company_news_window,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_global_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_news,
)
from tradingagents.agents.utils.market_data.dark_pool_tools import (
    get_dark_pool_short_volume,
    get_off_exchange_volume_context,
)
from tradingagents.agents.utils.market_data.options_flow_tools import (
    get_options_sentiment_summary,
    get_unusual_options_activity,
)
from tradingagents.agents.utils.market_data.price_action_tools import get_price_action_summary
from tradingagents.agents.utils.market_data.short_interest_tools import (
    get_short_interest_data,
    get_squeeze_candidates_assessment,
)
from tradingagents.agents.utils.market_data.technical_indicators_tools import get_indicators
from tradingagents.agents.utils.market_data.vwap_tools import (
    get_intraday_vwap_position,
    get_multi_day_vwap_context,
)


async def _run_tool(tool_obj: Any, payload: Dict[str, Any]) -> str:
    try:
        return str(await tool_obj.ainvoke(payload))
    except Exception as e:
        return f"ToolError[{getattr(tool_obj, 'name', 'unknown')}]: {type(e).__name__}: {e}"


def _parse_indicator_csv(indicators_csv: str) -> list[str]:
    allowed = [
        "close_50_sma",
        "close_200_sma",
        "close_10_ema",
        "macd",
        "macds",
        "macdh",
        "rsi",
        "boll",
        "boll_ub",
        "boll_lb",
        "atr",
        "vwma",
        "mfi",
    ]
    requested = [x.strip() for x in str(indicators_csv or "").split(",") if x.strip()]
    if not requested:
        requested = ["close_10_ema", "close_50_sma", "macd", "rsi", "atr"]

    filtered: list[str] = []
    for indicator in requested:
        if indicator in allowed and indicator not in filtered:
            filtered.append(indicator)
        if len(filtered) >= 6:
            break
    return filtered or ["close_10_ema", "close_50_sma", "macd", "rsi", "atr"]


@tool
async def get_market_data_bundle(
    symbol: Annotated[str, "Ticker symbol, e.g. AAPL"],
    curr_date: Annotated[str, "Current trading date in YYYY-mm-dd format"],
    indicators_csv: Annotated[
        str,
        "Comma-separated indicator names (max 6). Example: close_10_ema,close_50_sma,macd,rsi,atr",
    ] = "close_10_ema,close_50_sma,macd,rsi,atr",
    indicator_look_back_days: Annotated[int, "Look-back window for indicators"] = 90,
) -> str:
    """
    Fetch market analyst data in a single bundled tool call.
    Runs all sub-tools concurrently to reduce extra LLM planning turns.
    """
    indicators = _parse_indicator_csv(indicators_csv)
    tasks: dict[str, asyncio.Task] = {
        "price_action_summary": asyncio.create_task(
            _run_tool(
                get_price_action_summary,
                {"symbol": symbol, "curr_date": curr_date, "look_back_days": 180},
            )
        ),
        "intraday_vwap_position": asyncio.create_task(
            _run_tool(get_intraday_vwap_position, {"symbol": symbol, "curr_date": curr_date})
        ),
        "multi_day_vwap_context": asyncio.create_task(
            _run_tool(get_multi_day_vwap_context, {"symbol": symbol, "curr_date": curr_date})
        ),
        "unusual_options_activity": asyncio.create_task(
            _run_tool(get_unusual_options_activity, {"symbol": symbol, "curr_date": curr_date})
        ),
        "options_sentiment_summary": asyncio.create_task(
            _run_tool(get_options_sentiment_summary, {"symbol": symbol, "curr_date": curr_date})
        ),
        "dark_pool_short_volume": asyncio.create_task(
            _run_tool(get_dark_pool_short_volume, {"symbol": symbol, "curr_date": curr_date})
        ),
        "off_exchange_volume_context": asyncio.create_task(
            _run_tool(get_off_exchange_volume_context, {"symbol": symbol, "curr_date": curr_date})
        ),
        "short_interest_data": asyncio.create_task(
            _run_tool(get_short_interest_data, {"symbol": symbol, "curr_date": curr_date})
        ),
        "squeeze_candidates_assessment": asyncio.create_task(
            _run_tool(get_squeeze_candidates_assessment, {"symbol": symbol, "curr_date": curr_date})
        ),
    }

    for indicator in indicators:
        tasks[f"indicator::{indicator}"] = asyncio.create_task(
            _run_tool(
                get_indicators,
                {
                    "symbol": symbol,
                    "indicator": indicator,
                    "curr_date": curr_date,
                    "look_back_days": int(indicator_look_back_days),
                },
            )
        )

    results = {key: await task for key, task in tasks.items()}
    lines = [
        f"## Market Data Bundle: {symbol} ({curr_date})",
        "Use sections below directly; each subsection is a raw sub-tool result.",
    ]
    for key, value in results.items():
        lines.append("")
        lines.append(f"### {key}")
        lines.append(value)
    return "\n".join(lines)


@tool
async def get_fundamentals_data_bundle(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "Current trading date in YYYY-mm-dd format"],
    freq: Annotated[str, "Statement frequency (quarterly or annual)"] = "quarterly",
) -> str:
    """Fetch fundamentals analyst data in a single bundled tool call."""
    tasks = {
        "fundamentals": asyncio.create_task(
            _run_tool(get_fundamentals, {"ticker": ticker, "curr_date": curr_date})
        ),
        "income_statement": asyncio.create_task(
            _run_tool(
                get_income_statement,
                {"ticker": ticker, "freq": freq, "curr_date": curr_date},
            )
        ),
        "balance_sheet": asyncio.create_task(
            _run_tool(
                get_balance_sheet,
                {"ticker": ticker, "freq": freq, "curr_date": curr_date},
            )
        ),
        "cashflow": asyncio.create_task(
            _run_tool(get_cashflow, {"ticker": ticker, "freq": freq, "curr_date": curr_date})
        ),
        "insider_transactions": asyncio.create_task(
            _run_tool(get_insider_transactions, {"ticker": ticker, "curr_date": curr_date})
        ),
        "insider_sentiment": asyncio.create_task(
            _run_tool(get_insider_sentiment, {"ticker": ticker, "curr_date": curr_date})
        ),
    }
    results = {key: await task for key, task in tasks.items()}
    lines = [f"## Fundamentals Data Bundle: {ticker} ({curr_date})"]
    for key, value in results.items():
        lines.append("")
        lines.append(f"### {key}")
        lines.append(value)
    return "\n".join(lines)


@tool
async def get_news_data_bundle(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "Current trading date in YYYY-mm-dd format"],
    company_look_back_days: Annotated[int, "Company-news look-back days"] = 14,
    global_look_back_days: Annotated[int, "Global-news look-back days"] = 5,
    global_limit: Annotated[int, "Max global headlines"] = 10,
) -> str:
    """Fetch news analyst data in a single bundled tool call."""
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_date = (curr_dt - timedelta(days=int(company_look_back_days))).strftime("%Y-%m-%d")
    except Exception:
        start_date = curr_date

    tasks = {
        "company_news_window": asyncio.create_task(
            _run_tool(
                get_company_news_window,
                {"ticker": ticker, "curr_date": curr_date, "look_back_days": int(company_look_back_days)},
            )
        ),
        "company_news_raw": asyncio.create_task(
            _run_tool(get_news, {"ticker": ticker, "start_date": start_date, "end_date": curr_date})
        ),
        "global_news": asyncio.create_task(
            _run_tool(
                get_global_news,
                {
                    "curr_date": curr_date,
                    "look_back_days": int(global_look_back_days),
                    "limit": int(global_limit),
                },
            )
        ),
    }
    results = {key: await task for key, task in tasks.items()}
    lines = [f"## News Data Bundle: {ticker} ({curr_date})"]
    for key, value in results.items():
        lines.append("")
        lines.append(f"### {key}")
        lines.append(value)
    return "\n".join(lines)


@tool
async def get_sentiment_data_bundle(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "Current trading date in YYYY-mm-dd format"],
    look_back_days: Annotated[int, "Look-back days for sentiment proxy"] = 21,
) -> str:
    """Fetch sentiment analyst data in a single bundled tool call."""
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_date = (curr_dt - timedelta(days=int(look_back_days))).strftime("%Y-%m-%d")
    except Exception:
        start_date = curr_date

    tasks = {
        "company_news_window": asyncio.create_task(
            _run_tool(
                get_company_news_window,
                {"ticker": ticker, "curr_date": curr_date, "look_back_days": int(look_back_days)},
            )
        ),
        "company_news_raw": asyncio.create_task(
            _run_tool(get_news, {"ticker": ticker, "start_date": start_date, "end_date": curr_date})
        ),
    }
    results = {key: await task for key, task in tasks.items()}
    lines = [f"## Sentiment Data Bundle: {ticker} ({curr_date})"]
    for key, value in results.items():
        lines.append("")
        lines.append(f"### {key}")
        lines.append(value)
    return "\n".join(lines)
