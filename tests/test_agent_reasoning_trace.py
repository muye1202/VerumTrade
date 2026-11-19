from __future__ import annotations

import json

from api.utils import build_analysis_reports_payload
from verumtrade.graph.reasoning_trace import build_agent_reasoning_trace
from verumtrade.graph.propagation import Propagator


def _state() -> dict:
    return {
        "company_of_interest": "MU",
        "trade_date": "2026-05-11",
        "time_horizon": "1-2 weeks",
        "trader_decision_brief": {
            "ticker": "MU",
            "reference_price": 121.25,
            "top_supporting_evidence": [{"claim": "Breakout held."}],
            "top_opposing_evidence": [{"claim": "Event risk remains high."}],
        },
        "trade_setup_diagnosis": {
            "primary_setup": "BREAKOUT_CONFIRMATION",
            "setup_quality": "C",
            "entry_status": "overextended",
            "setup_requires_trigger": True,
        },
        "scenario_analysis": {
            "dominant_risk": "Event risk",
            "risk_reward_estimate": 2.09,
        },
        "execution_plan_compiler": {
            "recommended_execution_intent": "WAIT_FOR_TRIGGER",
            "recommended_action": "HOLD",
            "compiler_checks": ["setup quality C prefers HOLD/WAIT_FOR_TRIGGER"],
        },
        "trader_self_audit": {
            "passed": False,
            "violations": ["BUY blocked by setup diagnosis."],
            "repairs_applied": ["set action to HOLD"],
            "final_action_consistent": True,
        },
        "trader_investment_plan": "TRADER_REASONING_SUMMARY:\n- SETUP_TYPE: BREAKOUT_CONFIRMATION",
        "risk_debate_state": {"judge_decision": "Risk judge kept HOLD."},
        "final_trade_decision_structured": {"action": "HOLD", "decision_version": "v2"},
        "decision_guard": {"validation_ok": True},
    }


def test_build_agent_reasoning_trace_exports_ordered_structured_stages():
    trace = build_agent_reasoning_trace(_state())

    assert trace["schema_version"] == "agent_reasoning_trace.v1"
    assert trace["ticker"] == "MU"
    assert trace["trade_date"] == "2026-05-11"
    assert trace["status"] == "available"

    stage_ids = [stage["id"] for stage in trace["stages"]]
    assert stage_ids == [
        "trader_decision_brief",
        "trade_setup_diagnosis",
        "scenario_analysis",
        "execution_plan_compiler",
        "trader_self_audit",
        "trader_final_plan",
        "risk_judge",
        "decision_guard",
    ]

    diagnosis_stage = trace["stages"][1]
    assert diagnosis_stage["agent"] == "Trader"
    assert diagnosis_stage["kind"] == "structured_reasoning"
    assert diagnosis_stage["status"] == "available"
    assert diagnosis_stage["content"]["primary_setup"] == "BREAKOUT_CONFIRMATION"
    assert diagnosis_stage["summary"]["setup_quality"] == "C"
    assert diagnosis_stage["summary"]["entry_status"] == "overextended"

    audit_stage = trace["stages"][4]
    assert audit_stage["content"]["violations"] == ["BUY blocked by setup diagnosis."]
    assert audit_stage["summary"]["passed"] is False
    assert audit_stage["summary"]["violations_count"] == 1

    json.dumps(trace)


def test_build_agent_reasoning_trace_marks_missing_stages_without_dropping_contract():
    trace = build_agent_reasoning_trace({"company_of_interest": "MU"})

    assert trace["status"] == "partial"
    assert len(trace["stages"]) == 8
    assert trace["stages"][0]["id"] == "trader_decision_brief"
    assert trace["stages"][0]["status"] == "missing"
    assert trace["stages"][0]["content"] == {}


def test_initial_state_includes_empty_agent_reasoning_trace():
    state = Propagator().create_initial_state("MU", "2026-05-11")

    assert state["agent_reasoning_trace"]["schema_version"] == "agent_reasoning_trace.v1"
    assert state["agent_reasoning_trace"]["status"] == "partial"


def test_api_report_payload_includes_agent_reasoning_trace():
    state = _state()
    state["agent_reasoning_trace"] = build_agent_reasoning_trace(state)

    payload = build_analysis_reports_payload(state)

    assert payload["agent_reasoning_trace"]["stages"][1]["id"] == "trade_setup_diagnosis"
