from __future__ import annotations

import json
from types import SimpleNamespace

from verumtrade.agents.trader.decision_brief import (
    apply_trader_self_audit,
    build_execution_plan_compiler,
    build_scenario_analysis,
    build_trade_setup_diagnosis,
    build_trader_decision_brief,
)
from verumtrade.agents.trader.trader import create_trader
from verumtrade.graph.debate_schema import DebateWorkflowHardFault
from verumtrade.graph.decision_schema import validate_structured_decision
from verumtrade.dataflows.config import get_config, set_config

import pytest


@pytest.fixture
def config_override():
    """Temporarily override global config keys, restoring the previous values afterwards."""
    saved = {}

    def apply(**values):
        current = get_config()
        for key, value in values.items():
            saved.setdefault(key, current.get(key))
        set_config(values)

    yield apply
    set_config(saved)


def _state() -> dict:
    return {
        "company_of_interest": "MU",
        "trade_date": "2026-05-11",
        "time_horizon": "1-2 weeks",
        "investment_plan": "Research manager leans bullish if breakout confirms.",
        "market_report": "RAW MARKET REPORT SHOULD NOT BE PRIMARY TRADER INPUT " * 50,
        "sentiment_report": "sentiment memo",
        "news_report": "news memo",
        "fundamentals_report": "fundamentals memo",
        "catalyst_report": "catalyst prose memo",
        "portfolio_context": (
            "Position: MU 25 shares, cost basis 110.50, unrealized PnL 8.2%, "
            "portfolio weight 6.5%, effective buying power $12,500. Concentration risk: semiconductor sleeve near cap."
        ),
        "market_snapshot": {"symbol": "MU", "reference_price": 121.25},
        "evidence_graph": {
            "facts": [
                {
                    "id": "fact_market_001",
                    "domain": "market",
                    "claim": "Price broke above resistance on 1.9x volume.",
                    "as_of": "2026-05-11",
                    "confidence": 0.83,
                    "quality": "normal",
                    "source": "price_action_summary",
                },
                {
                    "id": "fact_news_001",
                    "domain": "news",
                    "claim": "Customer order timing is still unconfirmed.",
                    "as_of": "2026-05-11",
                    "confidence": 0.62,
                    "quality": "normal",
                    "source": "news_bundle",
                },
            ],
            "inferences": [
                {
                    "id": "inf_market_001",
                    "domain": "market",
                    "claim": "Breakout remains valid but overextended after the gap.",
                    "stance": "bullish",
                    "confidence": 0.81,
                    "depends_on": ["fact_market_001"],
                    "support_fact_ids": ["fact_market_001"],
                    "counter_fact_ids": [],
                    "falsifier": "Close back below the breakout level.",
                },
                {
                    "id": "inf_news_001",
                    "domain": "news",
                    "claim": "Unconfirmed customer timing adds execution risk.",
                    "stance": "bearish",
                    "confidence": 0.66,
                    "depends_on": ["fact_news_001"],
                    "support_fact_ids": ["fact_news_001"],
                    "counter_fact_ids": [],
                    "falsifier": "",
                },
            ],
            "audit_issues": [
                {"code": "domain_without_observations", "domain": "sentiment", "severity": "medium"}
            ],
        },
        "catalyst_event_report_structured": {
            "event_risk_rating": "HIGH",
            "recommended_action": "freeze_new_buys",
            "near_term_catalysts": ["Expected earnings date this week."],
            "risk_controls": ["Do not add before earnings confirmation."],
            "data_quality_notes": ["Options-flow source unavailable."],
        },
    }


def test_build_trader_decision_brief_tags_evidence_and_constraints():
    brief = build_trader_decision_brief(_state())

    assert brief["ticker"] == "MU"
    assert brief["reference_price"] == 121.25
    assert brief["current_position"]["has_position"] is True
    assert brief["current_position"]["shares"] == 25
    assert brief["evidence_by_domain"]["market"][0]["direction"] == "bullish"
    assert brief["evidence_by_domain"]["market"][0]["decision_relevance"] == "entry"
    assert brief["evidence_by_domain"]["news"][0]["direction"] == "bearish"
    assert brief["top_supporting_evidence"][0]["source_ids"] == ["inf_market_001", "fact_market_001"]
    assert "Options-flow source unavailable." in brief["missing_or_stale_data"]
    assert any("freeze_new_buys" in item for item in brief["hard_constraints"])


