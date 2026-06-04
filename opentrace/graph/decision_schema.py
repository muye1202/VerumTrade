from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DECISION_VERSION_V1 = "v1"
DECISION_VERSION_V2 = "v2"
DECISION_VERSION_DEFAULT = DECISION_VERSION_V1

_ALLOWED_ACTIONS = {"BUY", "SELL", "HOLD"}
_ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"}
_ALLOWED_TIF = {"DAY", "GTC"}
_ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_NA_STRINGS = {"N/A", "NA", "NONE", "-"}
_ALLOWED_PLAN_MODES = {"IMMEDIATE", "CONDITIONAL"}
_ALLOWED_SESSIONS = {"ANY", "PREMARKET", "MARKET_HOURS", "AFTERHOURS", "OVERNIGHT", "WEEKEND"}
_ALLOWED_EXECUTION_INTENTS = {"ACT_NOW", "WAIT_FOR_TRIGGER"}
_FINAL_TRACE_FIELDS = (
    "rationale_evidence_ids",
    "accepted_patches",
    "rejected_patches",
    "no_material_change_reason",
)


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


def _date_or_none(value: Any, *, field: str) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        parsed = datetime.strptime(s, "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        raise ValueError(f"{field} must be ISO date YYYY-MM-DD.")


def _normalize_confidence(value: Any) -> Optional[str]:
    if value is None:
        return None
    conf_u = str(value).strip().upper()
    if conf_u not in _ALLOWED_CONFIDENCE:
        raise ValueError("confidence must be HIGH/MEDIUM/LOW or null.")
    return conf_u


def _normalize_action_template(
    template: Dict[str, Any],
    *,
    ticker: str,
    require_action: bool = True,
) -> Dict[str, Any]:
    norm: Dict[str, Any] = {}

    action = str(template.get("action", "")).strip().upper()
    if require_action and action not in _ALLOWED_ACTIONS:
        raise ValueError("action must be one of BUY/SELL/HOLD.")
    norm["action"] = action if action in _ALLOWED_ACTIONS else "HOLD"
    norm["ticker"] = ticker

    norm["quantity"] = _int_or_none(template.get("quantity"))

    order_type = (
        str(template.get("order_type", ""))
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if order_type not in _ALLOWED_ORDER_TYPES:
        raise ValueError("order_type must be one of MARKET/LIMIT/STOP/STOP_LIMIT/TRAILING_STOP.")
    norm["order_type"] = order_type

    tif = (
        str(template.get("time_in_force", ""))
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if tif not in _ALLOWED_TIF:
        raise ValueError("time_in_force must be DAY or GTC.")
    norm["time_in_force"] = tif

    eh = template.get("extended_hours")
    if eh is not None and not isinstance(eh, bool):
        raise ValueError("extended_hours must be boolean or null.")
    norm["extended_hours"] = eh

    norm["limit_price"] = _num(template.get("limit_price"))
    norm["stop_price"] = _num(template.get("stop_price"))
    norm["trail_percent"] = _num(template.get("trail_percent"))
    norm["trail_price"] = _num(template.get("trail_price"))
    norm["stop_loss"] = _num(template.get("stop_loss"))
    norm["take_profit"] = _num(template.get("take_profit"))
    norm["position_size_pct"] = _num(template.get("position_size_pct"))

    if norm["stop_loss"] is None or norm["take_profit"] is None:
        raise ValueError("stop_loss and take_profit are required numeric fields.")

    if norm["position_size_pct"] is not None:
        p = float(norm["position_size_pct"])
        if p > 1.0 and p <= 100.0:
            p = p / 100.0
        if not (0.0 < p <= 1.0):
            raise ValueError("position_size_pct must be in (0,1] or (0,100].")
        norm["position_size_pct"] = p

    th = template.get("time_horizon")
    if th is not None:
        th = str(th).strip()
    norm["time_horizon"] = th or None

    norm["confidence"] = _normalize_confidence(template.get("confidence"))

    rationale = template.get("rationale")
    if rationale is not None:
        rationale = str(rationale).strip()
    norm["rationale"] = rationale or None

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

    return norm


def _normalize_execution_intent(value: Any) -> str:
    intent = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if intent not in _ALLOWED_EXECUTION_INTENTS:
        raise ValueError("execution_intent must be one of act_now or wait_for_trigger.")
    return intent.lower()


def _normalize_override_reason(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _attach_final_trace_fields(norm: Dict[str, Any], decision: Dict[str, Any]) -> None:
    for field in _FINAL_TRACE_FIELDS:
        if field in decision:
            norm[field] = decision.get(field)


def validate_final_decision_contract(decision: Dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if not isinstance(decision, dict):
        return ["final decision must be an object"]
    for field in _FINAL_TRACE_FIELDS:
        if field not in decision:
            violations.append(f"{field} is required")
    rationale_ids = decision.get("rationale_evidence_ids")
    if "rationale_evidence_ids" in decision and (
        not isinstance(rationale_ids, list)
        or not all(isinstance(item, str) and item.strip() for item in rationale_ids)
    ):
        violations.append("rationale_evidence_ids must be a non-empty list of strings")
    accepted = decision.get("accepted_patches")
    if "accepted_patches" in decision and not isinstance(accepted, list):
        violations.append("accepted_patches must be a list")
    rejected = decision.get("rejected_patches")
    if "rejected_patches" in decision and not isinstance(rejected, list):
        violations.append("rejected_patches must be a list")
    reason = decision.get("no_material_change_reason")
    if "no_material_change_reason" in decision and reason is not None and not str(reason).strip():
        violations.append("no_material_change_reason must be null or non-empty")
    if isinstance(accepted, list) and not accepted and reason is None:
        violations.append("no_material_change_reason is required when no patches are accepted")
    return violations


def validate_v1_decision(
    decision: Dict[str, Any],
    *,
    expected_ticker: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(decision, dict):
        return None, "Decision payload must be an object."

    norm: Dict[str, Any] = {}
    try:
        action = str(decision.get("action", "")).strip().upper()
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("action must be one of BUY/SELL/HOLD.")
        ticker = str(decision.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        if expected_ticker and ticker != str(expected_ticker).strip().upper():
            raise ValueError(f"ticker mismatch: expected {expected_ticker}, got {ticker}.")

        norm = _normalize_action_template(decision, ticker=ticker, require_action=True)
        version = str(decision.get("decision_version", "")).strip() or DECISION_VERSION_V1
        if version != DECISION_VERSION_V1:
            raise ValueError(f"Unsupported decision_version '{version}'.")
        norm["decision_version"] = version
        norm["execution_intent"] = _normalize_execution_intent(decision.get("execution_intent"))
        if norm["execution_intent"] != "act_now":
            raise ValueError("v1 requires execution_intent=act_now.")
        norm["override_reason"] = _normalize_override_reason(decision.get("override_reason"))
        _attach_final_trace_fields(norm, decision)
    except Exception as e:
        return None, str(e)

    return norm, None


def _normalize_branch_conditions(branch: Dict[str, Any]) -> Dict[str, Any]:
    conditions = branch.get("conditions")
    if conditions is None:
        conditions = {}
    if not isinstance(conditions, dict):
        raise ValueError("conditions must be an object when provided.")
    out: Dict[str, Any] = {}

    price = conditions.get("price")
    if price is not None:
        if not isinstance(price, dict):
            raise ValueError("conditions.price must be an object.")
        price_out: Dict[str, Any] = {
            "last_price": _num(price.get("last_price")),
            "close_above": _num(price.get("close_above")),
            "close_below": _num(price.get("close_below")),
            "tolerance_pct": _num(price.get("tolerance_pct")),
        }
        if (
            price_out["close_above"] is not None
            and price_out["close_below"] is not None
            and float(price_out["close_above"]) >= float(price_out["close_below"])
        ):
            raise ValueError("conditions.price has contradictory range: close_above must be less than close_below.")
        if price_out["tolerance_pct"] is not None and float(price_out["tolerance_pct"]) < 0:
            raise ValueError("conditions.price.tolerance_pct must be >= 0.")
        out["price"] = price_out

    volume = conditions.get("volume")
    if volume is not None:
        if not isinstance(volume, dict):
            raise ValueError("conditions.volume must be an object.")
        vmin = _num(volume.get("volume_ratio_min"))
        if vmin is not None and float(vmin) <= 0:
            raise ValueError("conditions.volume.volume_ratio_min must be > 0.")
        out["volume"] = {"volume_ratio_min": vmin}

    schedule = conditions.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, dict):
            raise ValueError("conditions.schedule must be an object.")
        valid_from = _date_or_none(schedule.get("valid_from"), field="valid_from")
        valid_to = _date_or_none(schedule.get("valid_to"), field="valid_to")
        if valid_from and valid_to and valid_from > valid_to:
            raise ValueError("conditions.schedule valid_from must be <= valid_to.")
        session_constraint = str(schedule.get("session_constraint", "ANY")).strip().upper()
        session_constraint = session_constraint.replace("-", "_").replace(" ", "_")
        if session_constraint not in _ALLOWED_SESSIONS:
            raise ValueError(
                "conditions.schedule.session_constraint must be one of ANY/PREMARKET/MARKET_HOURS/AFTERHOURS/OVERNIGHT/WEEKEND."
            )
        out["schedule"] = {
            "valid_from": valid_from,
            "valid_to": valid_to,
            "session_constraint": session_constraint,
        }

    event_conditions = branch.get("event_conditions")
    if event_conditions is None:
        event_conditions = []
    if not isinstance(event_conditions, list):
        raise ValueError("event_conditions must be an array.")
    events_out: List[Dict[str, Any]] = []
    for idx, item in enumerate(event_conditions):
        if not isinstance(item, dict):
            raise ValueError(f"event_conditions[{idx}] must be an object.")
        event_key = str(item.get("event_key", "")).strip()
        if not event_key:
            raise ValueError(f"event_conditions[{idx}].event_key is required.")
        requires_confirmation = item.get("requires_confirmation")
        if requires_confirmation is None:
            requires_confirmation = True
        if not isinstance(requires_confirmation, bool):
            raise ValueError(f"event_conditions[{idx}].requires_confirmation must be boolean.")
        expected_value = item.get("expected_value")
        if expected_value is not None:
            expected_value = str(expected_value).strip()
        events_out.append(
            {
                "event_key": event_key,
                "requires_confirmation": requires_confirmation,
                "expected_value": expected_value or None,
            }
        )
    out["event_conditions"] = events_out
    return out


def validate_v2_decision(
    decision: Dict[str, Any],
    *,
    expected_ticker: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(decision, dict):
        return None, "Decision payload must be an object."

    norm: Dict[str, Any] = {}
    try:
        ticker = str(decision.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        if expected_ticker and ticker != str(expected_ticker).strip().upper():
            raise ValueError(f"ticker mismatch: expected {expected_ticker}, got {ticker}.")

        version = str(decision.get("decision_version", "")).strip() or DECISION_VERSION_V2
        if version != DECISION_VERSION_V2:
            raise ValueError(f"Unsupported decision_version '{version}'.")
        norm["decision_version"] = version
        norm["ticker"] = ticker
        norm["execution_intent"] = _normalize_execution_intent(decision.get("execution_intent"))
        if norm["execution_intent"] != "wait_for_trigger":
            raise ValueError("v2 requires execution_intent=wait_for_trigger.")
        norm["override_reason"] = _normalize_override_reason(decision.get("override_reason"))

        plan_mode = str(decision.get("plan_mode", "conditional")).strip().upper()
        if plan_mode not in _ALLOWED_PLAN_MODES:
            raise ValueError("plan_mode must be immediate or conditional.")
        norm["plan_mode"] = plan_mode.lower()

        th = decision.get("time_horizon")
        if th is not None:
            th = str(th).strip()
        norm["time_horizon"] = th or None
        norm["confidence"] = _normalize_confidence(decision.get("confidence"))
        rationale = decision.get("rationale")
        if rationale is not None:
            rationale = str(rationale).strip()
        norm["rationale"] = rationale or None

        raw_plan = decision.get("execution_plan")
        if not isinstance(raw_plan, list) or not raw_plan:
            raise ValueError("execution_plan must be a non-empty array.")

        branch_ids: set[str] = set()
        branches: List[Dict[str, Any]] = []
        for i, branch in enumerate(raw_plan):
            if not isinstance(branch, dict):
                raise ValueError(f"execution_plan[{i}] must be an object.")
            branch_id = str(branch.get("branch_id", "")).strip()
            if not branch_id:
                raise ValueError(f"execution_plan[{i}].branch_id is required.")
            if branch_id in branch_ids:
                raise ValueError(f"Duplicate execution_plan.branch_id '{branch_id}'.")
            branch_ids.add(branch_id)

            action_template = branch.get("action_template")
            if not isinstance(action_template, dict):
                raise ValueError(f"execution_plan[{i}].action_template is required.")

            branches.append(
                {
                    "branch_id": branch_id,
                    "priority": int(branch.get("priority", i)),
                    "conditions": _normalize_branch_conditions(branch),
                    "action_template": _normalize_action_template(
                        action_template,
                        ticker=ticker,
                        require_action=True,
                    ),
                }
            )

        branches.sort(key=lambda x: int(x.get("priority", 0)))
        norm["execution_plan"] = branches

        default_action = decision.get("default_action")
        if isinstance(default_action, str):
            default_branch_id = default_action.strip()
            if default_branch_id and default_branch_id not in branch_ids:
                raise ValueError("default_action branch id not found in execution_plan.")
            norm["default_action"] = default_branch_id or None
        elif isinstance(default_action, dict):
            norm["default_action"] = _normalize_action_template(
                default_action,
                ticker=ticker,
                require_action=True,
            )
        elif default_action is None:
            norm["default_action"] = None
        else:
            raise ValueError("default_action must be string, object, or null.")

        immediate_branch_id: Optional[str] = None
        for b in branches:
            cond = b.get("conditions") or {}
            has_price = bool((cond.get("price") or {}).keys()) if isinstance(cond.get("price"), dict) else False
            has_volume = bool((cond.get("volume") or {}).keys()) if isinstance(cond.get("volume"), dict) else False
            schedule = cond.get("schedule") or {}
            has_schedule = bool(schedule.get("valid_from") or schedule.get("valid_to")) or (
                str(schedule.get("session_constraint", "ANY")).upper() != "ANY"
            )
            has_events = bool(cond.get("event_conditions"))
            if not (has_price or has_volume or has_schedule or has_events):
                immediate_branch_id = b["branch_id"]
                break
        norm["immediate_branch_id"] = immediate_branch_id

        if plan_mode == "IMMEDIATE" and not immediate_branch_id and not isinstance(norm["default_action"], dict):
            raise ValueError("plan_mode=immediate requires an unconditional execution_plan branch or object default_action.")

        action = "HOLD"
        if immediate_branch_id:
            for b in branches:
                if b["branch_id"] == immediate_branch_id:
                    action = str((b.get("action_template") or {}).get("action") or "HOLD").upper()
                    break
        elif isinstance(norm["default_action"], dict):
            action = str(norm["default_action"].get("action") or "HOLD").upper()
        norm["action"] = action if action in _ALLOWED_ACTIONS else "HOLD"
        _attach_final_trace_fields(norm, decision)

    except Exception as e:
        return None, str(e)

    return norm, None


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

    version = str(decision.get("decision_version", "")).strip().lower() or DECISION_VERSION_DEFAULT
    if version == DECISION_VERSION_V1:
        return validate_v1_decision(decision, expected_ticker=expected_ticker)
    if version == DECISION_VERSION_V2:
        return validate_v2_decision(decision, expected_ticker=expected_ticker)
    return None, f"Unsupported decision_version '{version}'."
