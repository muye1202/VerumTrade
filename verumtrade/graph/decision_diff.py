from __future__ import annotations

from typing import Any, TypedDict


DIFF_FIELDS = (
    "action",
    "execution_mode",
    "order_type",
    "entry_price",
    "entry_condition",
    "stop_loss",
    "take_profit",
    "position_size_pct",
    "trigger_condition",
    "time_horizon",
    "invalidation_condition",
)


class FinalDecisionTrace(TypedDict, total=False):
    final_decision_id: str
    decision_diff: dict[str, dict[str, Any]] | None
    accepted_patches: list[str]
    rejected_patches: list[dict[str, Any]]
    no_material_change_reason: str | None


def build_decision_diff(
    trader_plan: dict[str, Any] | None,
    final_decision: dict[str, Any] | None,
    *,
    accepted_patch_ids: list[str] | None = None,
    rejected_patches: list[dict[str, Any]] | None = None,
    no_material_change_reason: str | None = None,
) -> FinalDecisionTrace:
    plan = trader_plan if isinstance(trader_plan, dict) else {}
    final = final_decision if isinstance(final_decision, dict) else {}
    from_plan: dict[str, Any] = {}
    to_final: dict[str, Any] = {}

    for field in DIFF_FIELDS:
        plan_value = _value_for(field, plan)
        final_value = _value_for(field, final)
        if plan_value != final_value:
            from_plan[field] = plan_value
            to_final[field] = final_value

    has_diff = bool(from_plan)
    reason = no_material_change_reason
    if not has_diff and not reason:
        reason = "Final decision made no material change from trader_plan_v1."

    return {
        "final_decision_id": "final_trade_decision",
        "decision_diff": (
            {"from_trader_plan": from_plan, "to_final_decision": to_final}
            if has_diff
            else None
        ),
        "accepted_patches": list(accepted_patch_ids or []),
        "rejected_patches": list(rejected_patches or []),
        "no_material_change_reason": None if has_diff else reason,
    }


def _value_for(field: str, obj: dict[str, Any]) -> Any:
    if field == "execution_mode":
        return (
            obj.get("execution_mode")
            or obj.get("execution_intent")
            or obj.get("recommended_execution_intent")
        )
    if field == "entry_price":
        return obj.get("entry_price") or obj.get("limit_price")
    if field == "trigger_condition":
        return obj.get("trigger_condition") or obj.get("default_action")
    return obj.get(field)
