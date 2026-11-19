from __future__ import annotations

import pytest
from types import SimpleNamespace

from verumtrade.agents.managers.research_manager import create_research_manager
from verumtrade.agents.managers.risk_manager import create_risk_manager
from verumtrade.agents.researchers.bull_researcher import create_bull_researcher
from verumtrade.agents.risk_mgmt.conservative_debator import create_safe_debator
from verumtrade.agents.utils.agent_runtime.evidence_graph import create_evidence_graph_node
from verumtrade.graph.verumtrade_graph import VerumtradeGraph
from verumtrade.graph.signal_processing import SignalProcessor
from verumtrade.graph.debate_schema import DebateWorkflowHardFault
from verumtrade.dataflows.config import get_config, set_config


@pytest.fixture(autouse=True)
def strict_debate_gates():
    """These tests cover strict mode: intermediate gates are soft by default (71c9a42),
    so a contract violation only raises when debate_soft_intermediate_gates is off."""
    previous = get_config().get("debate_soft_intermediate_gates", True)
    set_config({"debate_soft_intermediate_gates": False})
    yield
    set_config({"debate_soft_intermediate_gates": previous})


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, *_args, **_kwargs):
        return SimpleNamespace(content=self.content)


class EmptyMemory:
    def get_memories(self, *_args, **_kwargs):
        return []


