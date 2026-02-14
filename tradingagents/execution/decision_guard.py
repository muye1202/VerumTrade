from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tradingagents.graph.decision_schema import (
    extract_decision_json_block,
    validate_structured_decision,
)


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    if s.upper() in {"N/A", "NA", "NONE", "-"}:
        return None
    m = re.search(r"([-+]?\d[\d,]*\.?\d*)", s.replace("%", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def extract_last_close_from_market_report(market_report: str) -> Optional[float]:
    text = str(market_report or "")
    m = re.search(r"Last close:\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def extract_analysis_price_hint(market_report: str) -> Optional[float]:
    text = str(market_report or "")
    matches = re.findall(r"price[^0-9]{0,20}\$?\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        return float(matches[0])
    except Exception:
        return None


def build_market_snapshot(
    *,
    symbol: str,
    market_report: str = "",
    quote: Optional[Dict[str, Any]] = None,
    structured_decision: Optional[Dict[str, Any]] = None,
    snapshot_source: str = "executor_quote_first",
) -> Dict[str, Any]:
    bid = _to_float((quote or {}).get("bid_price"))
    ask = _to_float((quote or {}).get("ask_price"))
    quote_ref = _to_float((quote or {}).get("reference_price"))
    quote_source = str((quote or {}).get("source") or "").strip() or None

    quote_mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        quote_mid = (bid + ask) / 2.0
    quote_price = quote_mid or ask or bid or quote_ref

    last_close = extract_last_close_from_market_report(market_report)
    analysis_hint = extract_analysis_price_hint(market_report)

    decision_hint = None
    if isinstance(structured_decision, dict):
        decision_hint = (
            _to_float(structured_decision.get("limit_price"))
            or _to_float(structured_decision.get("stop_price"))
            or _to_float(structured_decision.get("stop_loss"))
            or _to_float(structured_decision.get("take_profit"))
        )

    source_mode = str(snapshot_source or "executor_quote_first").strip().lower()
    reference_price = None
    source = None
    if source_mode == "executor_quote_first":
        if quote_price:
            reference_price = float(quote_price)
            source = quote_source or "alpaca_latest_quote"
        elif last_close:
            reference_price = float(last_close)
            source = "analysis_fallback"
        elif decision_hint:
            reference_price = float(decision_hint)
            source = "analysis_fallback"
    else:
        if last_close:
            reference_price = float(last_close)
            source = "analysis_fallback"
        elif quote_price:
            reference_price = float(quote_price)
            source = quote_source or "alpaca_latest_quote"
        elif decision_hint:
            reference_price = float(decision_hint)
            source = "analysis_fallback"

    return {
        "symbol": str(symbol or "").upper(),
        "asof": datetime.now().isoformat(),
        "source": source or "analysis_fallback",
        "reference_price": reference_price,
        "bid": bid,
        "ask": ask,
        "last_close": last_close,
        "analysis_price_hint": analysis_hint,
    }


def validate_decision_prices(
    *,
    decision: Dict[str, Any],
    market_snapshot: Dict[str, Any],
    band_pct: float,
) -> List[str]:
    ref = _to_float((market_snapshot or {}).get("reference_price"))
    if not ref or ref <= 0:
        return ["missing_reference_price"]

    band = max(0.0, float(band_pct or 0.0)) / 100.0
    action = str((decision or {}).get("action") or "").upper()
    order_type = str((decision or {}).get("order_type") or "").upper()
    values = {
        "limit_price": _to_float((decision or {}).get("limit_price")),
        "stop_price": _to_float((decision or {}).get("stop_price")),
        "stop_loss": _to_float((decision or {}).get("stop_loss")),
        "take_profit": _to_float((decision or {}).get("take_profit")),
    }

    violations: List[str] = []
    for field, v in values.items():
        if v is None:
            continue
        if abs(v - ref) / ref > band:
            violations.append(f"{field}_out_of_band")

    limit_price = values["limit_price"]
    stop_loss = values["stop_loss"]
    take_profit = values["take_profit"]

    if action == "BUY":
        if order_type == "LIMIT" and limit_price is not None and limit_price > ref * (1.0 + band):
            violations.append("buy_limit_above_max_band")
        if stop_loss is not None and stop_loss >= ref:
            violations.append("buy_stop_loss_not_below_ref")
        if take_profit is not None and take_profit <= ref:
            violations.append("buy_take_profit_not_above_ref")
    elif action == "SELL":
        if order_type == "LIMIT" and limit_price is not None and limit_price < ref * (1.0 - band):
            violations.append("sell_limit_below_min_band")
        if stop_loss is not None and stop_loss <= ref:
            violations.append("sell_stop_loss_not_above_ref")
        if take_profit is not None and take_profit >= ref:
            violations.append("sell_take_profit_not_below_ref")

    return sorted(set(violations))


def _repair_prompt(
    *,
    decision_text: str,
    expected_ticker: str,
    market_snapshot: Dict[str, Any],
    validation_error: str,
    price_violations: Sequence[str],
) -> str:
    return (
        "You repair trading decisions into a strict canonical JSON block.\n"
        "Output ONLY:\nBEGIN_DECISION_JSON\n{...}\nEND_DECISION_JSON\n"
        "No markdown, no extra text.\n\n"
        f"EXPECTED TICKER: {expected_ticker}\n"
        f"MARKET SNAPSHOT JSON: {json.dumps(market_snapshot, ensure_ascii=False)}\n"
        f"VALIDATION ERROR: {validation_error}\n"
        f"PRICE VIOLATIONS: {list(price_violations)}\n\n"
        "Constraints:\n"
        "- Keep action intent from the original text when possible.\n"
        "- decision_version must be 'v1'.\n"
        "- Numeric fields must be numbers or null (never strings like N/A).\n"
        "- For BUY/SELL/HOLD, stop_loss and take_profit must be numeric.\n"
        "- LIMIT requires limit_price.\n"
        "- Prices must be coherent with reference_price and violation notes.\n\n"
        f"ORIGINAL DECISION TEXT:\n{decision_text}"
    )


def attempt_repair_canonical_decision(
    *,
    llm: Any,
    decision_text: str,
    expected_ticker: str,
    market_snapshot: Dict[str, Any],
    validation_error: str = "",
    price_violations: Optional[Sequence[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    prompt = _repair_prompt(
        decision_text=decision_text,
        expected_ticker=expected_ticker,
        market_snapshot=market_snapshot,
        validation_error=validation_error or "",
        price_violations=price_violations or [],
    )
    try:
        response = llm.invoke(prompt)
    except Exception as e:
        return None, f"repair_invoke_failed: {type(e).__name__}: {e}", ""

    content = str(getattr(response, "content", "") or "")
    raw, err = extract_decision_json_block(content)
    if err:
        return None, f"repair_parse_failed: {err}", content

    normalized, validation_err = validate_structured_decision(
        raw or {},
        expected_ticker=expected_ticker,
    )
    if validation_err:
        return None, f"repair_validation_failed: {validation_err}", content

    return normalized, None, content
