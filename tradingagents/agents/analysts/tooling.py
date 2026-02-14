from __future__ import annotations

from typing import Any, Dict


def build_tooling_state_update(
    state: Dict[str, Any],
    analyst_key: str,
    tool_calls_count: int,
) -> Dict[str, Any]:
    """Return state updates for analyst tool-round and tool-call counters."""
    tool_round_counts = dict(
        state.get("tool_round_counts")
        or state.get("tool_call_counts")
        or {}
    )
    tool_calls_issued_by_agent = dict(state.get("tool_calls_issued_by_agent") or {})

    tool_call_total = int(state.get("tool_call_total", 0) or 0)
    tool_calls_issued_total = int(state.get("tool_calls_issued_total", 0) or 0)

    if tool_calls_count > 0:
        tool_round_counts[analyst_key] = int(tool_round_counts.get(analyst_key, 0) or 0) + 1
        tool_call_total += 1
        tool_calls_issued_by_agent[analyst_key] = (
            int(tool_calls_issued_by_agent.get(analyst_key, 0) or 0) + int(tool_calls_count)
        )
        tool_calls_issued_total += int(tool_calls_count)

    return {
        # Preferred naming
        "tool_round_counts": tool_round_counts,
        # Backward-compatible alias used by existing results/log readers
        "tool_call_counts": dict(tool_round_counts),
        "tool_call_total": tool_call_total,
        "tool_calls_issued_by_agent": tool_calls_issued_by_agent,
        "tool_calls_issued_total": tool_calls_issued_total,
    }

