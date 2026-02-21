from __future__ import annotations

import re
from typing import Any, Dict, Optional

from tradingagents.utils.market_session import now_et


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
    patterns = [
        r"\blast close\s*[:|]\s*\$?\s*([0-9]+(?:\.[0-9]+)?)",
        r"\blast close\b[^$\n]{0,48}\$\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bcurrent price\b[^$\n]{0,48}\$\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bsitting at\s*\$([0-9]+(?:\.[0-9]+)?)",
        r"\btrading at\s*~?\$([0-9]+(?:\.[0-9]+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                continue
    return None


def _extract_scored_dollar_amount(text: str) -> Optional[float]:
    candidates = []
    for m in re.finditer(r"\$\s*([0-9]+(?:\.[0-9]+)?)", text):
        try:
            value = float(m.group(1))
        except Exception:
            continue
        start = max(0, m.start() - 64)
        context = text[start : m.start()].lower()
        score = 0
        if "current price" in context:
            score += 6
        if "last close" in context:
            score += 5
        if "sitting at" in context or "trading at" in context:
            score += 4
        if "entry zone" in context:
            score += 2
        if "high" in context or "low" in context:
            score -= 4
        if "%" in context:
            score -= 3
        candidates.append((score, value, m.start()))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[2]))
    return candidates[0][1]


def extract_analysis_price_hint(market_report: str) -> Optional[float]:
    text = str(market_report or "")
    anchored = extract_last_close_from_market_report(text)
    if anchored is not None:
        return anchored
    return _extract_scored_dollar_amount(text)


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
    last_trade = _to_float((quote or {}).get("last_trade_price"))
    quote_ref = _to_float((quote or {}).get("reference_price"))
    quote_source = str((quote or {}).get("source") or "").strip() or None

    quote_mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        quote_mid = (bid + ask) / 2.0
    quote_price = last_trade or quote_mid or ask or bid or quote_ref

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

    anchor_conflict = False
    anchor_conflict_reason = ""
    if isinstance(reference_price, (int, float)) and isinstance(analysis_hint, (int, float)):
        ref = float(reference_price)
        hint = float(analysis_hint)
        if ref > 0 and hint > 0:
            rel_gap = abs(ref - hint) / ref
            if rel_gap > 0.35:
                anchor_conflict = True
                anchor_conflict_reason = (
                    f"reference_price ({ref:.4f}) and analysis_price_hint ({hint:.4f}) "
                    f"diverge by {rel_gap:.1%}"
                )

    return {
        "symbol": str(symbol or "").upper(),
        "asof": now_et().isoformat(),
        "source": source or "analysis_fallback",
        "reference_price": reference_price,
        "bid": bid,
        "ask": ask,
        "last_close": last_close,
        "analysis_price_hint": analysis_hint,
        "price_anchor_conflict": anchor_conflict,
        "price_anchor_conflict_reason": anchor_conflict_reason,
    }
