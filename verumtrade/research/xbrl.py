"""Point-in-time standardized financial facts from SEC XBRL company facts.

Each returned fact keeps the distinctions a calculation needs: unit, exact period
(start/end), whether it is a quarter, year-to-date, annual or instant value, the
form and accession that reported it, and the date it was filed. Only facts filed
on or before the as-of date are used, and a period whose value changed in a later
filing is marked as restated, with the originally reported value kept.

Aggregate company facts cover standard US-GAAP concepts only; segment, custom and
dimensional disclosures require reading the filing text.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, TypedDict

METRIC_CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss")],
    "net_income": [("us-gaap", "NetIncomeLoss")],
    "eps_diluted": [("us-gaap", "EarningsPerShareDiluted")],
    "operating_cash_flow": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities")],
    "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment")],
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue")],
    "inventory": [("us-gaap", "InventoryNet")],
    "long_term_debt": [("us-gaap", "LongTermDebtNoncurrent"), ("us-gaap", "LongTermDebt")],
    "shares_outstanding": [("dei", "EntityCommonStockSharesOutstanding")],
}
DEFAULT_METRICS = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted"]


class XbrlFact(TypedDict, total=False):
    metric: str
    concept: str
    taxonomy: str
    label: str
    unit: str
    value: float
    period_start: str | None
    period_end: str
    duration_days: int | None
    period_type: str
    fiscal_year: int | None
    fiscal_period: str
    form: str
    accession: str
    filed: str
    amended: bool
    restated: bool
    original_value: float
    original_accession: str
    first_filed: str
    derived: bool
    formula: str
    input_accessions: list[str]
    input_periods: list[str]


# Per-share and share-count values are not additive across periods, so a missing
# fiscal Q4 cannot be derived for them by subtraction.
NON_ADDITIVE_METRICS = {"eps_diluted", "shares_outstanding"}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def classify_period(start: str | None, end: str) -> tuple[str, int | None]:
    start_d, end_d = _parse_date(start), _parse_date(end)
    if start_d is None or end_d is None:
        return "instant", None
    days = (end_d - start_d).days
    if 80 <= days <= 100:
        return "quarter", days
    if 350 <= days <= 380:
        return "annual", days
    if 170 <= days <= 290:
        return "year_to_date", days
    return "other", days


def _concept_entries(companyfacts: dict[str, Any], taxonomy: str, concept: str) -> tuple[str, dict[str, list[dict]]]:
    node = ((companyfacts.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}
    return str(node.get("label") or concept), dict(node.get("units") or {})


def facts_as_of(
    companyfacts: dict[str, Any],
    as_of: str | None,
    *,
    metrics: Iterable[str] | None = None,
    periods: int = 4,
) -> list[XbrlFact]:
    """Latest ``periods`` quarter/instant values plus the latest annual value per metric."""
    cutoff = _parse_date(as_of) if as_of else None
    out: list[XbrlFact] = []
    for metric in metrics or DEFAULT_METRICS:
        for taxonomy, concept in METRIC_CONCEPTS.get(metric, []):
            label, units = _concept_entries(companyfacts, taxonomy, concept)
            facts = _point_in_time_facts(metric, taxonomy, concept, label, units, cutoff)
            if facts:
                if metric not in NON_ADDITIVE_METRICS:
                    facts.extend(_derive_fiscal_q4(facts))
                out.extend(_select_recent(facts, periods))
                break
    return out


def _point_in_time_facts(
    metric: str,
    taxonomy: str,
    concept: str,
    label: str,
    units: dict[str, list[dict]],
    cutoff: date | None,
) -> list[XbrlFact]:
    by_period: dict[tuple[str, str | None, str], list[dict]] = {}
    for unit, entries in units.items():
        for entry in entries or []:
            filed = _parse_date(entry.get("filed"))
            if filed is None or (cutoff is not None and filed > cutoff) or entry.get("val") is None:
                continue
            key = (unit, entry.get("start"), str(entry.get("end") or ""))
            by_period.setdefault(key, []).append(entry)

    facts: list[XbrlFact] = []
    for (unit, start, end), entries in by_period.items():
        entries.sort(key=lambda e: (str(e.get("filed")), str(e.get("accn"))))
        original, latest = entries[0], entries[-1]
        period_type, days = classify_period(start, end)
        form = str(latest.get("form") or "")
        facts.append(
            {
                "metric": metric,
                "concept": concept,
                "taxonomy": taxonomy,
                "label": label,
                "unit": unit,
                "value": float(latest["val"]),
                "period_start": start,
                "period_end": end,
                "duration_days": days,
                "period_type": period_type,
                # Fiscal labels come from the filing that first reported this period;
                # later filings re-report it as a comparative under their own labels.
                "fiscal_year": original.get("fy"),
                "fiscal_period": str(original.get("fp") or ""),
                "form": form,
                "accession": str(latest.get("accn") or ""),
                "filed": str(latest.get("filed") or ""),
                "amended": form.endswith("/A"),
                "restated": float(latest["val"]) != float(original["val"]),
                "original_value": float(original["val"]),
                "original_accession": str(original.get("accn") or ""),
                "first_filed": str(original.get("filed") or ""),
            }
        )
    return facts


def _derive_fiscal_q4(facts: list[XbrlFact]) -> list[XbrlFact]:
    """Derive fiscal Q4 as annual minus nine-month year-to-date, when Q4 was not reported.

    Issuers file no Q4 10-Q, so the fourth quarter only exists inside the 10-K total.
    The derivation is recorded (formula, input accessions and periods) so it can be
    audited and never passes for a reported figure.
    """
    quarter_ends = {f["period_end"] for f in facts if f["period_type"] == "quarter"}
    derived: list[XbrlFact] = []
    for annual in (f for f in facts if f["period_type"] == "annual"):
        if annual["period_end"] in quarter_ends:
            continue
        nine_month = [
            f
            for f in facts
            if f["period_type"] == "year_to_date"
            and f["unit"] == annual["unit"]
            and f["period_start"] == annual["period_start"]
            and 250 <= int(f.get("duration_days") or 0) <= 290
            and f["period_end"] < annual["period_end"]
        ]
        if not nine_month:
            continue
        ytd = max(nine_month, key=lambda f: f["period_end"])
        q4_start = (_parse_date(ytd["period_end"]) + timedelta(days=1)).isoformat()
        period_type, days = classify_period(q4_start, annual["period_end"])
        if period_type != "quarter":
            continue
        value = annual["value"] - ytd["value"]
        original = annual["original_value"] - ytd["original_value"]
        derived.append(
            {
                **annual,
                "value": value,
                "period_start": q4_start,
                "duration_days": days,
                "period_type": "quarter",
                "fiscal_period": "Q4",
                "restated": value != original,
                "original_value": original,
                "first_filed": max(annual["first_filed"], ytd["first_filed"]),
                "derived": True,
                "formula": "annual - nine_month_ytd",
                "input_accessions": [annual["accession"], ytd["accession"]],
                "input_periods": [
                    f"{annual['period_start']}..{annual['period_end']}",
                    f"{ytd['period_start']}..{ytd['period_end']}",
                ],
            }
        )
    return derived


def _select_recent(facts: list[XbrlFact], periods: int) -> list[XbrlFact]:
    selected: list[XbrlFact] = []
    for period_type, count in (("quarter", periods), ("instant", periods), ("annual", 1)):
        matching = sorted(
            (f for f in facts if f["period_type"] == period_type),
            key=lambda f: f["period_end"],
            reverse=True,
        )
        selected.extend(matching[:count])
    if not selected:
        # Only year-to-date figures exist; return them labeled rather than nothing.
        selected = sorted(facts, key=lambda f: f["period_end"], reverse=True)[:periods]
    return selected


def describe_fact(fact: XbrlFact) -> str:
    """One-line statement of a fact with its period, unit and filing provenance."""
    value = fact["value"]
    unit = fact.get("unit") or ""
    if unit == "USD" and abs(value) >= 1e6:
        amount = f"${value / 1e6:,.1f}M"
    elif unit.startswith("USD/"):
        amount = f"${value:,.2f}/share"
    else:
        amount = f"{value:,.0f} {unit}".strip()
    if fact["period_type"] == "instant":
        period = f"as of {fact['period_end']}"
    else:
        period = f"{fact['period_type'].replace('_', '-')} {fact.get('period_start')} to {fact['period_end']}"
    text = (
        f"{fact['label']} ({fact['concept']}) was {amount} for the {period} "
        f"(FY{fact.get('fiscal_year')} {fact.get('fiscal_period')}; first filed {fact.get('first_filed')}"
    )
    if fact.get("derived"):
        text += f"; derived as {fact.get('formula')} from {', '.join(fact.get('input_accessions') or [])})"
    else:
        text += f"; latest value from {fact.get('form')} filed {fact.get('filed')})"
    if fact.get("restated"):
        text += f"; originally reported as {fact['original_value']:,.0f} in {fact.get('original_accession')}"
    return text + "."
