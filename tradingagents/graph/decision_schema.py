from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

DECISION_VERSION = "v1"

_ALLOWED_ACTIONS = {"BUY", "SELL", "HOLD"}
_ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"}
_ALLOWED_TIF = {"DAY", "GTC"}
_ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_NA_STRINGS = {"N/A", "NA", "NONE", "-"}


def extract_decision_json_block(text: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Extract and JSON-decode a canonical decision block from free-form text.

    Expected format:
      BEGIN_DECISION_JSON
      { ... }
      END_DECISION_JSON
    """
    if not text:
        return None, "Missing decision text."

    pattern = r"BEGIN_DECISION_JSON\s*(\{.*?\})\s*END_DECISION_JSON"
    matches = list(re.finditer(pattern, str(text), flags=re.DOTALL | re.IGNORECASE))
    if not matches:
        return None, "Missing BEGIN_DECISION_JSON/END_DECISION_JSON block."
    # If multiple blocks exist, use the last one as the most recent canonical decision.
    m = matches[-1]

    raw = m.group(1).strip()
    try:
        parsed = json.loads(raw)
    except Exception as e:
        return None, f"Invalid decision JSON: {type(e).__name__}: {e}"

    if not isinstance(parsed, dict):
        return None, "Decision JSON must be an object."
    return parsed, None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.upper() in _NA_STRINGS:
            raise ValueError("String placeholders like N/A are not allowed in canonical JSON.")
        return float(s)
    raise ValueError(f"Expected numeric or null, got {type(value).__name__}.")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Quantity must be integer or null.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if int(value) != value:
            raise ValueError("Quantity must be an integer.")
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.upper() in _NA_STRINGS:
            raise ValueError("String placeholders like N/A are not allowed in canonical JSON.")
        if not re.fullmatch(r"[+-]?\d+", s):
            raise ValueError("Quantity must be an integer.")
        return int(s)
    raise ValueError("Quantity must be integer or null.")


def validate_structured_decision(
    decision: Dict[str, Any],
    *,
    expected_ticker: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate and normalize canonical decision JSON.
    Returns (normalized_decision, error).
    """
    if not isinstance(decision, dict):
        return None, "Decision payload must be an object."

    norm: Dict[str, Any] = {}
    try:
        action = str(decision.get("action", "")).strip().upper()
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("action must be one of BUY/SELL/HOLD.")
        norm["action"] = action

        ticker = str(decision.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        if expected_ticker and ticker != str(expected_ticker).strip().upper():
            raise ValueError(f"ticker mismatch: expected {expected_ticker}, got {ticker}.")
        norm["ticker"] = ticker

        norm["quantity"] = _int_or_none(decision.get("quantity"))

        order_type = str(decision.get("order_type", "")).strip().upper().replace("-", "_").replace(" ", "_")
        if order_type not in _ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be one of MARKET/LIMIT/STOP/STOP_LIMIT/TRAILING_STOP.")
        norm["order_type"] = order_type

        tif = str(decision.get("time_in_force", "")).strip().upper().replace("-", "_").replace(" ", "_")
        if tif not in _ALLOWED_TIF:
            raise ValueError("time_in_force must be DAY or GTC.")
        norm["time_in_force"] = tif

        eh = decision.get("extended_hours")
        if eh is not None and not isinstance(eh, bool):
            raise ValueError("extended_hours must be boolean or null.")
        norm["extended_hours"] = eh

        norm["limit_price"] = _num(decision.get("limit_price"))
        norm["stop_price"] = _num(decision.get("stop_price"))
        norm["trail_percent"] = _num(decision.get("trail_percent"))
        norm["trail_price"] = _num(decision.get("trail_price"))
        norm["stop_loss"] = _num(decision.get("stop_loss"))
        norm["take_profit"] = _num(decision.get("take_profit"))
        norm["position_size_pct"] = _num(decision.get("position_size_pct"))

        if norm["stop_loss"] is None or norm["take_profit"] is None:
            raise ValueError("stop_loss and take_profit are required numeric fields.")

        if norm["position_size_pct"] is not None:
            p = float(norm["position_size_pct"])
            if p > 1.0 and p <= 100.0:
                p = p / 100.0
            if not (0.0 < p <= 1.0):
                raise ValueError("position_size_pct must be in (0,1] or (0,100].")
            norm["position_size_pct"] = p

        th = decision.get("time_horizon")
        if th is not None:
            th = str(th).strip()
        norm["time_horizon"] = th or None

        conf = decision.get("confidence")
        if conf is not None:
            conf_u = str(conf).strip().upper()
            if conf_u not in _ALLOWED_CONFIDENCE:
                raise ValueError("confidence must be HIGH/MEDIUM/LOW or null.")
            conf = conf_u
        norm["confidence"] = conf

        rationale = decision.get("rationale")
        if rationale is not None:
            rationale = str(rationale).strip()
        norm["rationale"] = rationale or None

        version = str(decision.get("decision_version", "")).strip() or DECISION_VERSION
        if version != DECISION_VERSION:
            raise ValueError(f"Unsupported decision_version '{version}'.")
        norm["decision_version"] = version

        if norm["action"] == "BUY":
            if norm["quantity"] is None and norm["position_size_pct"] is None:
                raise ValueError("BUY requires quantity or position_size_pct.")
        if norm["action"] == "SELL":
            if norm["quantity"] is None:
                raise ValueError("SELL requires explicit quantity.")

        if norm["order_type"] == "LIMIT":
            if norm["limit_price"] is None:
                raise ValueError("LIMIT requires limit_price.")
        elif norm["order_type"] == "STOP":
            if norm["stop_price"] is None:
                raise ValueError("STOP requires stop_price.")
        elif norm["order_type"] == "STOP_LIMIT":
            if norm["stop_price"] is None or norm["limit_price"] is None:
                raise ValueError("STOP_LIMIT requires both stop_price and limit_price.")
        elif norm["order_type"] == "TRAILING_STOP":
            tp = norm["trail_percent"]
            tr = norm["trail_price"]
            if (tp is None and tr is None) or (tp is not None and tr is not None):
                raise ValueError("TRAILING_STOP requires exactly one of trail_percent or trail_price.")

    except Exception as e:
        return None, str(e)

    return norm, None
