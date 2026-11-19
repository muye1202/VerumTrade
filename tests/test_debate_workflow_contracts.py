from __future__ import annotations

from verumtrade.agents.trader.decision_brief import build_trader_plan_v1
from verumtrade.agents.utils.agent_runtime.agent_states import AgentState
from verumtrade.graph.debate_schema import (
    debate_context_ids_from_state,
    debate_validation_context_from_state,
    extract_research_debate_turns_from_text,
    frame_contested_issues,
    require_risk_response_contract,
    require_valid_research_turns,
    validate_thesis_ledger,
    validate_research_debate_turns,
    validate_trader_plan,
)
from verumtrade.graph.decision_diff import build_decision_diff
from verumtrade.graph.evidence_ledger_schema import (
    build_evidence_ledger,
    rank_critical_evidence,
    validate_admissible_evidence,
)
from verumtrade.graph.plan_patch_schema import validate_plan_patches
from verumtrade.graph.structured_schemas import (
    EvidenceItem,
    FinalDecisionTrace,
    PlanPatch,
    ResearchDebateTurn,
    ThesisLedger,
    TraderPlan,
    schema_for_structured_output,
)


def _state() -> dict:
    return {
        "company_of_interest": "MU",
        "trade_date": "2026-05-11",
        "time_horizon": "1-2 weeks",
        "evidence_graph": {
            "facts": [
                {
                    "id": "fact_market_001",
                    "domain": "market",
                    "claim": "RSI is 74, indicating overbought short-term momentum.",
                    "source": "get_indicators",
                    "section": "technical_indicators",
                    "as_of": "2026-05-11T14:30:00Z",
                    "confidence": 0.82,
                    "quality": "normal",
                    "source_type": "vendor",
                },
                {
                    "id": "fact_news_001",
                    "domain": "news",
                    "claim": "Customer order timing remains unconfirmed.",
                    "source": "news_bundle",
                    "section": "company_news",
                    "as_of": "",
                    "confidence": 0.61,
                    "quality": "normal",
                    "source_type": "vendor",
                },
            ],
            "inferences": [
                {
                    "id": "inf_market_001",
                    "domain": "market",
                    "claim": "Immediate entry should wait for confirmation.",
                    "stance": "bearish",
                    "confidence": 0.78,
                    "depends_on": ["fact_market_001"],
                    "falsifier": "High-volume close above resistance.",
                }
            ],
            "audit_issues": [],
        },
        "execution_plan_compiler": {
            "recommended_action": "HOLD",
            "recommended_execution_intent": "WAIT_FOR_TRIGGER",
            "order_type": "MARKET",
            "position_size_pct": None,
            "stop_loss": 113.98,
            "take_profit": 136.41,
            "canonical_json_contract": {
                "decision_version": "v2",
                "ticker": "MU",
                "action": "HOLD",
                "execution_intent": "wait_for_trigger",
                "plan_mode": "conditional",
                "time_horizon": "1-2 weeks",
            },
        },
        "trader_decision_brief": {
            "top_supporting_evidence": [],
            "top_opposing_evidence": [
                {"source_ids": ["inf_market_001", "fact_market_001"]}
            ],
            "hard_constraints": ["event_risk_rating:HIGH"],
        },
    }


def test_evidence_ledger_validates_and_ranks_critical_items():
    ledger = build_evidence_ledger(_state())
    report = validate_admissible_evidence(ledger, time_horizon="1-2 weeks")
    ranked = rank_critical_evidence(ledger)

    accepted_ids = set(report["accepted_evidence_ids"])
    rejected_ids = {item["evidence_id"] for item in report["rejected_evidence"]}

    assert ledger[0]["evidence_id"] == "E-MKT-001"
    assert ledger[0]["source_ref"] == "fact_market_001"
    assert "prefer_wait_for_trigger" in ledger[0]["supports"]
    assert "E-MKT-001" in accepted_ids
    assert "E-NEW-001" in rejected_ids
    assert ranked[0]["evidence_id"] == "E-MKT-001"
    assert ranked[0]["criticality"] >= 0.45


