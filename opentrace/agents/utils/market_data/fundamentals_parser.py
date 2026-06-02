from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any


FIELD_ALIASES = {
    "total_revenue": {
        "totalrevenue",
        "total revenue",
        "revenue",
        "revenues",
    },
    "cost_of_revenue": {
        "costofrevenue",
        "cost of revenue",
        "costofgoodsandservicessold",
        "cost of goods and services sold",
    },
    "gross_profit": {"grossprofit", "gross profit"},
    "operating_income": {
        "operatingincome",
        "operating income",
        "operatingincomeorloss",
        "operating income or loss",
    },
    "net_income": {
        "netincome",
        "net income",
        "netincomeapplicabletocommonshares",
        "net income applicable to common shares",
    },
    "operating_cashflow": {
        "operatingcashflow",
        "operating cashflow",
        "operating cash flow",
        "totalcashfromoperatingactivities",
        "total cash from operating activities",
    },
    "cash_and_short_term_investments": {
        "cashandshortterminvestments",
        "cash and short term investments",
        "cashandcashequivalentsatcarryingvalue",
        "cash and cash equivalents at carrying value",
        "cashcashequivalentsandshortterminvestments",
        "cash cash equivalents and short term investments",
    },
    "total_debt": {
        "shortlongtermdebttotal",
        "short long term debt total",
        "totaldebt",
        "total debt",
    },
    "short_term_debt": {"shorttermdebt", "short term debt"},
    "long_term_debt": {"longtermdebt", "long term debt"},
}

OVERVIEW_FIELDS = {
    "OperatingMarginTTM": "operating_margin_ttm",
    "ProfitMargin": "profit_margin",
    "RevenueTTM": "revenue_ttm",
    "RevenuePerShareTTM": "revenue_per_share_ttm",
}


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _to_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in {"NONE", "NULL", "N/A", "NA", "NAN", "-"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        number = float(text)
    except Exception:
        return None
    if number.is_integer():
        return int(number)
    return number


def _safe_ratio(numerator: Any, denominator: Any) -> float | None:
    top = _to_number(numerator)
    bottom = _to_number(denominator)
    if top is None or bottom in {None, 0}:
        return None
    return float(top) / float(bottom)


def _parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def _first_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _canonical_field(key: Any) -> str | None:
    normalized = _normalize_key(key)
    for canonical, aliases in FIELD_ALIASES.items():
        if normalized in {_normalize_key(alias) for alias in aliases}:
            return canonical
    return None


def _statement_type(section: str) -> str:
    lower = str(section or "").lower()
    if "income" in lower:
        return "income_statement"
    if "balance" in lower:
        return "balance_sheet"
    if "cash" in lower:
        return "cashflow"
    return lower or "statement"


def _derive_period_metrics(period: dict[str, Any]) -> dict[str, Any]:
    revenue = period.get("total_revenue")
    gross_profit = period.get("gross_profit")
    cost_of_revenue = period.get("cost_of_revenue")
    if gross_profit is None and revenue is not None and cost_of_revenue is not None:
        gross_profit = float(revenue) - float(cost_of_revenue)
        period["gross_profit"] = int(gross_profit) if float(gross_profit).is_integer() else gross_profit

    period["gross_margin"] = _safe_ratio(gross_profit, revenue)
    period["operating_margin"] = _safe_ratio(period.get("operating_income"), revenue)
    period["net_margin"] = _safe_ratio(period.get("net_income"), revenue)
    period["operating_cashflow_margin"] = _safe_ratio(period.get("operating_cashflow"), revenue)

    cash = period.get("cash_and_short_term_investments")
    total_debt = period.get("total_debt")
    if total_debt is None:
        debt_parts = [
            value
            for value in (period.get("short_term_debt"), period.get("long_term_debt"))
            if value is not None
        ]
        if debt_parts:
            total_debt = sum(float(value) for value in debt_parts)
            period["total_debt"] = int(total_debt) if float(total_debt).is_integer() else total_debt
    if cash is not None and total_debt is not None:
        net_cash = float(cash) - float(total_debt)
        period["net_cash"] = int(net_cash) if net_cash.is_integer() else net_cash
    else:
        period["net_cash"] = None
    return period


def _period_from_mapping(row: dict[str, Any], section: str, source: str) -> dict[str, Any] | None:
    period_end = _parse_date(row.get("fiscalDateEnding") or row.get("period_end") or row.get("asOfDate"))
    if not period_end:
        return None
    period: dict[str, Any] = {
        "period_end": period_end,
        "source": source,
        "statement_type": _statement_type(section),
    }
    for key, value in row.items():
        canonical = _canonical_field(key)
        if canonical:
            period[canonical] = _to_number(value)
    return _derive_period_metrics(period)


def _parse_json_statement(raw: Any, section: str, source: str) -> list[dict[str, Any]]:
    parsed = _first_json(raw)
    if not isinstance(parsed, dict):
        return [
            period
            for row in _extract_complete_report_objects(raw)
            if (period := _period_from_mapping(row, section, source))
        ]
    rows = parsed.get("quarterlyReports") or parsed.get("annualReports")
    if not isinstance(rows, list):
        rows = [parsed] if any(key in parsed for key in ("fiscalDateEnding", "period_end")) else []
    periods = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = _period_from_mapping(row, section, source)
        if period:
            periods.append(period)
    return periods


def _extract_complete_report_objects(raw: Any) -> list[dict[str, Any]]:
    text = str(raw or "")
    if "fiscalDateEnding" not in text:
        return []
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    stack: list[int] = []
    in_string = False
    escaped = False
    for idx, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            stack.append(idx)
            continue
        if char != "}" or not stack:
            continue
        start = stack.pop()
        snippet = text[start : idx + 1]
        if "fiscalDateEnding" not in snippet:
            continue
        try:
            obj, end = decoder.raw_decode(snippet)
        except Exception:
            continue
        if end == len(snippet) and isinstance(obj, dict):
            objects.append(obj)
    return objects


def _parse_csv_statement(raw: Any, section: str, source: str) -> list[dict[str, Any]]:
    text = str(raw or "")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return []
    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines))))
    except Exception:
        return []
    if not rows or len(rows[0]) < 2:
        return []
    dates = [_parse_date(value) for value in rows[0][1:]]
    if not any(dates):
        return []
    periods = [
        {"period_end": date, "source": source, "statement_type": _statement_type(section)}
        for date in dates
    ]
    for row in rows[1:]:
        if not row:
            continue
        canonical = _canonical_field(row[0])
        if not canonical:
            continue
        for idx, value in enumerate(row[1:]):
            if idx >= len(periods) or periods[idx]["period_end"] is None:
                continue
            periods[idx][canonical] = _to_number(value)
    return [_derive_period_metrics(period) for period in periods if period.get("period_end")]


