from langchain_core.tools import tool
from typing import Annotated
import asyncio
from opentrace.dataflows.interface import route_to_vendor
from datetime import datetime, timedelta

@tool
async def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.

    Returns news articles with TEXT-BASED SENTIMENT scores derived from NLP
    analysis of headlines and article content (e.g., "Bullish", "Bearish", "Neutral").

    This is different from get_insider_sentiment() which measures ACTION-BASED
    sentiment from insider trading behavior.

    Uses the configured news_data vendor.

    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data with sentiment scores
    """
    return await asyncio.to_thread(route_to_vendor, "get_news", ticker, start_date, end_date)


@tool
async def get_company_news_window(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of calendar days to look back"] = 21,
) -> str:
    """
    Convenience wrapper around get_news() that computes the date window.

    Helps analysts avoid date arithmetic mistakes in prompts. Uses the configured news_data vendor.
    """
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    except Exception as e:
        return f"Error: curr_date must be yyyy-mm-dd, got '{curr_date}': {e}"

    start_date = (curr_dt - timedelta(days=int(look_back_days))).strftime("%Y-%m-%d")
    end_date = curr_dt.strftime("%Y-%m-%d")
    return await asyncio.to_thread(route_to_vendor, "get_news", ticker, start_date, end_date)

@tool
async def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor.
    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back (default 7)
        limit (int): Maximum number of articles to return (default 5)
    Returns:
        str: A formatted string containing global news data
    """
    return await asyncio.to_thread(route_to_vendor, "get_global_news", curr_date, look_back_days, limit)

@tool
async def get_insider_sentiment(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve insider sentiment information about a company.

    IMPORTANT: This measures ACTION-BASED sentiment, NOT text-based sentiment.
    "Insider sentiment" refers to the confidence/outlook IMPLIED by insider trading
    behavior - specifically when company executives and directors BUY or SELL their
    own company's stock.

    The sentiment is derived from analyzing:
    - Net share changes (total insider buys - total insider sells)
    - Share purchase ratio (% of insider transactions that are purchases)
    - Dollar values of insider purchases vs sales

    Interpretation:
    - Net positive (more buying) -> Bullish insider sentiment (insiders confident)
    - Net negative (more selling) -> Bearish insider sentiment (insiders cautious)

    This is fundamentally different from get_news() which provides TEXT-BASED
    sentiment from NLP analysis of news article language.

    Uses the configured news_data vendor.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A report of insider sentiment data derived from trading actions
    """
    # Some vendors reject future dates; treat curr_date as "as-of" and clamp to today.
    effective_date = curr_date
    try:
        cd = datetime.strptime(curr_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        if cd > today:
            effective_date = today.strftime("%Y-%m-%d")
    except Exception:
        effective_date = curr_date

    try:
        return await asyncio.to_thread(route_to_vendor, "get_insider_sentiment", ticker, effective_date)
    except Exception as e:
        note = ""
        if effective_date != curr_date:
            note = f" (requested date {curr_date} is in the future; using {effective_date})"
        return (
            f"No insider sentiment data available for {ticker} as of {effective_date}{note}. "
            f"Reason: {type(e).__name__}: {e}"
        )

@tool
async def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A report of insider transaction data
    """
    # Some vendors reject future dates; treat curr_date as "as-of" and clamp to today.
    effective_date = curr_date
    try:
        cd = datetime.strptime(curr_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        if cd > today:
            effective_date = today.strftime("%Y-%m-%d")
    except Exception:
        effective_date = curr_date

    try:
        return await asyncio.to_thread(route_to_vendor, "get_insider_transactions", ticker, effective_date)
    except Exception as e:
        note = ""
        if effective_date != curr_date:
            note = f" (requested date {curr_date} is in the future; using {effective_date})"
        return (
            f"No insider transactions data available for {ticker} as of {effective_date}{note}. "
            f"Reason: {type(e).__name__}: {e}"
        )

@tool
async def get_news_sentiment(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Get pre-computed Finnhub sentiment data for a ticker.
    Returns structured numerical sentiment containing bullish/bearish percentage and buzz score.
    """
    return await asyncio.to_thread(route_to_vendor, "get_news_sentiment", ticker)

@tool
async def get_recent_sec_filings(
    ticker: Annotated[str, "Ticker symbol"] = "",
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = "",
) -> str:
    """
    Retrieve recent SEC filings (e.g. 8-K, Form 4) for a specific ticker (or globally if ticker is empty).
    Provides primary source structural records of material events, earnings, Insider trades, etc.
    """
    return await asyncio.to_thread(route_to_vendor, "get_recent_sec_filings", ticker, curr_date)