def test_structured_schema_models_are_closed_for_model_outputs():
    schema = schema_for_structured_output(TraderPlan)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["rationale_links"]["additionalProperties"]["items"]["type"] == "string"

    required_models = [
        EvidenceItem,
        ResearchDebateTurn,
        ThesisLedger,
        TraderPlan,
        PlanPatch,
        FinalDecisionTrace,
    ]
    for model in required_models:
        assert model.model_config["extra"] == "forbid"

    invalid_plan = {
        "plan_id": "trader_plan_v1",
        "action": "BUY",
        "execution_mode": "act_now",
        "order_type": "MARKET",
        "position_size_pct": 0.05,
        "entry_condition": "Enter now.",
        "stop_loss": "Below support.",
        "take_profit": "Trim at resistance.",
        "rationale_links": {
            "action": ["E-MKT-001"],
            "execution_mode": ["E-MKT-001"],
            "order_type": ["E-MKT-001"],
            "position_size_pct": ["E-MKT-001"],
            "entry_condition": ["E-MKT-001"],
            "stop_loss": ["E-MKT-001"],
            "take_profit": ["E-MKT-001"],
        },
        "unexpected": "not allowed",
    }

    try:
        TraderPlan.model_validate(invalid_plan)
    except Exception as exc:
        assert "unexpected" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("TraderPlan accepted an extra field")


def test_admissibility_rejects_stale_and_downgrades_contradicted_evidence():
    ledger = [
        {
            "evidence_id": "E-MKT-001",
            "ticker": "MU",
            "source_agent": "market_analyst",
            "source_tool": "get_indicators",
            "source_ref": "fact_old",
            "observed_at": "2026-05-01T14:30:00Z",
            "claim": "Old RSI reading was bullish.",
            "fact_type": "technical_indicator",
            "polarity": "bullish",
            "time_horizon": "1-2 weeks",
            "confidence": 0.8,
            "materiality": 0.8,
            "supports": ["supports_long_bias"],
            "contradicts": [],
        },
        {
            "evidence_id": "E-MKT-002",
            "ticker": "MU",
            "source_agent": "market_analyst",
            "source_tool": "get_indicators",
            "source_ref": "fact_fresh_a",
            "observed_at": "2026-06-03T14:30:00Z",
            "claim": "Fresh RSI supports long bias.",
            "fact_type": "technical_indicator",
            "polarity": "bullish",
            "time_horizon": "1-2 weeks",
            "confidence": 0.7,
            "materiality": 0.7,
            "supports": ["supports_long_bias"],
            "contradicts": ["prefer_wait_for_trigger"],
        },
        {
            "evidence_id": "E-MKT-003",
            "ticker": "MU",
            "source_agent": "market_analyst",
            "source_tool": "get_indicators",
            "source_ref": "fact_fresh_b",
            "observed_at": "2026-06-04T14:30:00Z",
            "claim": "Fresher RSI argues to wait.",
            "fact_type": "technical_indicator",
            "polarity": "bearish",
            "time_horizon": "1-2 weeks",
            "confidence": 0.9,
            "materiality": 0.9,
            "supports": ["prefer_wait_for_trigger"],
            "contradicts": ["supports_long_bias"],
        },
    ]

    report = validate_admissible_evidence(
        ledger,
        time_horizon="1-2 weeks",
        as_of_date="2026-06-04T15:00:00Z",
    )

    assert {
        item["evidence_id"] for item in report["rejected_evidence"]
    } == {"E-MKT-001"}
    assert {
        item["evidence_id"] for item in report["downgraded_evidence"]
    } == {"E-MKT-002"}
    assert report["accepted_evidence_ids"] == ["E-MKT-003"]


def test_agent_state_declares_debate_upgrade_channels_for_langgraph_persistence():
    required = {
        "evidence_ledger",
        "admissibility_report",
        "critical_evidence_ids",
        "contested_issues",
        "research_debate_turns",
        "research_debate_validation",
        "thesis_ledger",
        "thesis_ledger_validation",
        "trader_plan_v1",
        "risk_patches",
        "risk_patch_validation",
        "decision_diff",
    }

    assert required.issubset(set(AgentState.__annotations__))


def test_debate_context_ids_rebuild_from_evidence_graph_when_state_channels_missing():
    state = _state()
    state.pop("evidence_ledger", None)
    state.pop("contested_issues", None)

    evidence_ids, issue_ids = debate_context_ids_from_state(state)

    assert "E-MKT-001" in evidence_ids
    assert "I-001" in issue_ids


