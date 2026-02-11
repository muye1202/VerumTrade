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
    """
    Returns compact live/historical news sentiment for a ticker from Alpha Vantage.

    NOTE: This returns TEXT-BASED SENTIMENT from news articles.
    The sentiment scores/labels are derived from NLP analysis of article headlines
    and content (e.g., "Bullish", "Bearish", "Neutral" with confidence scores).

    This is different from get_insider_sentiment() which measures ACTION-BASED
    sentiment from actual insider trading behavior (buying/selling).
    """

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


def get_insider_sentiment(ticker: str, curr_date: str) -> str:
    """
    Derive insider sentiment metrics from Alpha Vantage insider transactions.

    IMPORTANT: This is ACTION-BASED sentiment, NOT text-based sentiment.
    "Insider sentiment" refers to the confidence/outlook implied by insider trading
    behavior (executives and directors buying or selling their own company's stock).

    The sentiment is derived from:
    - Net share changes (total buys - total sells)
    - Share purchase ratio (% of transactions that are purchases)
    - Dollar values of purchases vs sales

    Interpretation:
    - Net positive (more buying) → Bullish insider sentiment (insiders are confident)
    - Net negative (more selling) → Bearish insider sentiment (insiders are cautious)

    This is fundamentally different from get_news() which provides TEXT-BASED
    sentiment from NLP analysis of news articles.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date in yyyy-mm-dd format (not used by Alpha Vantage API)

    Returns:
        str: A formatted report of insider sentiment derived from transaction data
    """
    try:
        # Get raw insider transactions data
        raw_data = get_insider_transactions(ticker)

        # Parse JSON response
        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except (json.JSONDecodeError, TypeError):
            return f"No insider sentiment data available for {ticker}."

        # Check if data is valid
        if not isinstance(data, dict) or "data" not in data:
            return f"No insider sentiment data available for {ticker}."

        transactions = data.get("data", [])
        if not transactions or len(transactions) == 0:
            return f"No insider sentiment data available for {ticker}."

        # Aggregate transaction data to derive sentiment metrics
        net_shares = 0
        total_purchases = 0
        total_sales = 0
        purchase_value = 0
        sale_value = 0

        for txn in transactions:
            shares = txn.get("acquisition_or_disposition_shares", 0)
            price = txn.get("share_price", 0)
            code = str(txn.get("acquisition_or_disposition_code", "")).upper()
            
            try:
                shares = float(shares) if shares else 0
                price = float(price) if price else 0
            except (ValueError, TypeError):
                continue

            # A = Acquisition (purchase), D = Disposition (sale)
            if code == "A":
                net_shares += shares
                total_purchases += shares
                purchase_value += shares * price
            elif code == "D":
                net_shares -= shares
                total_sales += shares
                sale_value += shares * price

        # Calculate metrics
        total_transactions = total_purchases + total_sales
        if total_transactions > 0:
            mspr = total_purchases / total_transactions  # Monthly Share Purchase Ratio
        else:
            mspr = 0.0

        # Format the report
        lines = []
        lines.append(f"## {ticker} Insider Sentiment Data (derived from Alpha Vantage):")
        lines.append("")
        lines.append(f"**Net Share Change**: {net_shares:,.0f} shares")
        lines.append(f"**Share Purchase Ratio**: {mspr:.2%}")
        lines.append("")
        lines.append("### Summary:")
        lines.append(f"- Total Purchases: {total_purchases:,.0f} shares (${purchase_value:,.2f})")
        lines.append(f"- Total Sales: {total_sales:,.0f} shares (${sale_value:,.2f})")
        lines.append(f"- Net Position Change: {net_shares:,.0f} shares")
        lines.append("")
        lines.append("**Interpretation**: The net share change reflects aggregate insider buying/selling activity. ")
        lines.append("A positive value indicates net buying (bullish sentiment), while negative indicates net selling (bearish sentiment). ")
        lines.append("The share purchase ratio shows the proportion of purchases vs total transactions.")
        
        return "\n".join(lines)

    except Exception as e:
        return f"No insider sentiment data available for {ticker}. Reason: {type(e).__name__}: {e}"