def _base_state() -> dict:
    return {
        "company_of_interest": "MU",
        "trade_date": "2026-05-11",
        "time_horizon": "1-2 weeks",
        "investment_debate_state": {
            "history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_debate_state": {
            "history": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "count": 0,
        },
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
        "catalyst_report": "",
        "portfolio_context": "",
        "market_session_context": "REGULAR MARKET SESSION: open",
        "trader_investment_plan": "FINAL TRANSACTION PROPOSAL:\n- EXECUTION_INTENT: WAIT_FOR_TRIGGER",
        "evidence_graph": {"facts": [], "inferences": [], "audit_issues": []},
        "evidence_ledger": [
            {
                "evidence_id": "E-MKT-001",
                "source_ref": "fact_market_001",
                "ticker": "MU",
                "source_agent": "market_analyst",
                "observed_at": "2026-05-11",
                "fact_type": "technical",
                "polarity": "neutral",
                "time_horizon": "1-2 weeks",
                "confidence": 0.7,
                "materiality": 0.6,
                "contradicts": [],
                "claim": "RSI is overbought.",
                "supports": ["prefer_wait_for_trigger"],
                "criticality": 0.67,
            }
        ],
        "contested_issues": [
            {
                "issue_id": "I-001",
                "question": "Act now or wait?",
                "candidate_evidence": ["E-MKT-001"],
                "decision_fields_at_risk": ["execution_mode"],
            }
        ],
        "research_debate_turns": [],
        "market_snapshot": {"symbol": "MU", "reference_price": 100.0},
    }


def test_bull_researcher_hard_faults_when_turn_json_is_missing():
    node = create_bull_researcher(FakeLLM("Generic bullish prose only."), EmptyMemory())

    with pytest.raises(DebateWorkflowHardFault, match="bull_researcher.*RESEARCH_DEBATE_TURN_JSON"):
        node(_base_state())


def test_research_manager_hard_faults_when_thesis_ledger_is_invalid():
    node = create_research_manager(
        FakeLLM(
            """
            Recommendation: HOLD.
            THESIS_LEDGER_JSON
            {"winning_thesis":"Cautious", "accepted_claims":[{"claim_id":"C-001"}], "recommended_plan_constraints":{}}
            """
        ),
        EmptyMemory(),
    )

    with pytest.raises(DebateWorkflowHardFault, match="research_manager.*accepted_claims\\[0\\] missing evidence_ids"):
        node(_base_state())


def test_research_manager_normalizes_legacy_thesis_ledger_shape():
    state = _base_state()
    state["research_debate_turns"] = [
        {
            "turn_id": "BULL-001",
            "speaker": "bull_researcher",
            "issue_id": "I-001",
            "position": "WAIT_FOR_TRIGGER",
            "claim": "Overbought momentum argues for waiting on confirmation.",
            "evidence_ids": ["E-MKT-001"],
            "plan_implication": {
                "field": "execution_mode",
                "proposed_value": "wait_for_trigger",
                "rationale": "Avoid chasing stretched technicals.",
            },
            "falsification_condition": "High-volume close above resistance.",
            "confidence": 0.72,
        }
    ]
    node = create_research_manager(
        FakeLLM(
            """
            Recommendation: HOLD until confirmation.
            THESIS_LEDGER_JSON
            {
              "winning_thesis": "Cautious confirmation setup.",
              "accepted_claims": [
                {
                  "claim": "Overbought momentum argues for waiting on confirmation.",
                  "evidence_ids": ["E-MKT-001"]
                }
              ],
              "rejected_claims": [],
              "unresolved_uncertainties": [],
              "recommended_plan_constraints": {}
            }
            """
        ),
        EmptyMemory(),
    )

    result = node(state)

    accepted_claim = result["thesis_ledger"]["accepted_claims"][0]
    assert accepted_claim["claim_id"] == "C-001"
    assert accepted_claim["effect"] == "execution_mode=wait_for_trigger"
    assert result["thesis_ledger"]["recommended_plan_constraints"] == {
        "execution_mode": "wait_for_trigger"
    }


def test_research_manager_normalizes_thesis_ledger_source_refs_to_evidence_ids():
    state = _base_state()
    state["evidence_ledger"] = [
        {
            "evidence_id": "E-MKT-001",
            "source_ref": "fact_market_price_action_summary_001",
            "ticker": "MU",
            "source_agent": "market_analyst",
            "observed_at": "2026-05-11",
            "fact_type": "technical",
            "polarity": "neutral",
            "time_horizon": "1-2 weeks",
            "confidence": 0.7,
            "materiality": 0.6,
            "contradicts": [],
            "source_node_id": "fact_market_price_action_summary_001",
            "claim": "ATR is elevated.",
            "supports": ["reduce_entry_aggression"],
            "criticality": 0.72,
        },
        {
            "evidence_id": "E-MKT-015",
            "source_ref": "fact_market_obs_004_recovery",
            "ticker": "MU",
            "source_agent": "market_analyst",
            "observed_at": "2026-05-11",
            "fact_type": "technical",
            "polarity": "neutral",
            "time_horizon": "1-2 weeks",
            "confidence": 0.7,
            "materiality": 0.6,
            "contradicts": [],
            "source_node_id": "fact_market_obs_004_recovery",
            "claim": "Price is far above the 200 SMA.",
            "supports": ["prefer_wait_for_trigger"],
            "criticality": 0.81,
        },
    ]
    node = create_research_manager(
        FakeLLM(
            """
            Recommendation: WAIT_FOR_TRIGGER.
            THESIS_LEDGER_JSON
            {
              "winning_thesis": "Momentum is real but entry risk is stretched.",
              "accepted_claims": [
                {
                  "claim_id": "C-001",
                  "claim": "Elevated volatility and extreme deviation require a trigger.",
                  "evidence_ids": [
                    "fact_market_obs_004_recovery",
                    "fact_market_price_action_summary_001"
                  ],
                  "effect": "execution_mode=wait_for_trigger"
                }
              ],
              "rejected_claims": [],
              "unresolved_uncertainties": [],
              "recommended_plan_constraints": {
                "execution_mode": "wait_for_trigger"
              }
            }
            """
        ),
        EmptyMemory(),
    )

    result = node(state)

    assert result["thesis_ledger_validation"]["valid"] is True
    assert result["thesis_ledger"]["accepted_claims"][0]["evidence_ids"] == [
        "E-MKT-015",
        "E-MKT-001",
    ]


def test_safe_debator_hard_faults_when_patch_contract_is_missing():
    node = create_safe_debator(FakeLLM("This plan feels risky, be careful."))

    with pytest.raises(DebateWorkflowHardFault, match="safe_debator.*PLAN_PATCH"):
        node(_base_state())


def test_final_decision_hard_faults_when_canonical_json_is_missing():
    graph = VerumtradeGraph.__new__(VerumtradeGraph)
    graph.config = {"decision_snapshot_source": "executor_quote_first"}
    graph.signal_processor = SignalProcessor(FakeLLM("HOLD"))

    with pytest.raises(DebateWorkflowHardFault, match="risk_manager.*BEGIN_DECISION_JSON"):
        graph._attach_canonical_decision(
            {
                **_base_state(),
                "final_trade_decision": "Final answer: HOLD, but no canonical block.",
            },
            expected_ticker="MU",
        )


def test_evidence_graph_hard_faults_when_all_analyst_evidence_is_invalid():
    node = create_evidence_graph_node()

    with pytest.raises(DebateWorkflowHardFault, match="analyst_validator.*no admissible analyst evidence"):
        node(
            {
                **_base_state(),
                "trade_date": "2026-06-04",
                "time_horizon": "1-2 weeks",
                "evidence_source_facts": [
                    {
                        "id": "fact_market_001",
                        "domain": "market",
                        "claim": "RSI is overbought.",
                        "source": "get_indicators",
                        "section": "technical_indicators",
                        "as_of": "",
                        "confidence": 0.82,
                        "quality": "normal",
                        "source_type": "vendor",
                    }
                ],
                "market_ledger": {
                    "observations": [
                        {
                            "id": "obs_market_001",
                            "claim": "RSI is overbought.",
                            "source_fact_ids": ["fact_market_001"],
                        }
                    ],
                    "active_hypotheses": [
                        {
                            "id": "hyp_market_001",
                            "claim": "Wait for confirmation.",
                            "stance": "bearish",
                            "confidence": 0.78,
                            "support_observation_ids": ["obs_market_001"],
                        }
                    ],
                },
            }
        )


def test_risk_manager_applies_valid_plan_patches_before_synthesis():
    class RecordingLLM:
        def __init__(self) -> None:
            self.prompt = ""

        def invoke(self, prompt, **_kwargs):
            self.prompt = str(prompt)
            return SimpleNamespace(
                content="""
                Risk judge accepts the safe patch.
                DECISION_DIFF
                FROM_TRADER_PLAN: position_size_pct 0.10
                TO_FINAL_DECISION: position_size_pct 0.04
                ACCEPTED_PATCHES: ["P-SAFE-001"]
                REJECTED_PATCHES: []
                NO_MATERIAL_CHANGE_REASON: null
                BEGIN_DECISION_JSON
                {
                  "action": "BUY",
                  "ticker": "MU",
                  "quantity": null,
                  "order_type": "LIMIT",
                  "time_in_force": "DAY",
                  "extended_hours": false,
                  "limit_price": 101.0,
                  "stop_price": null,
                  "trail_percent": null,
                  "trail_price": null,
                  "stop_loss": 95.0,
                  "take_profit": 115.0,
                  "position_size_pct": 0.04,
                  "time_horizon": "1-2 weeks",
                  "confidence": "MEDIUM",
                  "rationale": "Patch-reduced starter size.",
                  "decision_version": "v1",
                  "execution_intent": "act_now",
                  "override_reason": null,
                  "rationale_evidence_ids": ["E-MKT-001"],
                  "accepted_patches": ["P-SAFE-001"],
                  "rejected_patches": [],
                  "no_material_change_reason": null
                }
                END_DECISION_JSON
                """
            )

    state = {
        **_base_state(),
        "trader_plan_v1": {
            "plan_id": "trader_plan_v1",
            "action": "BUY",
            "execution_mode": "act_now",
            "order_type": "LIMIT",
            "position_size_pct": 0.10,
            "entry_condition": "Enter now.",
            "stop_loss": 95.0,
            "take_profit": 115.0,
            "rationale_links": {
                "action": ["E-MKT-001"],
                "execution_mode": ["E-MKT-001"],
                "order_type": ["E-MKT-001"],
                "position_size_pct": ["E-MKT-001"],
                "entry_condition": ["E-MKT-001"],
                "stop_loss": ["E-MKT-001"],
                "take_profit": ["E-MKT-001"],
            },
        },
        "evidence_ledger": [
            {
                "evidence_id": "E-MKT-001",
                "ticker": "MU",
                "source_agent": "market_analyst",
                "source_tool": "get_indicators",
                "source_ref": "fact_market_001",
                "observed_at": "2026-05-11T14:30:00Z",
                "claim": "RSI is overbought.",
                "fact_type": "technical_indicator",
                "polarity": "bearish",
                "time_horizon": "1-2 weeks",
                "confidence": 0.82,
                "materiality": 0.70,
                "supports": ["reduce_entry_aggression"],
                "contradicts": ["full_size_entry"],
            }
        ],
        "risk_debate_state": {
            **_base_state()["risk_debate_state"],
            "history": """
            Safe Analyst:
            PLAN_PATCH
            {
              "patch_id": "P-SAFE-001",
              "author": "safe_analyst",
              "target_plan_version": "trader_plan_v1",
              "patch_type": "modify",
              "field": "position_size_pct",
              "old_value": 0.10,
              "new_value": 0.04,
              "evidence_ids": ["E-MKT-001"],
              "reason": "Overbought risk requires smaller starter size.",
              "expected_effect": "reduce_drawdown_risk",
              "materiality": "high"
            }
            """,
        },
    }
    llm = RecordingLLM()
    result = create_risk_manager(llm, EmptyMemory())(state)

    assert result["trader_plan_v1"]["position_size_pct"] == 0.04
    assert result["risk_patch_validation"][0]["valid"] is True
    assert '"position_size_pct": 0.04' in llm.prompt
    assert "RISK PATCH VALIDATION JSON" in llm.prompt


def test_final_decision_hard_faults_when_trace_contract_fields_are_missing():
    graph = VerumtradeGraph.__new__(VerumtradeGraph)
    graph.config = {"decision_snapshot_source": "executor_quote_first"}
    graph.signal_processor = SignalProcessor(FakeLLM("HOLD"))

    with pytest.raises(DebateWorkflowHardFault, match="final decision contract"):
        graph._attach_canonical_decision(
            {
                **_base_state(),
                "trader_plan_v1": {"plan_id": "trader_plan_v1", "action": "HOLD"},
                "final_trade_decision": """
                BEGIN_DECISION_JSON
                {
                  "action": "HOLD",
                  "ticker": "MU",
                  "quantity": null,
                  "order_type": "MARKET",
                  "time_in_force": "DAY",
                  "extended_hours": false,
                  "limit_price": null,
                  "stop_price": null,
                  "trail_percent": null,
                  "trail_price": null,
                  "stop_loss": 95.0,
                  "take_profit": 115.0,
                  "position_size_pct": null,
                  "time_horizon": "1-2 weeks",
                  "confidence": "MEDIUM",
                  "rationale": "No order.",
                  "decision_version": "v1",
                  "execution_intent": "act_now",
                  "override_reason": null
                }
                END_DECISION_JSON
                """,
            },
            expected_ticker="MU",
        )
