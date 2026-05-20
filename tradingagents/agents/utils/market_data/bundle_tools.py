from __future__ import annotations

import asyncio
import json
import re
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
    get_recent_sec_filings,
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
from tradingagents.dataflows.vendors.finnhub.finnhub_vendor import get_earnings_calendar_finnhub

_COMMON_FALSE_TICKERS = {
    "AI",
    "API",
    "CEO",
    "CFO",
    "CIO",
    "COO",
    "CTO",
    "EPS",
    "ETF",
    "FBI",
    "FDA",
    "FOMC",
    "GDP",
    "IPO",
    "IRS",
    "LLC",
    "M&A",
    "NASDAQ",
    "NYSE",
    "PC",
    "R&D",
    "RAN",
    "SEC",
    "USA",
    "USD",
}


async def _run_tool(tool_obj: Any, payload: Dict[str, Any]) -> str:
    try:
        return str(await tool_obj.ainvoke(payload))
    except Exception as e:
        return f"ToolError[{getattr(tool_obj, 'name', 'unknown')}]: {type(e).__name__}: {e}"


async def _run_sync_callable(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        return f"ToolError[{name}]: {type(e).__name__}: {e}"


def select_bundle_first_tools(
    bundle_tool: Any,
    fallback_tools: list[Any],
    *,
    enable_bundle_tools: bool,
    rounds_used: int,
) -> list[Any]:
    """Expose only the bundle on the first analyst round, then fall back if needed."""
    if not enable_bundle_tools:
        return list(fallback_tools)
    if int(rounds_used or 0) <= 0:
        return [bundle_tool]
    return list(fallback_tools)


def _score_bundle_line(line: str) -> int:
    lower = line.lower()
    score = 0
    if re.search(r"\$?\d+(?:\.\d+)?%?", line):
        score += 2
    for term in (
        "last close",
        "returns",
        "atr",
        "volume",
        "support",
        "resistance",
        "trigger",
        "risk",
        "sentiment",
        "earnings",
        "revenue",
        "margin",
        "cash",
        "debt",
        "insider",
        "short",
        "vwap",
        "options",
        "valuation",
        "price",
    ):
        if term in lower:
            score += 1
    if line.lstrip().startswith(("-", "*", "|")):
        score += 1
    return score


def _bundle_domain(bundle_name: str) -> str:
    lower = str(bundle_name or "").lower()
    if "fundamental" in lower:
        return "fundamentals"
    if "news" in lower:
        return "news"
    if "sentiment" in lower:
        return "sentiment"
    return "market"


def _clean_fact_part(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def _first_json_object(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _compact_source_quality(results: Dict[str, Any]) -> dict[str, Any]:
    quality: dict[str, Any] = {}
    for section, raw in results.items():
        text = str(raw or "")
        missing = _missing_summary(section, text)
        quality[section] = {
            "status": "missing" if missing else "ok",
            "chars": len(text),
            "issue": missing or "",
        }
    return quality


def _source_quality_from_event_diagnostics(
    results: Dict[str, Any],
    diagnostics: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    source_quality = _compact_source_quality(results)
    accepted_total = 0
    quarantined_total = 0
    dropped_total = 0
    max_contamination = 0.0

    for section, raw in results.items():
        diag = diagnostics.get(section, {})
        accepted_count = len(diag.get("accepted_events") or [])
        quarantined_count = len(diag.get("quarantined_events") or [])
        dropped_count = len(diag.get("dropped_events") or [])
        candidate_count = accepted_count + quarantined_count + dropped_count
        contamination_score = (
            round((quarantined_count + dropped_count) / candidate_count, 2)
            if candidate_count
            else 0.0
        )
        max_contamination = max(max_contamination, contamination_score)
        accepted_total += accepted_count
        quarantined_total += quarantined_count
        dropped_total += dropped_count

        quality = dict(source_quality.get(section) or {})
        if quality.get("status") != "missing":
            if candidate_count == 0:
                quality["status"] = "sparse"
                quality["issue"] = quality.get("issue") or "No usable target-relevant catalyst events extracted."
            elif contamination_score >= 0.50:
                quality["status"] = "contaminated"
                quality["issue"] = "Multiple non-target or ambiguous catalyst lines dominated extracted candidates."
            elif contamination_score > 0:
                quality["status"] = "degraded"
                quality["issue"] = "Some catalyst candidates were quarantined or dropped for weak target relevance."
            else:
                quality["status"] = "ok"
                quality["issue"] = quality.get("issue") or ""
        quality.update(
            {
                "chars": len(str(raw or "")),
                "accepted_events": accepted_count,
                "quarantined_events": quarantined_count,
                "dropped_events": dropped_count,
                "contamination_score": contamination_score,
            }
        )
        source_quality[section] = quality

    missing_count = sum(1 for item in source_quality.values() if item.get("status") == "missing")
    if not results or missing_count >= max(2, len(results) // 2 + 1):
        gate = "failed"
    elif max_contamination >= 0.50:
        gate = "contaminated"
    elif accepted_total == 0:
        gate = "sparse"
    elif max_contamination > 0:
        gate = "degraded"
    else:
        gate = "clean"
    bundle_quality = {
        "accepted_event_count": accepted_total,
        "quarantined_event_count": quarantined_total,
        "dropped_event_count": dropped_total,
        "max_source_contamination": round(max_contamination, 2),
        "has_target_material_event": False,
        "quality_gate": gate,
    }
    return {"source_quality": source_quality, "bundle_quality": bundle_quality}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = text.upper()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _company_common_aliases(company_name: str | None) -> list[str]:
    name = str(company_name or "").strip()
    if not name:
        return []
    aliases = [name]
    base = re.sub(
        r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|class [a-z])\.?\b",
        "",
        name,
        flags=re.I,
    )
    base = re.sub(r"[,.\s]+", " ", base).strip()
    if base and base.lower() != name.lower():
        aliases.append(base)
    if re.search(r"\bcorporation\b", name, re.I):
        aliases.append(re.sub(r"\bcorporation\b", "Corp", name, flags=re.I).replace(".,", "."))
    if re.search(r"\bincorporated\b", name, re.I):
        aliases.append(re.sub(r"\bincorporated\b", "Inc", name, flags=re.I).replace(".,", "."))
    return _dedupe_preserve_order(aliases)


def _company_name_from_fundamentals(raw: Any) -> str | None:
    parsed = _first_json_object(raw)
    if isinstance(parsed, dict):
        for key in (
            "Name",
            "name",
            "CompanyName",
            "company_name",
            "Company Name",
            "shortName",
            "longName",
        ):
            value = parsed.get(key)
            if value and str(value).strip().upper() not in {"N/A", "NONE", "NULL"}:
                return str(value).strip()
    text = str(raw or "")
    for pattern in (
        r'"(?:Name|CompanyName|company_name|shortName|longName)"\s*:\s*"([^"]+)"',
        r"(?:^|\n)\s*(?:Name|Company Name|CompanyName|company_name)\s*[:=]\s*([^\n\r]+)",
    ):
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip().strip('",')
            if value and value.upper() not in {"N/A", "NONE", "NULL"}:
                return value
    return None


def resolve_company_aliases(
    ticker: str,
    curr_date: str,
    *,
    fundamentals_raw: Any = None,
) -> dict[str, Any]:
    """Resolve stable target aliases from existing fundamentals metadata first."""
    symbol = str(ticker or "").strip().upper()
    company_name = _company_name_from_fundamentals(fundamentals_raw)
    aliases = [symbol]
    if symbol:
        aliases.append(f"NASDAQ:{symbol}")
        aliases.append(f"NYSE:{symbol}")
    aliases.extend(_company_common_aliases(company_name))
    return {
        "ticker": symbol,
        "company_name": company_name,
        "aliases": _dedupe_preserve_order(aliases),
        "negative_aliases": [],
        "source": "fundamentals_company_overview" if company_name else "ticker_fallback",
        "confidence": 0.9 if company_name else 0.35,
        "as_of": curr_date,
    }


def extract_mentioned_tickers(text: str) -> list[str]:
    """Extract ticker-like symbols while suppressing common finance acronyms."""
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?<![A-Za-z0-9.])(?:\$|NASDAQ:|NYSE:)?([A-Z][A-Z0-9]{0,4})(?![A-Za-z0-9.])", str(text or "")):
        token = match.group(1).upper()
        if len(token) < 2:
            continue
        if token in _COMMON_FALSE_TICKERS:
            continue
        if token not in seen:
            found.append(token)
            seen.add(token)
    return found


def _alias_matches(text: str, aliases: list[str]) -> list[str]:
    matches: list[str] = []
    for alias in aliases:
        clean = str(alias or "").strip()
        if not clean:
            continue
        if clean.upper().startswith(("NASDAQ:", "NYSE:")):
            pattern = rf"\b{re.escape(clean)}\b"
        elif clean.isupper() and len(clean) <= 5:
            pattern = rf"(?<![A-Za-z0-9.])\$?{re.escape(clean)}(?![A-Za-z0-9.])"
        else:
            pattern = rf"\b{re.escape(clean)}\b"
        if re.search(pattern, text, flags=re.I):
            matches.append(clean)
    return _dedupe_preserve_order(matches)


def score_event_relevance(
    *,
    text: str,
    ticker: str,
    aliases: list[str],
    source: str,
    vendor_ticker: str | None = None,
) -> dict[str, Any]:
    target = str(ticker or "").strip().upper()
    body = str(text or "")
    all_aliases = _dedupe_preserve_order([target, *aliases])
    matched_aliases = _alias_matches(body, all_aliases)
    mentioned_tickers = extract_mentioned_tickers(body)
    unrelated_tickers = [item for item in mentioned_tickers if item != target]
    target_found = bool(matched_aliases) or target in mentioned_tickers
    score = 0.0
    flags: list[str] = []

    if target and target in mentioned_tickers:
        score += 0.50
    legal_or_common_matches = [
        alias
        for alias in matched_aliases
        if not (alias.upper() == target or alias.upper().startswith(("NASDAQ:", "NYSE:")))
    ]
    if legal_or_common_matches:
        score += 0.40
        if any(len(alias.split()) >= 2 for alias in legal_or_common_matches):
            score += 0.25
    if vendor_ticker and str(vendor_ticker).strip().upper() == target:
        score += 0.25
    source_text = str(source or "")
    if _alias_matches(source_text, all_aliases):
        score += 0.15
    if unrelated_tickers and not target_found:
        score -= 0.50
        flags.append("unrelated_ticker_without_target")
    if unrelated_tickers and target_found:
        flags.append("mixed_target_and_unrelated_tickers")
    if unrelated_tickers and mentioned_tickers and mentioned_tickers[0] != target:
        flags.append("unrelated_ticker_prominent")
    score = max(0.0, min(1.0, score))

    if score < 0.35 or ("unrelated_ticker_prominent" in flags and not target_found):
        decision = "drop"
    elif score < 0.65 or "mixed_target_and_unrelated_tickers" in flags:
        decision = "quarantine"
    else:
        decision = "accept"

    return {
        "relevance_score": score,
        "decision": decision,
        "matched_aliases": matched_aliases,
        "mentioned_tickers": mentioned_tickers,
        "contamination_flags": flags,
        "quarantine_reason": ";".join(flags) if decision == "quarantine" and flags else None,
    }


def _event_from_line(
    *,
    ticker: str,
    source: str,
    line: str,
    event_type: str,
    as_of: str,
    idx: int,
    materiality_score: float,
    relevance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", str(line or "")).strip("-* |")
    event_id = f"{source}_{idx:03d}"
    relevance = relevance or {}
    return {
        "event_id": event_id,
        "source_event_id": event_id,
        "ticker": ticker,
        "event_type": event_type,
        "event_time": None,
        "detected_at": as_of,
        "source": source,
        "title": title[:220] or f"{source} event",
        "summary": title[:500],
        "url": None,
        "materiality_score": materiality_score,
        "novelty_score": 0.5,
        "sentiment_score": None,
        "confidence": 0.6,
        "relevance_score": relevance.get("relevance_score", 0.0),
        "matched_aliases": relevance.get("matched_aliases", []),
        "mentioned_tickers": relevance.get("mentioned_tickers", []),
        "contamination_flags": relevance.get("contamination_flags", []),
        "quarantine_reason": relevance.get("quarantine_reason"),
    }


def _looks_like_noise_line(line: str) -> bool:
    text = str(line or "").strip()
    lower = text.lower()
    if not text:
        return True
    if re.match(r"^https?://\S+$", text, flags=re.I):
        return True
    if re.match(r'^["\'][^"\']+["\']\s*:\s*', text):
        return True
    if re.match(r"^[a-zA-Z_][\w-]*\s*:\s*", text):
        return True
    if " etf " in f" {lower} " or lower.startswith("etf "):
        return True
    if "schd" in lower:
        return True
    return False


def _classify_event_type(line: str, source: str) -> str:
    lower = line.lower()
    if source == "price_action_summary":
        return "price_volume_shock"
    if "earnings" in lower:
        return "earnings_result"
    if "guidance" in lower or "outlook" in lower:
        return "guidance_change"
    if "filing" in lower or " sec " in f" {lower} ":
        return "sec_filing"
    if "insider" in lower or "form 4" in lower:
        return "insider_transaction"
    if "lawsuit" in lower or "regulatory" in lower:
        return "lawsuit_regulatory"
    if "contract" in lower:
        return "customer_contract"
    if "launch" in lower:
        return "product_launch"
    if "offering" in lower or "raise" in lower or "dilution" in lower:
        return "capital_raise"
    if "upgrade" in lower or "downgrade" in lower or "rating" in lower:
        return "analyst_rating_change"
    if "volume" in lower or "price" in lower or "rallied" in lower:
        return "price_volume_shock"
    return "other"


def _events_from_text(
    ticker: str,
    source: str,
    raw: Any,
    as_of: str,
    limit: int = 8,
    *,
    aliases: list[str] | None = None,
    include_diagnostics: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    text = str(raw or "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    target_aliases = _dedupe_preserve_order([str(ticker or "").upper(), *(aliases or [])])
    for line in lines:
        lower = line.lower()
        if len(line) < 18:
            continue
        if _looks_like_noise_line(line):
            continue
        if any(
            term in lower
            for term in (
                "earnings",
                "guidance",
                "filing",
                "sec",
                "offering",
                "insider",
                "lawsuit",
                "regulatory",
                "contract",
                "launch",
                "dividend",
                "split",
                "rating",
                "upgrade",
                "downgrade",
                "volume",
                "price",
            )
        ):
            relevance = score_event_relevance(
                text=line,
                ticker=ticker,
                aliases=target_aliases,
                source=source,
                vendor_ticker=ticker,
            )
            event = _event_from_line(
                ticker=ticker,
                source=source,
                line=line,
                event_type=_classify_event_type(line, source),
                as_of=as_of,
                idx=len(accepted) + len(quarantined) + len(dropped) + 1,
                materiality_score=0.55 if source == "price_action_summary" else 0.45,
                relevance=relevance,
            )
            if relevance["decision"] == "accept":
                accepted.append(event)
            elif relevance["decision"] == "quarantine":
                if not event.get("quarantine_reason"):
                    event["quarantine_reason"] = "target relevance below accept threshold"
                quarantined.append(event)
            else:
                dropped.append(event)
        if len(accepted) >= limit:
            break
    if include_diagnostics:
        return accepted[:limit], quarantined, dropped
    return accepted[:limit]


def _filings_from_raw(raw: Any, as_of: str) -> list[dict[str, Any]]:
    parsed = _first_json_object(raw)
    candidates: list[Any] = []
    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, dict):
        for key in ("filings", "recent_filings", "data", "items"):
            if isinstance(parsed.get(key), list):
                candidates = parsed[key]
                break
        if not candidates and any(k in parsed for k in ("form_type", "form", "accession_number")):
            candidates = [parsed]
    filings = []
    for idx, item in enumerate([x for x in candidates if isinstance(x, dict)][:10], 1):
        form = str(item.get("form_type") or item.get("form") or item.get("type") or "").upper()
        url = item.get("primary_document_url") or item.get("url") or item.get("link") or ""
        filings.append(
            {
                "accession_number": str(item.get("accession_number") or item.get("accessionNo") or f"filing_{idx:03d}"),
                "cik": str(item.get("cik") or ""),
                "form_type": form or "SEC",
                "filing_date": str(item.get("filing_date") or item.get("date") or as_of),
                "report_date": item.get("report_date"),
                "primary_document_url": str(url or ""),
                "filing_summary": str(item.get("filing_summary") or item.get("summary") or item.get("description") or ""),
                "extracted_signals": [],
                "materiality_score": 0.75 if form in {"8-K", "10-Q", "10-K", "S-3", "424B"} else 0.5,
            }
        )
    if filings:
        return filings
    text = str(raw or "")
    out = []
    for idx, line in enumerate([line.strip() for line in text.splitlines() if line.strip()][:10], 1):
        if not re.search(r"\b(10-K|10-Q|8-K|S-1|S-3|424B|DEF 14A|13D|13G|Form 4|6-K|20-F)\b", line, re.I):
            continue
        match = re.search(r"\b(10-K|10-Q|8-K|S-1|S-3|424B|DEF 14A|13D|13G|Form 4|6-K|20-F)\b", line, re.I)
        form = match.group(1).upper() if match else "SEC"
        out.append(
            {
                "accession_number": f"text_filing_{idx:03d}",
                "cik": "",
                "form_type": form,
                "filing_date": as_of,
                "report_date": None,
                "primary_document_url": "",
                "filing_summary": line[:500],
                "extracted_signals": [],
                "materiality_score": 0.75 if form in {"8-K", "10-Q", "10-K", "S-3", "424B"} else 0.5,
            }
        )
    return out


def _filing_materiality(form_type: Any) -> float:
    form = str(form_type or "").upper()
    if form in {"S-1", "S-3", "424B", "424B3", "424B5"}:
        return 0.80
    if form == "8-K":
        return 0.75
    if form in {"13D", "13G"}:
        return 0.70
    if form in {"10-Q", "10-K", "6-K", "20-F"}:
        return 0.65
    if form == "DEF 14A":
        return 0.50
    if form == "FORM 4":
        return 0.45
    return 0.50


def _filing_event_from_record(
    filing: dict[str, Any],
    *,
    ticker: str,
    as_of: str,
    idx: int,
) -> dict[str, Any]:
    form = str(filing.get("form_type") or "SEC").upper()
    accession = str(filing.get("accession_number") or f"filing_{idx:03d}")
    materiality = _filing_materiality(form)
    return {
        "event_id": f"filing_{idx:03d}",
        "source_event_id": accession,
        "ticker": ticker,
        "event_type": "sec_filing",
        "event_time": filing.get("filing_date"),
        "detected_at": as_of,
        "source": "recent_sec_filings",
        "title": f"{form or 'SEC filing'} filed",
        "summary": filing.get("filing_summary") or "",
        "url": filing.get("primary_document_url") or None,
        "materiality_score": materiality,
        "novelty_score": 0.6,
        "sentiment_score": None,
        "confidence": 0.75,
        "relevance_score": 1.0,
        "matched_aliases": [ticker],
        "mentioned_tickers": [ticker],
        "contamination_flags": [],
        "quarantine_reason": None,
    }


def _earnings_events_from_calendar(
    raw: Any,
    *,
    ticker: str,
    as_of: str,
    aliases: list[str],
) -> list[dict[str, Any]]:
    parsed = _first_json_object(raw)
    if isinstance(parsed, dict):
        items = parsed.get("earningsCalendar") or parsed.get("earnings_calendar") or parsed.get("items") or []
    elif isinstance(parsed, list):
        items = parsed
    else:
        items = raw if isinstance(raw, list) else []
    target = str(ticker or "").upper()
    out: list[dict[str, Any]] = []
    for idx, item in enumerate([x for x in items if isinstance(x, dict)], 1):
        symbol = str(item.get("symbol") or item.get("ticker") or "").upper()
        if symbol != target:
            continue
        event_date = str(item.get("date") or item.get("earningsDate") or item.get("reportDate") or "").strip()
        if not event_date:
            continue
        hour = str(item.get("hour") or item.get("time") or "").strip().lower()
        timing = {"amc": "after market close", "bmo": "before market open"}.get(hour, hour)
        summary = "Upcoming earnings can create near-term gap and guidance risk."
        if timing:
            summary += f" Expected timing: {timing}."
        out.append(
            {
                "event_id": f"earnings_calendar_{len(out) + 1:03d}",
                "source_event_id": f"{target}_{event_date}_earnings",
                "ticker": target,
                "event_type": "earnings_date",
                "event_time": event_date,
                "detected_at": as_of,
                "source": "earnings_calendar",
                "title": "Expected earnings date",
                "summary": summary,
                "url": None,
                "materiality_score": 0.65,
                "novelty_score": 0.4,
                "sentiment_score": None,
                "confidence": 0.55,
                "relevance_score": 1.0,
                "matched_aliases": [target],
                "mentioned_tickers": [target],
                "contamination_flags": [],
                "quarantine_reason": None,
            }
        )
    return out[:5]


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _position_context_from_portfolio(portfolio_context: Any, *, ticker: str) -> dict[str, Any] | None:
    text = str(portfolio_context or "").strip()
    if not text:
        return None
    parsed = _first_json_object(portfolio_context)
    target = str(ticker or "").upper()
    position: dict[str, Any] | None = None
    portfolio_value = None
    if isinstance(parsed, dict):
        portfolio_value = _to_float(parsed.get("portfolio_value") or parsed.get("equity") or parsed.get("total_value"))
        candidates: list[Any] = []
        for key in ("positions", "open_positions", "holdings"):
            if isinstance(parsed.get(key), list):
                candidates.extend(parsed[key])
        if not candidates and any(k in parsed for k in ("symbol", "ticker", "qty", "market_value")):
            candidates.append(parsed)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("ticker") or "").upper()
            if symbol == target:
                position = item
                break
    if not position:
        return {
            "has_position": True,
            "position_size_pct": None,
            "cost_basis": None,
            "unrealized_pnl_pct": None,
            "stop_loss": None,
            "target_price": None,
            "max_position_size_pct": None,
            "holding_period": None,
        }
    market_value = _to_float(position.get("market_value") or position.get("marketValue"))
    base_value = _to_float(position.get("portfolio_value") or position.get("equity") or position.get("total_value")) or portfolio_value
    size_pct = None
    if market_value is not None and base_value:
        size_pct = round(market_value / base_value, 4)
    return {
        "has_position": True,
        "position_size_pct": size_pct,
        "cost_basis": _to_float(position.get("cost_basis") or position.get("avg_entry_price") or position.get("avgEntryPrice")),
        "unrealized_pnl_pct": _to_float(position.get("unrealized_pnl_pct") or position.get("unrealized_plpc")),
        "stop_loss": _to_float(position.get("stop_loss")),
        "target_price": _to_float(position.get("target_price")),
        "max_position_size_pct": _to_float(position.get("max_position_size_pct")),
        "holding_period": str(position.get("holding_period") or "").strip() or None,
    }


def _missing_summary(section: str, value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return "empty output"
    lower = text.lower()
    if lower.startswith("toolerror"):
        return text[:240]
    first_line = text.splitlines()[0].strip()
    first_lower = first_line.lower()
    missing_patterns = (
        r"^no\s+\w+.*(?:available|found|returned|data)",
        r"not available",
        r"\bfailed\b",
        r"\berror\b",
        r"\bmissing\b",
        r"^n/a$",
        r"^nan$",
    )
    for pattern in missing_patterns:
        if re.search(pattern, first_lower):
            return first_line[:240]
    return None


def format_evidence_bundle(
    bundle_name: str,
    symbol: str,
    curr_date: str,
    results: Dict[str, Any],
    *,
    max_chars: int = 6000,
) -> str:
    """Return a compact JSON evidence packet instead of raw concatenated tool output."""
    domain = _bundle_domain(bundle_name)
    facts: list[dict[str, Any]] = []
    missing_data: list[dict[str, str]] = []
    source_quality: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}

    for section, raw in results.items():
        text = str(raw or "")
        if missing := _missing_summary(section, text):
            missing_data.append({"section": section, "issue": missing})
        source_quality.append({"section": section, "chars": len(text), "status": "missing" if missing else "ok"})

        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        scored = [
            (_score_bundle_line(line), idx, line)
            for idx, line in enumerate(lines)
            if line and not line.startswith("##")
        ]
        selected = [
            line
            for score, _, line in sorted(scored, key=lambda item: (-item[0], item[1]))
            if score > 0
        ][:4]
        for line in selected:
            section_counts[section] = section_counts.get(section, 0) + 1
            fact_text = line[:320]
            facts.append(
                {
                    "id": f"fact_{domain}_{_clean_fact_part(section)}_{section_counts[section]:03d}",
                    "domain": domain,
                    "claim": fact_text,
                    "text": fact_text,
                    "source": section,
                    "section": section,
                    "as_of": curr_date,
                    "confidence": 0.85,
                    "quality": "normal",
                    "source_type": "vendor",
                }
            )

    packet: dict[str, Any] = {
        "bundle": bundle_name,
        "symbol": symbol,
        "date": curr_date,
        "facts": facts[:28],
        "missing_data": missing_data[:12],
        "source_quality": source_quality,
        "instruction": "Use this compact evidence packet for analysis; do not treat omitted raw rows as absent data.",
    }

    text = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text

    packet["facts"] = packet["facts"][: max(6, len(packet["facts"]) // 2)]
    packet["source_quality"] = packet["source_quality"][:12]
    text = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    while len(text) > max_chars and packet["facts"]:
        packet["facts"].pop()
        text = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    return text[:max_chars]


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
    return format_evidence_bundle("Market Data Bundle", symbol, curr_date, results)


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
    return format_evidence_bundle("Fundamentals Data Bundle", ticker, curr_date, results)


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
    return format_evidence_bundle("News Data Bundle", ticker, curr_date, results)


@tool
async def get_catalyst_event_bundle(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[str, "Current trading date in YYYY-mm-dd format"],
    portfolio_context: Annotated[str, "Optional current portfolio context"] = "",
    company_look_back_days: Annotated[int, "Company-news look-back days"] = 14,
) -> str:
    """Build the structured CatalystEventBundle used by the catalyst analyst."""
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_date = (curr_dt - timedelta(days=int(company_look_back_days))).strftime("%Y-%m-%d")
        earnings_end_date = (curr_dt + timedelta(days=45)).strftime("%Y-%m-%d")
    except Exception:
        start_date = curr_date
        earnings_end_date = curr_date

    tasks = {
        "fundamentals_overview": asyncio.create_task(
            _run_tool(get_fundamentals, {"ticker": ticker, "curr_date": curr_date})
        ),
        "earnings_calendar": asyncio.create_task(
            _run_sync_callable("get_earnings_calendar_finnhub", get_earnings_calendar_finnhub, curr_date, earnings_end_date)
        ),
        "company_news_window": asyncio.create_task(
            _run_tool(
                get_company_news_window,
                {"ticker": ticker, "curr_date": curr_date, "look_back_days": int(company_look_back_days)},
            )
        ),
        "company_news_raw": asyncio.create_task(
            _run_tool(get_news, {"ticker": ticker, "start_date": start_date, "end_date": curr_date})
        ),
        "recent_sec_filings": asyncio.create_task(
            _run_tool(get_recent_sec_filings, {"ticker": ticker, "curr_date": curr_date})
        ),
        "insider_transactions": asyncio.create_task(
            _run_tool(get_insider_transactions, {"ticker": ticker, "curr_date": curr_date})
        ),
        "price_action_summary": asyncio.create_task(
            _run_tool(get_price_action_summary, {"symbol": ticker, "curr_date": curr_date, "look_back_days": 90})
        ),
    }
    results = {key: await task for key, task in tasks.items()}
    identity = resolve_company_aliases(
        ticker,
        curr_date,
        fundamentals_raw=results.get("fundamentals_overview"),
    )
    aliases = list(identity.get("aliases") or [ticker])
    recent_events: list[dict[str, Any]] = []
    quarantined_events: list[dict[str, Any]] = []
    event_diagnostics: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for source, raw, limit in (
        ("company_news_window", results["company_news_window"], 8),
        ("company_news_raw", results["company_news_raw"], 8),
        ("insider_transactions", results["insider_transactions"], 8),
        ("price_action_summary", results["price_action_summary"], 4),
    ):
        accepted, quarantined, dropped = _events_from_text(
            ticker,
            source,
            raw,
            curr_date,
            limit=limit,
            aliases=aliases,
            include_diagnostics=True,
        )
        recent_events.extend(accepted)
        quarantined_events.extend(quarantined)
        event_diagnostics[source] = {
            "accepted_events": accepted,
            "quarantined_events": quarantined,
            "dropped_events": dropped,
        }
    filings = _filings_from_raw(results["recent_sec_filings"], curr_date)
    for idx, filing in enumerate(filings[:5], 1):
        recent_events.append(_filing_event_from_record(filing, ticker=ticker, as_of=curr_date, idx=idx))
    event_diagnostics["recent_sec_filings"] = {
        "accepted_events": recent_events[-len(filings[:5]) :] if filings else [],
        "quarantined_events": [],
        "dropped_events": [],
    }
    upcoming_events = _earnings_events_from_calendar(
        results.get("earnings_calendar"),
        ticker=ticker,
        as_of=curr_date,
        aliases=aliases,
    )
    event_diagnostics["earnings_calendar"] = {
        "accepted_events": upcoming_events,
        "quarantined_events": [],
        "dropped_events": [],
    }
    quality = _source_quality_from_event_diagnostics(results, event_diagnostics)
    quality["bundle_quality"]["has_target_material_event"] = any(
        float(event.get("materiality_score") or 0.0) >= 0.65 for event in recent_events + upcoming_events
    )

    price_text = str(results.get("price_action_summary") or "")
    shock = bool(re.search(r"\b(gap|breakout|breakdown|volume|shock|spike|plunge)\b", price_text, re.I))
    bundle = {
        "bundle": "CatalystEventBundle",
        "ticker": ticker,
        "company_name": identity.get("company_name"),
        "aliases": aliases,
        "as_of": curr_date,
        "recent_events": recent_events[:20],
        "quarantined_events": quarantined_events[:20],
        "dropped_event_count": quality["bundle_quality"].get("dropped_event_count", 0),
        "upcoming_events": upcoming_events,
        "recent_filings": filings[:10],
        "macro_events": [],
        "market_context": {
            "last_close": None,
            "one_day_return_pct": None,
            "five_day_return_pct": None,
            "volume_ratio": None,
            "price_volume_shock": shock,
            "summary": price_text[:1000],
        },
        "position_context": _position_context_from_portfolio(portfolio_context, ticker=ticker),
        "prior_thesis": None,
        "source_quality": quality["source_quality"],
        "bundle_quality": quality["bundle_quality"],
        "data_freshness": {key: curr_date for key in results},
    }
    return json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))


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
    return format_evidence_bundle("Sentiment Data Bundle", ticker, curr_date, results)
