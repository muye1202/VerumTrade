from __future__ import annotations
"""
Theme Engine — Evidence Collector

Fetches fresh public evidence for ticker-theme pairs from free/open-source
sources. Results are cached by date so repeated calls within a trading day
cost nothing.

Sources (in preference order, all free-tier):
  1. Google News RSS via feedparser
  2. Yahoo Finance news via yfinance
  3. Alpha Vantage NEWS_SENTIMENT (only when ALPHAVANTAGE_API_KEY env var set)
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import EvidenceItem


class ThemeEvidenceCollector:
    """
    Collects fresh public evidence for ticker-theme pairs.

    Sources (in order of preference, all free-tier):
      1. Google News RSS via feedparser
      2. Yahoo Finance news via yfinance
      3. Alpha Vantage NEWS_SENTIMENT (only if ALPHAVANTAGE_API_KEY env var set)

    Results are cached locally by date so repeated calls within a trading
    day are free (no network calls).
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        default_cache = Path(__file__).parents[4] / "data" / "themes" / ".evidence_cache"
        self._cache_dir = Path(
            (self.config.get("theme_engine") or {}).get("evidence_cache_dir", default_cache)
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._request_delay = float(
            (self.config.get("theme_engine") or {}).get("evidence_request_delay_secs", 0.5)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect_for_ticker(
        self,
        ticker: str,
        theme_label: str,
        theme_id: str,
        trade_date: str,
        keywords: Optional[List[str]] = None,
    ) -> List[EvidenceItem]:
        """Fetch evidence for one ticker-theme pair. Uses cache when available."""
        cache_path = self._cache_dir / trade_date / f"{ticker}_{theme_id}.json"
        cached = self._load_cache(cache_path, trade_date)
        if cached is not None:
            return cached

        kws = list(keywords or []) + theme_label.split() + [ticker]
        items: List[EvidenceItem] = []

        items.extend(self._fetch_google_rss(ticker, theme_label, theme_id, kws))
        items.extend(self._fetch_yfinance_news(ticker, theme_id, kws))
        items.extend(self._fetch_alpha_vantage(ticker, theme_id, kws))

        self._save_cache(cache_path, items)
        return items

    def collect_for_theme(
        self,
        chain,  # ThemeChain
        trade_date: str,
    ) -> Dict[str, List[EvidenceItem]]:
        """Collect evidence for all seed tickers in a theme. Returns {ticker: [EvidenceItem]}."""
        result: Dict[str, List[EvidenceItem]] = {}
        for ticker in chain.all_tickers:
            result[ticker] = self.collect_for_ticker(
                ticker=ticker,
                theme_label=chain.theme_label,
                theme_id=chain.theme_id,
                trade_date=trade_date,
            )
        return result

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self, path: Path, trade_date: str) -> Optional[List[EvidenceItem]]:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("date") != trade_date:
                return None
            return [EvidenceItem(**item) for item in raw["items"]]
        except Exception as exc:
            self.logger.debug("Cache read failed for %s: %s", path, exc)
            return None

    def _save_cache(self, path: Path, items: List[EvidenceItem]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": path.parent.name,
                "items": [
                    {
                        "ticker": i.ticker,
                        "theme_id": i.theme_id,
                        "source": i.source,
                        "headline": i.headline,
                        "url": i.url,
                        "date": i.date,
                        "relevance_score": i.relevance_score,
                        "snippet": i.snippet,
                    }
                    for i in items
                ],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            self.logger.debug("Cache write failed for %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Source: Google News RSS
    # ------------------------------------------------------------------

    def _fetch_google_rss(
        self,
        ticker: str,
        theme_label: str,
        theme_id: str,
        keywords: List[str],
    ) -> List[EvidenceItem]:
        try:
            import feedparser
            import urllib.parse

            query = f"{ticker} {theme_label}"
            url = (
                "https://news.google.com/rss/search?q="
                + urllib.parse.quote(query)
                + "&hl=en-US&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(url)
            items: List[EvidenceItem] = []
            for entry in (feed.entries or [])[:10]:
                headline = str(getattr(entry, "title", "") or "")
                link = str(getattr(entry, "link", "") or "")
                pub = str(getattr(entry, "published", "") or "")
                date_str = _parse_date(pub)
                score = _keyword_score(headline, keywords)
                items.append(
                    EvidenceItem(
                        ticker=ticker,
                        theme_id=theme_id,
                        source="rss",
                        headline=headline,
                        url=link,
                        date=date_str,
                        relevance_score=score,
                    )
                )
            time.sleep(self._request_delay)
            return items
        except Exception as exc:
            self.logger.warning("Google RSS fetch failed for %s: %s", ticker, exc)
            return []

    # ------------------------------------------------------------------
    # Source: Yahoo Finance news
    # ------------------------------------------------------------------

    def _fetch_yfinance_news(
        self,
        ticker: str,
        theme_id: str,
        keywords: List[str],
    ) -> List[EvidenceItem]:
        try:
            import yfinance as yf

            ticker_obj = yf.Ticker(ticker)
            news = ticker_obj.news or []
            items: List[EvidenceItem] = []
            for item in news[:10]:
                headline = str(item.get("title", "") or "")
                link = str(item.get("link", "") or "")
                ts = item.get("providerPublishTime")
                date_str = _unix_to_date(ts) if ts else ""
                score = _keyword_score(headline, keywords)
                items.append(
                    EvidenceItem(
                        ticker=ticker,
                        theme_id=theme_id,
                        source="yfinance_news",
                        headline=headline,
                        url=link,
                        date=date_str,
                        relevance_score=score,
                    )
                )
            return items
        except Exception as exc:
            self.logger.warning("yfinance news fetch failed for %s: %s", ticker, exc)
            return []

    # ------------------------------------------------------------------
    # Source: Alpha Vantage (optional)
    # ------------------------------------------------------------------

    def _fetch_alpha_vantage(
        self,
        ticker: str,
        theme_id: str,
        keywords: List[str],
    ) -> List[EvidenceItem]:
        key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if not key:
            return []
        try:
            import requests

            url = (
                f"https://www.alphavantage.co/query"
                f"?function=NEWS_SENTIMENT&tickers={ticker}&apikey={key}&limit=10"
            )
            resp = requests.get(url, timeout=10)
            data = resp.json()
            items: List[EvidenceItem] = []
            for item in (data.get("feed") or []):
                headline = str(item.get("title", "") or "")
                link = str(item.get("url", "") or "")
                pub = str(item.get("time_published", "") or "")
                date_str = _av_date(pub)
                snippet = str(item.get("summary", "") or "")[:300]
                score = _keyword_score(headline + " " + snippet, keywords)
                items.append(
                    EvidenceItem(
                        ticker=ticker,
                        theme_id=theme_id,
                        source="alpha_vantage",
                        headline=headline,
                        url=link,
                        date=date_str,
                        relevance_score=score,
                        snippet=snippet,
                    )
                )
            time.sleep(self._request_delay)
            return items
        except Exception as exc:
            self.logger.warning("Alpha Vantage fetch failed for %s: %s", ticker, exc)
            return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keyword_score(text: str, keywords: List[str]) -> float:
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    return min(1.0, hits / max(1, len(keywords)))


def _parse_date(raw: str) -> str:
    """Parse an RFC-2822 or ISO datetime string to YYYY-MM-DD."""
    if not raw:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw[:10]).date().isoformat()
    except Exception:
        return ""


def _unix_to_date(ts) -> str:
    try:
        return datetime.utcfromtimestamp(int(ts)).date().isoformat()
    except Exception:
        return ""


def _av_date(raw: str) -> str:
    """Parse Alpha Vantage's YYYYMMDDTHHmmSS format."""
    if not raw or len(raw) < 8:
        return ""
    try:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    except Exception:
        return ""