def test_build_trade_setup_diagnosis_waits_when_bullish_setup_is_overextended_and_frozen():
    brief = build_trader_decision_brief(_state())
    diagnosis = build_trade_setup_diagnosis(brief)

    assert diagnosis["primary_setup"] == "BREAKOUT_CONFIRMATION"
    assert diagnosis["setup_quality"] == "C"
    assert diagnosis["setup_requires_trigger"] is True
    assert diagnosis["trigger_type"] == "event"
    assert diagnosis["entry_status"] == "overextended"
    assert any("freeze_new_buys" in reason for reason in diagnosis["why_this_setup"])


def test_build_scenario_analysis_connects_target_invalidation_and_reward_risk():
    brief = build_trader_decision_brief(_state())
    diagnosis = build_trade_setup_diagnosis(brief)
    scenario = build_scenario_analysis(brief, diagnosis)

    assert scenario["bull_case"]["target_price"] == 136.41
    assert scenario["bear_case"]["invalidation_price"] == 113.98
    assert scenario["dominant_risk"] == "Catalyst/event risk is HIGH; recommended action is freeze_new_buys."
    assert scenario["risk_reward_estimate"] == 2.09
    assert scenario["asymmetric_payoff"] is True
    assert "inf_market_001" in scenario["bull_case"]["evidence"]
    assert "catalyst_event_report_structured" in scenario["bear_case"]["evidence"]


def test_execution_plan_compiler_forces_conditional_hold_when_setup_is_not_actionable():
    brief = build_trader_decision_brief(_state())
    diagnosis = build_trade_setup_diagnosis(brief)
    scenario = build_scenario_analysis(brief, diagnosis)
    compiler = build_execution_plan_compiler(
        decision_brief=brief,
        setup_diagnosis=diagnosis,
        scenario_analysis=scenario,
        market_snapshot=_state()["market_snapshot"],
        market_session_context="REGULAR MARKET SESSION: open",
        portfolio_context=_state()["portfolio_context"],
        catalyst_event_report_structured=_state()["catalyst_event_report_structured"],
    )

    assert compiler["recommended_decision_version"] == "v2"
    assert compiler["recommended_execution_intent"] == "WAIT_FOR_TRIGGER"
    assert compiler["recommended_action"] == "HOLD"
    assert compiler["order_type"] == "MARKET"
    assert compiler["stop_loss"] == 113.98
    assert compiler["take_profit"] == 136.41
    assert compiler["position_size_pct"] is None
    assert "setup quality C prefers HOLD/WAIT_FOR_TRIGGER" in compiler["compiler_checks"]
    assert "catalyst override freeze_new_buys applied" in compiler["compiler_checks"]
    parsed, err = validate_structured_decision(
        compiler["canonical_json_contract"], expected_ticker="MU"
    )
    assert err is None
    assert parsed["decision_version"] == "v2"


def test_trader_self_audit_repairs_obvious_compiler_contradictions():
    brief = build_trader_decision_brief(_state())
    diagnosis = build_trade_setup_diagnosis(brief)
    scenario = build_scenario_analysis(brief, diagnosis)
    compiler = build_execution_plan_compiler(
        decision_brief=brief,
        setup_diagnosis=diagnosis,
        scenario_analysis=scenario,
        market_snapshot=_state()["market_snapshot"],
        market_session_context="REGULAR MARKET SESSION: open",
        portfolio_context=_state()["portfolio_context"],
        catalyst_event_report_structured=_state()["catalyst_event_report_structured"],
    )
    compiler["recommended_action"] = "BUY"
    compiler["recommended_execution_intent"] = "ACT_NOW"
    compiler["recommended_decision_version"] = "v1"
    compiler["position_size_pct"] = 0.2
    compiler["order_type"] = "LIMIT"
    compiler["limit_price"] = 180.0

    audit, repaired = apply_trader_self_audit(
        decision_brief=brief,
        setup_diagnosis=diagnosis,
        scenario_analysis=scenario,
        execution_plan_compiler=compiler,
        market_snapshot=_state()["market_snapshot"],
        market_session_context="REGULAR MARKET SESSION: open",
        catalyst_event_report_structured=_state()["catalyst_event_report_structured"],
    )

    assert audit["passed"] is False
    assert audit["final_action_consistent"] is True
    assert repaired["recommended_action"] == "HOLD"
    assert repaired["recommended_execution_intent"] == "WAIT_FOR_TRIGGER"
    assert repaired["recommended_decision_version"] == "v2"
    assert repaired["order_type"] == "MARKET"
    assert repaired["limit_price"] is None
    assert repaired["position_size_pct"] is None
    assert any("BUY blocked by setup diagnosis" in item for item in audit["violations"])
    assert any("catalyst override blocked BUY" in item for item in audit["repairs_applied"])


