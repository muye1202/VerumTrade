from __future__ import annotations

from typing import Any, Mapping


def executor_kwargs_from_structured(structured: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Convert a parsed structured decision dict into AlpacaExecutor.execute_signal kwargs.

    Kept in a lightweight module so unit tests can validate the mapping without
    importing CLI modules (which may depend on optional UI dependencies).
    """
    structured = structured or {}
    return {
        "agent_order_type": structured.get("order_type"),
        "agent_time_in_force": structured.get("time_in_force"),
        "agent_extended_hours": structured.get("extended_hours"),
        "agent_stop_price": structured.get("stop_price"),
        "agent_trail_percent": structured.get("trail_percent"),
        "agent_trail_price": structured.get("trail_price"),
    }
