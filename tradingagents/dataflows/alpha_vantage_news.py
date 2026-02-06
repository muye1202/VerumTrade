import json

from .alpha_vantage_common import _make_api_request, format_datetime_for_api
from .config import get_config


def _compact_news_payload(raw: str, ticker: str, start_date: str, end_date: str) -> str:
    cfg = get_config()
    max_items = int(cfg.get("news_max_items", 12))

    try:
        payload = json.loads(raw)
    except Exception:
        return raw

    feed = payload.get("feed")
    if not isinstance(feed, list):
        return raw

    lines = []
    lines.append(f"## {ticker.upper()} Alpha Vantage News, from {start_date} to {end_date}:")
    lines.append("")

    for idx, item in enumerate(feed[:max_items], start=1):
        title = str(item.get("title") or "Untitled").strip()
        source = str(item.get("source") or "unknown").strip()
        time_published = str(item.get("time_published") or "").strip()
        summary = str(item.get("summary") or "").strip()
        overall_label = str(item.get("overall_sentiment_label") or "").strip()
        overall_score = item.get("overall_sentiment_score")
        url = str(item.get("url") or "").strip()

        if len(summary) > 360:
            summary = summary[:357] + "..."

        header_parts = [f"{idx}. {title}"]
        meta_parts = []
        if source:
            meta_parts.append(f"source: {source}")
        if time_published:
            meta_parts.append(f"published: {time_published}")
        if overall_label:
            if overall_score is not None:
                meta_parts.append(f"sentiment: {overall_label} ({overall_score})")
            else:
                meta_parts.append(f"sentiment: {overall_label}")
        if meta_parts:
            header_parts.append("(" + " | ".join(meta_parts) + ")")

        lines.append("### " + " ".join(header_parts))
        if summary:
            lines.append(summary)
        if url:
            lines.append(url)
        lines.append("")

    if len(feed) > max_items:
        lines.append(f"[Truncated to top {max_items} items out of {len(feed)} returned articles]")

    return "\n".join(lines)


def get_news(ticker, start_date, end_date) -> dict[str, str] | str:
    """Returns compact live/historical news sentiment for a ticker from Alpha Vantage."""

    params = {
        "tickers": ticker,
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(end_date),
        "sort": "LATEST",
        "limit": "50",
    }

    raw = _make_api_request("NEWS_SENTIMENT", params)
    return _compact_news_payload(str(raw), ticker, start_date, end_date)


def get_insider_transactions(symbol: str) -> dict[str, str] | str:
    """Returns latest and historical insider transactions by key stakeholders."""

    params = {
        "symbol": symbol,
    }

    return _make_api_request("INSIDER_TRANSACTIONS", params)
