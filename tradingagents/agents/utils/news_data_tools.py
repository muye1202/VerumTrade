from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor
from datetime import datetime, timedelta

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)


@tool
def get_company_news_window(
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
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
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
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_sentiment(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve insider sentiment information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A report of insider sentiment data
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
        return route_to_vendor("get_insider_sentiment", ticker, effective_date)
    except Exception as e:
        note = ""
        if effective_date != curr_date:
            note = f" (requested date {curr_date} is in the future; using {effective_date})"
        return (
            f"No insider sentiment data available for {ticker} as of {effective_date}{note}. "
            f"Reason: {type(e).__name__}: {e}"
        )

@tool
def get_insider_transactions(
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
        return route_to_vendor("get_insider_transactions", ticker, effective_date)
    except Exception as e:
        note = ""
        if effective_date != curr_date:
            note = f" (requested date {curr_date} is in the future; using {effective_date})"
        return (
            f"No insider transactions data available for {ticker} as of {effective_date}{note}. "
            f"Reason: {type(e).__name__}: {e}"
        )