def _parse_statement(raw: Any, section: str) -> list[dict[str, Any]]:
    source = "alpha_vantage" if isinstance(_first_json(raw), dict) else "yfinance_csv"
    periods = _parse_json_statement(raw, section, source)
    if periods:
        return periods
    return _parse_csv_statement(raw, section, "yfinance_csv")


def _parse_overview(raw: Any) -> dict[str, Any]:
    parsed = _first_json(raw)
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, Any] = {}
    for vendor_key, canonical in OVERVIEW_FIELDS.items():
        out[canonical] = _to_number(parsed.get(vendor_key))
    return {key: value for key, value in out.items() if value is not None}


def latest_periods(periods: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    return sorted(
        periods,
        key=lambda item: item.get("period_end") or "",
        reverse=True,
    )[:limit]


def _combined_latest_periods(periods: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for period in latest_periods(periods, limit=100):
        period_end = period.get("period_end")
        if not period_end:
            continue
        target = combined.setdefault(
            period_end,
            {"period_end": period_end, "source": period.get("source"), "statement_type": "combined"},
        )
        for key, value in period.items():
            if key in {"period_end", "source", "statement_type"}:
                continue
            if value is not None and target.get(key) is None:
                target[key] = value
    return [_derive_period_metrics(period) for period in latest_periods(list(combined.values()), limit=limit)]


def _reconciliation_flags(
    overview: dict[str, Any],
    periods: list[dict[str, Any]],
    source_quality: list[dict[str, Any]] | None = None,
) -> list[str]:
    flags: list[str] = []
    latest_income = next(
        (
            period
            for period in latest_periods(periods, limit=20)
            if period.get("statement_type") == "income_statement"
        ),
        None,
    )
    if any(
        period.get("statement_type") == "income_statement"
        and period.get("operating_margin") is not None
        and float(period["operating_margin"]) >= 0.50
        for period in periods
    ):
        flags.append("confirmed_high_operating_margin")

    overview_operating_margin = overview.get("operating_margin_ttm")
    if overview_operating_margin is not None:
        if latest_income and latest_income.get("operating_margin") is not None:
            latest_op_margin = float(latest_income["operating_margin"])
            if (
                float(overview_operating_margin) >= 0.50
                and latest_op_margin >= 0.50
            ) or (
                float(overview_operating_margin) < 0.50
                and latest_op_margin < 0.50
            ):
                flags.append("overview_statement_consistent")
        else:
            flags.append("period_mismatch_possible")

    income_periods = [p for p in periods if p.get("statement_type") == "income_statement"]
    if not income_periods:
        flags.append("missing_period_labels")
    for period in income_periods[:4]:
        if period.get("operating_income") is None:
            flags.append("missing_operating_income")
        if period.get("cost_of_revenue") is None and period.get("gross_profit") is None:
            flags.append("missing_cost_of_revenue")

    for quality in source_quality or []:
        if quality.get("status") == "missing":
            flags.append(f"missing_{quality.get('section')}")

    return list(dict.fromkeys(flags))


def parse_fundamentals_sections(
    results: dict[str, Any],
    *,
    symbol: str,
    curr_date: str,
) -> dict[str, Any]:
    source_quality: list[dict[str, Any]] = []
    periods: list[dict[str, Any]] = []
    overview = _parse_overview(results.get("fundamentals"))
    for section, raw in results.items():
        text = str(raw or "")
        status = "missing" if not text.strip() or text.lower().startswith("no ") else "ok"
        source_quality.append({"section": section, "chars": len(text), "status": status})
        if section in {"income_statement", "balance_sheet", "cashflow"}:
            periods.extend(_parse_statement(raw, section))

    return {
        "symbol": symbol,
        "date": curr_date,
        "overview_metrics": overview,
        "periods": latest_periods(periods, limit=20),
        "latest_periods": _combined_latest_periods(periods, limit=4),
        "source_quality": source_quality,
        "reconciliation_flags": _reconciliation_flags(overview, periods, source_quality),
    }


def _fact_id(*parts: Any) -> str:
    raw = "_".join(str(part or "") for part in parts)
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", raw)).strip("_").lower()


def _add_fact(
    facts: list[dict[str, Any]],
    *,
    symbol: str,
    curr_date: str,
    period: dict[str, Any],
    metric: str,
    value: Any,
) -> None:
    if value is None:
        return
    statement_type = period.get("statement_type") or "statement"
    period_end = period.get("period_end")
    facts.append(
        {
            "id": _fact_id("fact_fundamentals", statement_type, period_end, metric),
            "domain": "fundamentals",
            "claim": f"{symbol} {statement_type} {metric} for {period_end}: {value}",
            "text": f"{metric}={value}; period_end={period_end}; statement_type={statement_type}",
            "source": period.get("source") or "vendor",
            "section": statement_type,
            "as_of": curr_date,
            "confidence": 0.9,
            "quality": "normal",
            "source_type": "vendor",
        }
    )


def build_fundamentals_packet(
    symbol: str,
    curr_date: str,
    results: dict[str, Any],
    *,
    max_chars: int = 6000,
) -> dict[str, Any]:
    parsed = parse_fundamentals_sections(results, symbol=symbol, curr_date=curr_date)
    facts: list[dict[str, Any]] = []
    fact_metrics = (
        "total_revenue",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "operating_cashflow_margin",
        "net_cash",
    )
    for period in parsed["periods"]:
        for metric in fact_metrics:
            _add_fact(
                facts,
                symbol=symbol,
                curr_date=curr_date,
                period=period,
                metric=metric,
                value=period.get(metric),
            )

    packet = {
        "bundle": "Fundamentals Data Bundle",
        "symbol": symbol,
        "date": curr_date,
        "facts": facts[:40],
        "missing_data": [
            {"section": item["section"], "issue": "empty output"}
            for item in parsed["source_quality"]
            if item.get("status") == "missing"
        ],
        "source_quality": parsed["source_quality"],
        "overview_metrics": parsed["overview_metrics"],
        "latest_periods": parsed["latest_periods"],
        "derived_metrics": {
            "latest_operating_margin": (
                parsed["latest_periods"][0].get("operating_margin")
                if parsed["latest_periods"]
                else None
            ),
            "latest_gross_margin": (
                parsed["latest_periods"][0].get("gross_margin")
                if parsed["latest_periods"]
                else None
            ),
            "latest_net_margin": (
                parsed["latest_periods"][0].get("net_margin")
                if parsed["latest_periods"]
                else None
            ),
        },
        "reconciliation_flags": parsed["reconciliation_flags"],
        "instruction": (
            "Use this deterministic fundamentals packet for analysis. Treat high-surprise "
            "metrics as verification targets; do not reject statement-confirmed surprises "
            "solely because they are unusual."
        ),
    }
    text = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return packet
    packet["facts"] = packet["facts"][:20]
    return packet