def test_research_turn_validation_normalizes_graph_ids_to_ledger_ids():
    state = _state()
    context = debate_validation_context_from_state(state)
    text = """
    RESEARCH_DEBATE_TURN_JSON
    {
      "turn_id": 1,
      "speaker": "Bull Analyst",
      "issue_id": "SNDK_invest",
      "position": "long",
      "claim": "Wait for confirmation.",
      "evidence_ids": ["inf_market_001", "fact_market_001"],
      "plan_implication": {
        "field": "execution_mode",
        "proposed_value": "wait_for_trigger",
        "rationale": "Avoid chasing overbought momentum."
      },
      "falsification_condition": "High-volume close above resistance.",
      "confidence": 0.72
    }
    """

    result = require_valid_research_turns(
        text,
        stage="bull_researcher",
        evidence_ids=context["evidence_ids"],
        active_issue_ids=context["issue_ids"],
        evidence_aliases=context["evidence_aliases"],
        active_issues=context["issues"],
    )

    accepted = result["accepted_turns"][0]
    assert accepted["issue_id"] == "I-001"
    assert accepted["evidence_ids"] == ["E-MKT-001"]


def test_research_debate_turn_requires_valid_evidence_issue_and_plan_implication():
    ledger = build_evidence_ledger(_state())
    turns = [
        {
            "turn_id": "BEAR-001",
            "speaker": "bear_researcher",
            "issue_id": "I-001",
            "position": "WAIT_FOR_TRIGGER",
            "claim": "Immediate entry is not justified.",
            "evidence_ids": ["E-MKT-001"],
            "rebuttal_to": "BULL-001",
            "plan_implication": {
                "field": "execution_mode",
                "proposed_value": "wait_for_trigger",
                "rationale": "Avoid chasing overbought momentum.",
            },
            "falsification_condition": "High-volume close above resistance.",
            "confidence": 0.72,
        },
        {
            "turn_id": "BULL-001",
            "speaker": "bull_researcher",
            "issue_id": "I-404",
            "claim": "Buy now.",
            "evidence_ids": [],
            "plan_implication": {"field": "action", "proposed_value": "BUY"},
        },
    ]

    result = validate_research_debate_turns(
        turns,
        evidence_ids=[item["evidence_id"] for item in ledger],
        active_issue_ids=["I-001"],
    )

    assert result["accepted_turns"][0]["turn_id"] == "BEAR-001"
    assert result["rejected_turns"][0]["reason"] == "missing evidence_ids"


def test_research_debate_turn_validator_rejects_schema_extra_fields():
    turns = [
        {
            "turn_id": "BEAR-001",
            "speaker": "bear_researcher",
            "issue_id": "I-001",
            "position": "WAIT_FOR_TRIGGER",
            "claim": "Immediate entry is not justified.",
            "evidence_ids": ["E-MKT-001"],
            "rebuttal_to": "BULL-001",
            "plan_implication": {
                "field": "execution_mode",
                "proposed_value": "wait_for_trigger",
                "rationale": "Avoid chasing overbought momentum.",
            },
            "falsification_condition": "High-volume close above resistance.",
            "confidence": 0.72,
            "extra_commentary": "not part of the contract",
        }
    ]

    result = validate_research_debate_turns(
        turns,
        evidence_ids=["E-MKT-001"],
        active_issue_ids=["I-001"],
    )

    assert result["accepted_turns"] == []
    assert "schema validation failed" in result["rejected_turns"][0]["reason"]


def test_contested_issue_framing_groups_critical_evidence_by_decision_field():
    ledger = build_evidence_ledger(_state())
    report = validate_admissible_evidence(ledger, time_horizon="1-2 weeks")
    issues = frame_contested_issues(ledger, report)

    assert issues[0]["issue_id"] == "I-001"
    assert "ACT_NOW" in issues[0]["question"]
    assert issues[0]["candidate_evidence"] == ["E-MKT-001"]
    assert "execution_mode" in issues[0]["decision_fields_at_risk"]
    assert "position_size_pct" in issues[0]["decision_fields_at_risk"]


def test_extract_research_debate_turns_from_text_reads_contract_json():
    text = """
    The setup should wait.
    RESEARCH_DEBATE_TURN_JSON
    {
      "turn_id": "BEAR-001",
      "speaker": "bear_researcher",
      "issue_id": "I-001",
      "position": "WAIT_FOR_TRIGGER",
      "claim": "Immediate entry is not justified.",
      "evidence_ids": ["E-MKT-001"],
      "plan_implication": {
        "field": "execution_mode",
        "proposed_value": "wait_for_trigger",
        "rationale": "Avoid chasing overbought momentum."
      },
      "falsification_condition": "High-volume close above resistance.",
      "confidence": 0.72
    }
    """

    turns = extract_research_debate_turns_from_text(text)

    assert turns[0]["turn_id"] == "BEAR-001"
    assert turns[0]["evidence_ids"] == ["E-MKT-001"]


