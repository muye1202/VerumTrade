from typing import Annotated
from datetime import datetime
from dateutil.relativedelta import relativedelta
from .googlenews_utils import getNewsData


def get_google_news(
    query: Annotated[str, "Query to search with"],
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    query = query.replace(" ", "+")

    start_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before = start_date - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    news_results = getNewsData(query, before, curr_date)

    news_str = ""

    for news in news_results:
        news_str += (
            f"### {news['title']} (source: {news['source']}) \n\n{news['snippet']}\n\n"
        )

    if len(news_results) == 0:
        return ""

    return f"## {query} Google News, from {before} to {curr_date}:\n\n{news_str}"


def get_google_global_news(
    curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """Fetch global/macro news via Google News search scraping.

    Note: This is scraping-based (no API key), so it can be rate-limited or blocked.
    """

    start_date = datetime.strptime(curr_date, "%Y-%m-%d")
    before_dt = start_date - relativedelta(days=look_back_days)
    before = before_dt.strftime("%Y-%m-%d")

    # Keep the query simple and broad; Google News will rank by relevance/recency.
    query = "global+markets+macroeconomics+economy"
    news_results = getNewsData(query, before, curr_date)

    if not news_results:
        return ""

    news_str = ""
    for news in news_results[:limit]:
        source = news.get("source", "unknown")
        title = news.get("title", "").strip()
        snippet = news.get("snippet", "").strip()
        link = news.get("link", "").strip()
        date_str = news.get("date", "").strip()

        suffix = []
        if date_str:
            suffix.append(date_str)
        if source:
            suffix.append(f"source: {source}")
        suffix_text = f" ({' | '.join(suffix)})" if suffix else ""

        # Keep link in output so agents can cite/inspect if desired.
        link_text = f"\n{link}" if link else ""
        news_str += f"### {title}{suffix_text}\n\n{snippet}{link_text}\n\n"

    return f"## Global/Macro Google News, from {before} to {curr_date}:\n\n{news_str}"
