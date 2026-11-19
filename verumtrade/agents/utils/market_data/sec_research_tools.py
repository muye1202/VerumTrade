"""Analyst tools for reading SEC filings as inspectable, source-linked evidence.

These tools are the follow-up path for questions the Stage 0 research loop opens:
list the issuer's filings, read the passages that bear on a specific question,
and pull standardized financial facts with explicit periods and units.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from langchain_core.tools import tool

from verumtrade.research.sec_client import get_sec_client
from verumtrade.research.sec_research import (
    financial_facts_packet,
    list_filings_packet,
    read_filing_packet,
)
from verumtrade.research.source_store import SourceStore
from verumtrade.research.xbrl import DEFAULT_METRICS, METRIC_CONCEPTS

_store: SourceStore | None = None


def _source_store() -> SourceStore:
    global _store
    if _store is None:
        _store = SourceStore()
    return _store


def _csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _dump(packet: dict) -> str:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))


def _failure(bundle: str, ticker: str, curr_date: str, exc: Exception) -> str:
    return _dump(
        {
            "bundle": bundle,
            "symbol": ticker,
            "date": curr_date,
            "facts": [],
            "missing_data": [{"section": bundle, "issue": f"unavailable: {type(exc).__name__}: {exc}"}],
        }
    )


@tool
async def list_sec_filings(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "As-of date in yyyy-mm-dd format; later filings are excluded"],
    form_types: Annotated[str, "Comma-separated SEC form types, e.g. 10-Q,10-K,8-K"] = "10-K,10-Q,8-K",
    limit: Annotated[int, "Maximum filings to list"] = 8,
) -> str:
    """List the issuer's own SEC filings accepted on or before curr_date.

    The issuer is resolved by SEC CIK, so filings from other companies that merely
    mention the ticker are never returned. Each filing includes its accession number,
    which read_sec_filing needs.
    """
    try:
        packet = await asyncio.to_thread(
            list_filings_packet,
            ticker,
            curr_date,
            client=get_sec_client(),
            form_types=_csv(form_types),
            limit=int(limit),
        )
        return _dump(packet)
    except Exception as exc:
        return _failure("SecFilingList", ticker, curr_date, exc)


@tool
async def read_sec_filing(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "As-of date in yyyy-mm-dd format"],
    accession_number: Annotated[str, "Accession number from list_sec_filings, e.g. 0000723125-26-000012"],
    question: Annotated[str, "The specific question the passages should answer"],
    section: Annotated[str, "Optional section heading filter, e.g. 'Item 7' or 'Item 1A'"] = "",
    document: Annotated[str, "Optional file name within the filing; defaults to the primary document (plus EX-99 exhibits for 8-Ks)"] = "",
) -> str:
    """Read the passages of one of the issuer's filings that bear on a specific question.

    Returns exact passages with character spans into the stored filing text, so every
    fact can be re-checked against the source. Relevance ranking is lexical: cite a
    passage only for what it actually states.
    """
    try:
        packet = await asyncio.to_thread(
            read_filing_packet,
            ticker,
            curr_date,
            accession_number,
            question,
            client=get_sec_client(),
            store=_source_store(),
            section=section,
            document=document,
        )
        return _dump(packet)
    except Exception as exc:
        return _failure("SecFilingPassages", ticker, curr_date, exc)


@tool
async def get_sec_financial_facts(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "As-of date in yyyy-mm-dd format; facts filed later are excluded"],
    metrics: Annotated[
        str,
        "Comma-separated metrics: " + ", ".join(METRIC_CONCEPTS),
    ] = ",".join(DEFAULT_METRICS),
) -> str:
    """Get standardized financial facts from the issuer's XBRL filings as of curr_date.

    Each fact states its unit, exact period, whether it is a quarter, year-to-date,
    annual or point-in-time value, and the filing that reported it. Restated values
    keep the originally reported figure. Segment and custom metrics are not covered.
    """
    try:
        packet = await asyncio.to_thread(
            financial_facts_packet,
            ticker,
            curr_date,
            client=get_sec_client(),
            metrics=_csv(metrics) or None,
        )
        return _dump(packet)
    except Exception as exc:
        return _failure("SecXbrlFacts", ticker, curr_date, exc)


SEC_RESEARCH_TOOLS = [list_sec_filings, read_sec_filing, get_sec_financial_facts]
