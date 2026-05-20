from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, TypedDict


class ReasoningStage(TypedDict):
    id: str
    label: str
    agent: str
    kind: str
    status: str
    summary: dict[str, Any]
    content: Any


class AgentReasoningTrace(TypedDict):
    schema_version: str
    ticker: str
    trade_date: str
    time_horizon: str
    status: str
    stages: list[ReasoningStage]


StageSummaryBuilder = Callable[[Any], dict[str, Any]]


def build_agent_reasoning_trace(state: dict[str, Any] | None) -> AgentReasoningTrace:
    """Build a UI/API-safe trace of intermediate agent reasoning artifacts."""
    state = state or {}
    stages = [
        _stage(
            state,
            key="trader_decision_brief",
            label="Trader Decision Brief",
            agent="Trader",
            kind="structured_input",
            summary_builder=_summarize_decision_brief,
        ),
        _stage(
            state,
            key="trade_setup_diagnosis",
            label="Trade Setup Diagnosis",
            agent="Trader",
            kind="structured_reasoning",
            summary_builder=_summarize_setup_diagnosis,
        ),
        _stage(
            state,
            key="scenario_analysis",
            label="Scenario Analysis",
            agent="Trader",
            kind="structured_reasoning",
            summary_builder=_summarize_scenario_analysis,
        ),
        _stage(
            state,
            key="execution_plan_compiler",
            label="Execution Plan Compiler",
            agent="Trader",
            kind="structured_reasoning",
            summary_builder=_summarize_execution_plan_compiler,
        ),
        _stage(
            state,
            key="trader_self_audit",
            label="Trader Self-Audit",
            agent="Trader",
            kind="structured_validation",
            summary_builder=_summarize_trader_self_audit,
        ),
        _stage(
            state,
            key="trader_investment_plan",
            stage_id="trader_final_plan",
            label="Trader Final Plan",
            agent="Trader",
            kind="narrative_output",
            summary_builder=_summarize_trader_plan,
        ),
        _stage(
            state,
            key="risk_debate_state",
            stage_id="risk_judge",
            label="Risk Judge",
            agent="Risk Management",
            kind="debate_state",
            summary_builder=_summarize_risk_debate,
        ),
        _stage(
            state,
            key="decision_guard",
            label="Decision Guard",
            agent="Execution Guard",
            kind="structured_validation",
            summary_builder=_summarize_decision_guard,
        ),
    ]
    has_available = any(stage["status"] == "available" for stage in stages)
    all_available = all(stage["status"] == "available" for stage in stages)
    return {
        "schema_version": "agent_reasoning_trace.v1",
        "ticker": str(state.get("company_of_interest") or ""),
        "trade_date": str(state.get("trade_date") or ""),
        "time_horizon": str(state.get("time_horizon") or ""),
        "status": "available" if all_available else ("partial" if has_available else "partial"),
        "stages": stages,
    }


def empty_agent_reasoning_trace(
    *, ticker: str = "", trade_date: str = "", time_horizon: str = ""
) -> AgentReasoningTrace:
    return build_agent_reasoning_trace(
        {
            "company_of_interest": ticker,
            "trade_date": trade_date,
            "time_horizon": time_horizon,
        }
    )


def _stage(
    state: dict[str, Any],
    *,
    key: str,
    stage_id: str | None = None,
    label: str,
    agent: str,
    kind: str,
    summary_builder: StageSummaryBuilder,
) -> ReasoningStage:
    value = state.get(key)
    available = _has_content(value)
    content = _json_safe_copy(value) if available else {}
    return {
        "id": stage_id or key,
        "label": label,
        "agent": agent,
        "kind": kind,
        "status": "available" if available else "missing",
        "summary": summary_builder(value) if available else {},
        "content": content,
    }


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return bool(value)
    return True


def _json_safe_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe_copy(v) for k, v in deepcopy(value).items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_copy(item) for item in deepcopy(value)]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _summarize_decision_brief(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "ticker": value.get("ticker"),
        "reference_price": value.get("reference_price"),
        "supporting_evidence_count": len(value.get("top_supporting_evidence") or []),
        "opposing_evidence_count": len(value.get("top_opposing_evidence") or []),
        "hard_constraints_count": len(value.get("hard_constraints") or []),
    }


def _summarize_setup_diagnosis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "primary_setup": value.get("primary_setup"),
        "setup_quality": value.get("setup_quality"),
        "entry_status": value.get("entry_status"),
        "setup_requires_trigger": value.get("setup_requires_trigger"),
        "trigger_type": value.get("trigger_type"),
    }


def _summarize_scenario_analysis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "dominant_risk": value.get("dominant_risk"),
        "risk_reward_estimate": value.get("risk_reward_estimate"),
        "asymmetric_payoff": value.get("asymmetric_payoff"),
        "bull_probability": (value.get("bull_case") or {}).get("probability")
        if isinstance(value.get("bull_case"), dict)
        else None,
        "bear_probability": (value.get("bear_case") or {}).get("probability")
        if isinstance(value.get("bear_case"), dict)
        else None,
    }


def _summarize_execution_plan_compiler(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "recommended_execution_intent": value.get("recommended_execution_intent"),
        "recommended_action": value.get("recommended_action"),
        "order_type": value.get("order_type"),
        "confidence": value.get("confidence"),
        "compiler_checks_count": len(value.get("compiler_checks") or []),
    }


def _summarize_trader_self_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "passed": value.get("passed"),
        "violations_count": len(value.get("violations") or []),
        "repairs_count": len(value.get("repairs_applied") or []),
        "final_action_consistent": value.get("final_action_consistent"),
    }


def _summarize_trader_plan(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "has_reasoning_summary": "TRADER_REASONING_SUMMARY:" in text,
        "has_final_transaction_proposal": "FINAL TRANSACTION PROPOSAL:" in text,
        "length": len(text),
    }


def _summarize_risk_debate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "judge_decision_present": bool(value.get("judge_decision")),
        "latest_speaker": value.get("latest_speaker"),
        "round_count": value.get("count"),
    }


def _summarize_decision_guard(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "validation_ok": value.get("validation_ok"),
        "violations_count": len(value.get("violations") or []),
        "mode_selected_by": value.get("mode_selected_by"),
        "final_execution_intent": value.get("final_execution_intent"),
        "mode_overridden": value.get("mode_overridden"),
        "abort_reason": value.get("abort_reason"),
    }