def test_trader_prompt_uses_structured_brief_and_setup_not_raw_report_bundle(config_override):
    # No swing thesis in this state, so the 2.09 risk/reward comes from the setup-template
    # heuristic, which only runs when explicitly configured (default is thesis-driven).
    config_override(thesis_compiler={**(get_config().get("thesis_compiler") or {}), "scenario_mode": "legacy_heuristic"})

    class RecordingLLM:
        def __init__(self) -> None:
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return SimpleNamespace(content="Trader plan\n\nFINAL TRANSACTION PROPOSAL:\n- ACTION: HOLD")

    class RecordingMemory:
        def __init__(self) -> None:
            self.query = None

        def get_memories(self, query, n_matches=2):
            self.query = query
            return []

    llm = RecordingLLM()
    memory = RecordingMemory()
    result = create_trader(llm, memory)(_state())

    prompt_text = "\n\n".join(message["content"] for message in llm.messages)
    assert "TRADER DECISION BRIEF JSON" in prompt_text
    assert "TRADE SETUP DIAGNOSIS JSON" in prompt_text
    assert "SCENARIO ANALYSIS JSON" in prompt_text
    assert "EXECUTION PLAN COMPILER JSON" in prompt_text
    assert "TRADER SELF-AUDIT JSON" in prompt_text
    assert "RAW MARKET REPORT SHOULD NOT BE PRIMARY TRADER INPUT" not in prompt_text
    assert json.loads(memory.query)["primary_setup"] == "BREAKOUT_CONFIRMATION"
    assert result["trader_decision_brief"]["ticker"] == "MU"
    assert result["trade_setup_diagnosis"]["entry_status"] == "overextended"
    assert result["scenario_analysis"]["risk_reward_estimate"] == 2.09
    assert result["execution_plan_compiler"]["recommended_execution_intent"] == "WAIT_FOR_TRIGGER"
    assert result["trader_self_audit"]["final_action_consistent"] is True


def test_trader_hard_faults_when_trader_plan_fails_validator(monkeypatch, config_override):
    # Intermediate gates are soft by default; the hard fault is the strict-mode contract.
    config_override(debate_soft_intermediate_gates=False)

    class FailingIfInvokedLLM:
        def invoke(self, _messages):
            raise AssertionError("Trader LLM should not be invoked after plan validation fails")

    class EmptyMemory:
        def get_memories(self, *_args, **_kwargs):
            return []

    def invalid_plan(_state):
        plan = {
            "plan_id": "trader_plan_v1",
            "action": "HOLD",
            "execution_mode": "wait_for_trigger",
            "order_type": "MARKET",
            "position_size_pct": None,
            "entry_condition": "Wait for trigger.",
            "stop_loss": 113.98,
            "take_profit": 136.41,
            "rationale_links": {},
        }
        return plan

    monkeypatch.setattr(
        "verumtrade.agents.trader.trader.build_trader_plan_v1",
        invalid_plan,
    )

    node = create_trader(FailingIfInvokedLLM(), EmptyMemory())

    try:
        node(_state())
    except DebateWorkflowHardFault as exc:
        assert exc.stage == "trader"
        assert "invalid trader_plan_v1" in exc.reason
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Trader accepted invalid trader_plan_v1")


def test_trader_prompt_uses_section_6_prompt_redesign_without_legacy_prompt_text():
    class RecordingLLM:
        def __init__(self) -> None:
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return SimpleNamespace(content="Trader plan\n\nFINAL TRANSACTION PROPOSAL:\n- ACTION: HOLD")

    class EmptyMemory:
        def get_memories(self, query, n_matches=2):
            return []

    llm = RecordingLLM()
    create_trader(llm, EmptyMemory())(_state())
    system_prompt = llm.messages[0]["content"]
    user_prompt = llm.messages[1]["content"]
    combined = f"{system_prompt}\n{user_prompt}"

    assert "You are the portfolio-aware Trader in an agentic stock analysis system." in system_prompt
    assert "Follow this sequence exactly:" in system_prompt
    assert "1. Diagnose setup type." in system_prompt
    assert "7. Run the self-audit checklist." in system_prompt
    assert "Never BUY only because the thesis is bullish" in system_prompt
    assert "TRADER_REASONING_SUMMARY:" in system_prompt
    assert "- MEMORY_LESSON_USED:" in system_prompt
    assert "FINAL TRANSACTION PROPOSAL:" in system_prompt
    assert "professional trading agent analyzing market data" not in combined
    assert "Based on a comprehensive analysis by a team of analysts" not in combined
    assert "Leverage these insights to make an informed and strategic decision" not in combined
