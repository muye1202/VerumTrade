"""
Finnhub news vendor — drop-in replacement for Google News scraping.

Free tier: 60 calls/min, no credit card required.
Get API key: https://finnhub.io/register
"""

import finnhub
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

def _client() -> finnhub.Client:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        raise RuntimeError(
            "FINNHUB_API_KEY not set. Get a free key at https://finnhub.io/register"
        )
    return finnhub.Client(api_key=key)


def get_company_news_finnhub(
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int = 20,
) -> str:
    """
    Fetch company-specific news from Finnhub.
    Drop-in compatible with route_to_vendor("get_news", ...) interface.

    Returns formatted string matching existing vendor output format.
    """
    try:
        client = _client()
        articles = client.company_news(ticker, _from=start_date, to=end_date)
    except Exception as e:
        return f"Error fetching {ticker} news from Finnhub: {str(e)}"

    if not articles:
        return ""

    lines = [f"## {ticker} News ({start_date} to {end_date}):\n"]
    for article in articles[:limit]:
        headline = article.get("headline", "").strip()
        summary = article.get("summary", "").strip()
        source = article.get("source", "unknown")
        dt = datetime.fromtimestamp(article.get("datetime", 0))
        date_str = dt.strftime("%Y-%m-%d %H:%M")
        category = article.get("category", "")
        related = ", ".join(article.get("related", "").split(",")[:3])

        lines.append(f"### {headline}")
        lines.append(f"*{source} | {date_str} | {category}*")
        if related and related != ticker:
            lines.append(f"Related tickers: {related}")
        lines.append(f"{summary}\n")

    return "\n".join(lines)


def get_global_news_finnhub(
    curr_date: str = "",
    look_back_days: int = 7,
    limit: int = 15,
    category: str = "general",
) -> str:
    """
    Fetch general market news from Finnhub.
    Replaces the Google News scraping in get_google_global_news().
    """
    try:
        client = _client()
        articles = client.general_news(category)
    except Exception as e:
        return f"Error fetching global news from Finnhub: {str(e)}"

    if not articles:
        return ""

    lines = ["## Global Market News:\n"]
    for article in articles[:limit]:
        headline = article.get("headline", "").strip()
        summary = article.get("summary", "").strip()
        source = article.get("source", "unknown")
        dt = datetime.fromtimestamp(article.get("datetime", 0))
        date_str = dt.strftime("%Y-%m-%d %H:%M")
        url = article.get("url", "")

        lines.append(f"### {headline}")
        lines.append(f"*{source} | {date_str}*")
        lines.append(f"{summary}")
        if url:
            lines.append(f"{url}")
        lines.append("")

    return "\n".join(lines)


def get_news_sentiment_finnhub(ticker: str) -> Dict[str, Any]:
    """
    Get pre-computed sentiment for a ticker.

    Returns structured data — NO LLM needed to score sentiment.
    The news_analyst and social_media_analyst can consume this directly.
    """
    try:
        client = _client()
        data = client.news_sentiment(ticker)
    except Exception:
        return {
            "ticker": ticker,
            "error": "Failed to fetch sentiment data"
        }

    return {
        "ticker": ticker,
        "company_news_score": data.get("companyNewsScore", 0.0),
        "sector_avg_score": data.get("sectorAverageNewsScore", 0.0),
        "bullish_pct": data.get("sentiment", {}).get("bullishPercent", 0.0),
        "bearish_pct": data.get("sentiment", {}).get("bearishPercent", 0.0),
        "buzz": data.get("buzz", {}).get("buzz", 0.0),
        "articles_this_week": data.get("buzz", {}).get("articlesInLastWeek", 0),
        "weekly_average": data.get("buzz", {}).get("weeklyAverage", 0.0),
        # Derived: is this ticker getting unusual attention?
        "attention_spike": (
            data.get("buzz", {}).get("buzz", 0.0) > 1.5
        ),
        # Derived: strong bullish consensus
        "strong_bullish": (
            data.get("sentiment", {}).get("bullishPercent", 0.0) > 0.7
            and data.get("companyNewsScore", 0.0) > 0.6
        ),
    }


def get_earnings_calendar_finnhub(
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """
    Structured earnings calendar — supplements yfinance.
    Returns list of {symbol, date, epsEstimate, epsActual, ...}
    """
    try:
        client = _client()
        data = client.earnings_calendar(
            _from=start_date,
            to=end_date,
            symbol="",  # all symbols
        )
        return data.get("earningsCalendar", [])
    except Exception:
        return []
