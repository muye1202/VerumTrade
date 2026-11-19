"""Issuer-resolved, point-in-time access to SEC EDGAR.

Unlike EDGAR full-text search (which matches any filing that mentions a ticker
string), everything here is keyed by the issuer's CIK:

- ``resolve_cik``: ticker -> CIK via SEC's ``company_tickers.json`` (cached daily).
- ``list_filings``: the issuer's own filings from the submissions API, limited to
  filings accepted on or before the as-of date.
- ``fetch_document_text``: the filing's actual primary document or exhibit, as text.
- ``company_facts``: standardized XBRL facts with units, periods, and filing dates.

No API key is needed; SEC asks for a descriptive User-Agent with a contact address
(set ``VERUMTRADE_SEC_USER_AGENT``) and at most 10 requests per second.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable, TypedDict

DEFAULT_USER_AGENT = "verumtrade/1.0 (admin@verumtrade.com)"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{filename}"
TICKER_MAP_MAX_AGE_SECONDS = 24 * 3600


class FilingRecord(TypedDict, total=False):
    accession_number: str
    cik: str
    ticker: str
    company_name: str
    form_type: str
    filing_date: str
    accepted_at: str
    report_date: str
    primary_document: str
    primary_document_url: str
    items: str
    description: str
    is_xbrl: bool


class SecClientError(RuntimeError):
    pass


def normalize_cik(cik: Any) -> str:
    digits = re.sub(r"\D", "", str(cik or ""))
    return digits.zfill(10) if digits else ""


def archive_url(cik: str, accession: str, filename: str) -> str:
    return ARCHIVE_URL.format(
        cik_int=int(normalize_cik(cik) or 0),
        accession_nodash=str(accession).replace("-", ""),
        filename=filename,
    )


def _as_of_date(as_of: str | None) -> str | None:
    if not as_of:
        return None
    try:
        return datetime.strptime(str(as_of)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def filing_available_on(filing: FilingRecord, as_of: str | None) -> bool:
    """True when the filing was accepted (or, lacking that, filed) on or before ``as_of``."""
    cutoff = _as_of_date(as_of)
    if cutoff is None:
        return True
    stamp = str(filing.get("accepted_at") or filing.get("filing_date") or "")[:10]
    return bool(stamp) and stamp <= cutoff


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
    SKIP_TAGS = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    if not re.search(r"<(html|body|div|p|table|font|span)\b", html or "", re.I):
        return re.sub(r"[ \t]+", " ", unescape(html or "")).strip()
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = []
    for line in "".join(parser.parts).replace("\xa0", " ").splitlines():
        compact = re.sub(r"\s+", " ", line).strip(" |")
        if compact:
            lines.append(compact)
    return "\n".join(lines)


class SecClient:
    _rate_lock = threading.Lock()
    _last_request = 0.0

    def __init__(
        self,
        session: Any = None,
        *,
        user_agent: str | None = None,
        cache_dir: str | None = None,
        min_interval_seconds: float = 0.12,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.user_agent = user_agent or os.getenv("VERUMTRADE_SEC_USER_AGENT") or DEFAULT_USER_AGENT
        self.cache_dir = cache_dir
        self.min_interval_seconds = min_interval_seconds
        self._submissions: dict[str, dict[str, Any]] = {}
        self._facts: dict[str, dict[str, Any]] = {}
        self._ticker_map: dict[str, dict[str, str]] | None = None

    # --- HTTP -------------------------------------------------------------
    def _get(self, url: str, *, accept: str = "application/json"):
        with self._rate_lock:
            wait = self.min_interval_seconds - (time.monotonic() - SecClient._last_request)
            if wait > 0:
                time.sleep(wait)
            SecClient._last_request = time.monotonic()
        response = self.session.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept": accept, "Accept-Encoding": "gzip, deflate"},
            timeout=20,
        )
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            raise SecClientError(f"SEC request failed ({status}): {url}")
        return response

    def _get_json(self, url: str) -> dict[str, Any]:
        return self._get(url).json()

    # --- Identity ---------------------------------------------------------
    def _ticker_map_cache_path(self) -> str | None:
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, "sec_company_tickers.json")

    def ticker_map(self) -> dict[str, dict[str, str]]:
        if self._ticker_map is not None:
            return self._ticker_map
        raw: dict[str, Any] | None = None
        path = self._ticker_map_cache_path()
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < TICKER_MAP_MAX_AGE_SECONDS:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        if raw is None:
            raw = self._get_json(TICKER_MAP_URL)
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(raw, handle)
        mapping: dict[str, dict[str, str]] = {}
        for row in (raw or {}).values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                mapping[ticker] = {"cik": normalize_cik(row.get("cik_str")), "name": str(row.get("title") or "")}
        self._ticker_map = mapping
        return mapping

    def resolve_cik(self, ticker: str) -> dict[str, str] | None:
        """Return ``{"cik", "name", "ticker"}`` for a ticker, or None when SEC has no mapping."""
        symbol = str(ticker or "").upper().strip()
        candidates = [symbol, symbol.replace(".", "-"), symbol.replace("-", ".")]
        mapping = self.ticker_map()
        for candidate in candidates:
            if candidate in mapping:
                return {**mapping[candidate], "ticker": candidate}
        return None

    def ticker_for_cik(self, cik: str) -> str:
        target = normalize_cik(cik)
        for ticker, row in self.ticker_map().items():
            if row.get("cik") == target:
                return ticker
        return ""

    # --- Filings ----------------------------------------------------------
    def submissions(self, cik: str) -> dict[str, Any]:
        key = normalize_cik(cik)
        if key not in self._submissions:
            self._submissions[key] = self._get_json(SUBMISSIONS_URL.format(cik=key))
        return self._submissions[key]

    def _filing_rows(self, cik: str, max_pages: int = 2) -> Iterable[dict[str, Any]]:
        data = self.submissions(cik)
        pages = [data.get("filings", {}).get("recent", {})]
        for extra in (data.get("filings", {}).get("files") or [])[:max_pages]:
            name = extra.get("name") if isinstance(extra, dict) else None
            if name:
                try:
                    pages.append(self._get_json(SUBMISSIONS_PAGE_URL.format(name=name)))
                except Exception:
                    break
        for page in pages:
            columns = {key: value for key, value in (page or {}).items() if isinstance(value, list)}
            count = len(columns.get("accessionNumber", []))
            for idx in range(count):
                yield {key: (values[idx] if idx < len(values) else None) for key, values in columns.items()}

    def list_filings(
        self,
        ticker: str,
        *,
        as_of: str | None = None,
        form_types: Iterable[str] | None = None,
        limit: int = 10,
    ) -> list[FilingRecord]:
        identity = self.resolve_cik(ticker)
        if not identity:
            raise SecClientError(f"No SEC CIK mapping for ticker '{ticker}'")
        cik = identity["cik"]
        data = self.submissions(cik)
        wanted = {str(form).strip().upper() for form in (form_types or []) if str(form).strip()}
        results: list[FilingRecord] = []
        pages_needed = 0 if _as_of_date(as_of) is None else 2
        for row in self._filing_rows(cik, max_pages=pages_needed):
            form = str(row.get("form") or "").upper()
            if wanted and form not in wanted:
                continue
            accession = str(row.get("accessionNumber") or "")
            primary = str(row.get("primaryDocument") or "")
            record: FilingRecord = {
                "accession_number": accession,
                "cik": cik,
                "ticker": identity["ticker"],
                "company_name": str(data.get("name") or identity.get("name") or ""),
                "form_type": form,
                "filing_date": str(row.get("filingDate") or ""),
                "accepted_at": str(row.get("acceptanceDateTime") or ""),
                "report_date": str(row.get("reportDate") or ""),
                "primary_document": primary,
                "primary_document_url": archive_url(cik, accession, primary) if primary else "",
                "items": str(row.get("items") or ""),
                "description": str(row.get("primaryDocDescription") or ""),
                "is_xbrl": bool(row.get("isXBRL")),
            }
            if not filing_available_on(record, as_of):
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def filing_exhibits(self, filing: FilingRecord) -> list[str]:
        """Best-effort list of exhibit filenames (e.g. EX-99.1 press releases) in a filing."""
        try:
            index = self._get_json(archive_url(filing["cik"], filing["accession_number"], "index.json"))
        except Exception:
            return []
        names = []
        for item in (index.get("directory") or {}).get("item") or []:
            name = str(item.get("name") or "")
            if re.search(r"ex-?99|ex99", name, re.I) and name.lower().endswith((".htm", ".html", ".txt")):
                names.append(name)
        return names

    def fetch_document_text(self, filing: FilingRecord, filename: str | None = None) -> tuple[str, str]:
        """Return ``(url, text)`` for the filing's primary document or a named file in it."""
        name = filename or filing.get("primary_document") or ""
        if not name:
            raise SecClientError(f"Filing {filing.get('accession_number')} has no primary document")
        url = archive_url(filing["cik"], filing["accession_number"], name)
        response = self._get(url, accept="text/html,application/xhtml+xml,text/plain")
        return url, html_to_text(getattr(response, "text", "") or "")

    # --- XBRL -------------------------------------------------------------
    def company_facts(self, cik: str) -> dict[str, Any]:
        key = normalize_cik(cik)
        if key not in self._facts:
            self._facts[key] = self._get_json(COMPANY_FACTS_URL.format(cik=key))
        return self._facts[key]


_default_client: SecClient | None = None


def get_sec_client() -> SecClient:
    global _default_client
    if _default_client is None:
        from verumtrade.dataflows.config import get_config

        _default_client = SecClient(cache_dir=str(get_config().get("data_cache_dir") or "") or None)
    return _default_client