def test_thesis_ledger_validation_requires_cited_accepted_claims_and_constraints():
    valid = {
        "winning_thesis": "Cautious bullish setup, only after confirmation.",
        "accepted_claims": [
            {
                "claim_id": "C-001",
                "claim": "Immediate entry should wait.",
                "evidence_ids": ["E-MKT-001"],
                "effect": "prefer_wait_for_trigger",
            }
        ],
        "rejected_claims": [],
        "unresolved_uncertainties": [
            {"uncertainty": "Confirmation quality", "decision_effect": "wait_for_trigger"}
        ],
        "recommended_plan_constraints": {
            "max_size_pct": 0.05,
            "entry_mode": "wait_for_trigger",
            "invalidating_conditions": ["High-volume reversal below support."],
        },
    }
    invalid = {
        "winning_thesis": "Buy now.",
        "accepted_claims": [{"claim_id": "C-002", "claim": "Momentum is good."}],
        "recommended_plan_constraints": {},
    }

    assert validate_thesis_ledger(valid, evidence_ids=["E-MKT-001"])["valid"] is True
    result = validate_thesis_ledger(invalid, evidence_ids=["E-MKT-001"])
    assert result["valid"] is False
    assert "accepted_claims[0] missing evidence_ids" in result["violations"]


def test_trader_plan_v1_has_rationale_links_for_executable_fields():
    plan = build_trader_plan_v1(_state())
    result = validate_trader_plan(plan, evidence_ids=["E-MKT-001"], thesis_ids=[])

    assert plan["plan_id"] == "trader_plan_v1"
    assert plan["execution_mode"] == "wait_for_trigger"
    assert "E-MKT-001" in plan["rationale_links"]["execution_mode"]
    assert result["valid"] is True


def test_trader_plan_validator_rejects_schema_extra_fields():
    plan = build_trader_plan_v1(_state())
    plan["unexpected"] = "not allowed"

    result = validate_trader_plan(plan, evidence_ids=["E-MKT-001"], thesis_ids=[])

    assert result["valid"] is False
    assert any("schema validation failed" in violation for violation in result["violations"])


def test_plan_patch_validation_and_decision_diff_show_material_impact():
    trader_plan = build_trader_plan_v1(_state())
    patches = [
        {
            "patch_id": "P-SAFE-001",
            "author": "safe_analyst",
            "target_plan_version": "trader_plan_v1",
            "patch_type": "modify",
            "field": "position_size_pct",
            "old_value": None,
            "new_value": 0.04,
            "evidence_ids": ["E-MKT-001"],
            "reason": "Overbought conditions limit starter size.",
            "expected_effect": "reduce_drawdown_risk",
            "materiality": "high",
        }
    ]

    validation = validate_plan_patches(
        patches,
        trader_plan=trader_plan,
        evidence_ids=["E-MKT-001"],
    )
    diff = build_decision_diff(
        trader_plan,
        {
            "action": "HOLD",
            "execution_intent": "wait_for_trigger",
            "position_size_pct": 0.04,
            "order_type": "MARKET",
        },
        accepted_patch_ids=["P-SAFE-001"],
        rejected_patches=[],
    )

    assert validation[0]["valid"] is True
    assert diff["decision_diff"]["from_trader_plan"]["position_size_pct"] is None
    assert diff["decision_diff"]["to_final_decision"]["position_size_pct"] == 0.04
    assert diff["accepted_patches"] == ["P-SAFE-001"]


def test_plan_patch_validator_rejects_schema_extra_fields():
    patch = {
        "patch_id": "P-SAFE-001",
        "author": "safe_analyst",
        "target_plan_version": "trader_plan_v1",
        "patch_type": "modify",
        "field": "position_size_pct",
        "old_value": None,
        "new_value": 0.04,
        "evidence_ids": ["E-MKT-001"],
        "reason": "Overbought conditions limit starter size.",
        "expected_effect": "reduce_drawdown_risk",
        "materiality": "high",
        "extra_commentary": "not part of the contract",
    }

    validation = validate_plan_patches(
        [patch],
        trader_plan=build_trader_plan_v1(_state()),
        evidence_ids=["E-MKT-001"],
    )

    assert validation[0]["valid"] is False
    assert "schema validation failed" in validation[0]["reason"]


def test_risk_response_contract_uses_terminal_marker_for_noop_responses():
    text = """
    The risky analyst's PLAN_PATCH would over-size an already crowded move.
    I am rejecting that proposal and leaving the trader plan unchanged.
    NO_MATERIAL_CHANGE
    """

    assert require_risk_response_contract(text, stage="neutral_debator") == text
