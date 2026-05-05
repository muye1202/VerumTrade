import json
import sqlite3
from pathlib import Path

import pytest

from tradingagents.agents.utils.agent_runtime.context_budget import (
    build_report_evidence_summary,
    format_analyst_evidence_context,
)
from tradingagents.agents.analysts.discovery_lane import (
    calculate_question_priority,
    count_blocked_tool_call,
    filter_hypotheses_for_caps,
    record_tool_call_links,
    run_coverage_critic,
    select_allowed_question,
    select_question_gated_tools,
)
from tradingagents.agents.analysts.workbench import (
    ANALYST_LEDGER_KEYS,
    build_ledger_evidence_summary,
    build_ledger_report,
    build_workbench_metrics,
    extract_ledger_and_memo,
    finalize_analyst_workbench_output,
    normalize_ledger,
    strip_executable_proposals,
)
from tradingagents.agents.utils.market_data.bundle_tools import (
    format_evidence_bundle,
    select_bundle_first_tools,
)
from tradingagents.graph.propagation import Propagator
from api.utils import build_analysis_reports_payload


class _Tool:
    def __init__(self, name):
        self.name = name


def test_bundle_first_tool_selection_exposes_only_bundle_on_first_round():
    bundle = _Tool("get_market_data_bundle")
    fallback = [_Tool("get_price_action_summary"), _Tool("get_indicators")]

    first_round = select_bundle_first_tools(bundle, fallback, enable_bundle_tools=True, rounds_used=0)
    fallback_round = select_bundle_first_tools(bundle, fallback, enable_bundle_tools=True, rounds_used=1)
    disabled = select_bundle_first_tools(bundle, fallback, enable_bundle_tools=False, rounds_used=0)

    assert [tool.name for tool in first_round] == ["get_market_data_bundle"]
    assert [tool.name for tool in fallback_round] == [
        "get_price_action_summary",
        "get_indicators",
    ]
    assert [tool.name for tool in disabled] == [
        "get_price_action_summary",
        "get_indicators",
    ]


def test_format_evidence_bundle_reduces_raw_sections_and_surfaces_missing_data():
    results = {
        "price_action_summary": "\n".join(
            [
                "## Price-action snapshot",
                "- Last close: 542.21 (prev: 517.16)",
                "- Returns: 5D 9.16% | 1M 47.40%",
                "- ATR(14): 28.94 (5.34% of price)",
                "| 20D high | 545.91 | breakout trigger |",
            ]
        ),
        "intraday_vwap_position": "No intraday data available for MU on 2026-05-04.",
        "raw_statement": "revenue,expense\n" + ("123,456\n" * 2000),
    }

    packet = format_evidence_bundle("Market Data Bundle", "MU", "2026-05-04", results, max_chars=1800)
    data = json.loads(packet)

    assert data["bundle"] == "Market Data Bundle"
    assert data["symbol"] == "MU"
    assert len(packet) <= 1800
    assert any("Last close" in fact["text"] for fact in data["facts"])
    assert any(item["section"] == "intraday_vwap_position" for item in data["missing_data"])
    assert "123,456\n123,456\n123,456" not in packet


def test_report_evidence_summary_strips_final_proposals_and_keeps_decision_evidence():
    report = """I now have all the data needed. Here is the complete report.

# MU Technical Report
- Price at $542.21 is above 10-EMA $500.54 and 50-SMA $425.57.
- ATR is 5.34% of price, so stops need room.
- Risk: no intraday VWAP data is available.

FINAL TRANSACTION PROPOSAL: BUY
"""

    summary = build_report_evidence_summary("market", report, max_chars=700)

    assert "FINAL TRANSACTION PROPOSAL" not in summary
    assert "I now have all the data" not in summary
    assert "Price at $542.21" in summary
    assert "no intraday VWAP data" in summary


def test_format_analyst_evidence_context_prefers_structured_evidence_over_raw_report():
    state = {
        "market_report": "RAW MARKET " * 2000,
        "market_evidence": "Market evidence: trend bullish, invalidation below 500.",
        "sentiment_report": "RAW SENTIMENT " * 2000,
        "news_report": "",
        "fundamentals_report": "",
    }

    context = format_analyst_evidence_context(state, max_chars_per_report=500)

    assert "Market evidence: trend bullish" in context
    assert "RAW MARKET RAW MARKET" not in context
    assert len(context) < 1400


def test_format_analyst_evidence_context_prefers_ledger_when_evidence_missing():
    state = {
        "market_report": "RAW MARKET " * 2000,
        "market_ledger": normalize_ledger(
            "market",
            {
                "observations": [
                    {
                        "claim": "Breakout has weak volume confirmation",
                        "surprise_score": 0.72,
                        "why_it_matters": "Weak confirmation changes breakout quality.",
                        "status": "unexplained",
                    }
                ],
                "active_hypotheses": [
                    {
                        "claim": "Breakout may be liquidity-driven",
                        "origin": "anomaly_generated",
                        "support": ["obs_market_001"],
                        "against": [],
                        "confidence": 0.61,
                        "falsifier": "Follow-through volume normalizes",
                    }
                ],
            },
        ),
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
    }

    context = format_analyst_evidence_context(state, max_chars_per_report=900)

    assert "Origin mix" in context
    assert "anomaly_generated" in context
    assert "Breakout may be liquidity-driven" in context
    assert "RAW MARKET RAW MARKET" not in context


def test_normalize_ledger_adds_required_discovery_lane_keys():
    ledger = normalize_ledger(
        "market",
        {
            "active_hypotheses": [
                {
                    "claim": "Trend continuation remains plausible",
                    "support": ["obs_market_001"],
                    "confidence": 0.7,
                    "falsifier": "Close below 10 EMA",
                }
            ]
        },
    )

    assert set(ANALYST_LEDGER_KEYS).issubset(ledger.keys())
    assert ledger["analyst_domain"] == "market"
    assert ledger["active_hypotheses"][0]["origin"] == "default_prior"
    assert ledger["active_hypotheses"][0]["id"] == "h_market_001"


def test_question_priority_formula_uses_information_gain_and_cost():
    question = {
        "decision_relevance": 0.8,
        "expected_information_gain": 0.5,
        "evidence_surprise": 0.75,
        "estimated_tool_cost": 0.5,
    }

    assert calculate_question_priority(question) == pytest.approx(0.6)


def test_hypothesis_caps_preserve_discovery_generated_explanations():
    hypotheses = [
        {"id": "h1", "claim": "default 1", "origin": "default_prior", "confidence": 0.9, "falsifier": "x"},
        {"id": "h2", "claim": "default 2", "origin": "default_prior", "confidence": 0.8, "falsifier": "x"},
        {"id": "h3", "claim": "default 3", "origin": "default_prior", "confidence": 0.7, "falsifier": "x"},
        {"id": "h4", "claim": "squeeze", "origin": "anomaly_generated", "confidence": 0.6, "falsifier": "x"},
        {"id": "h5", "claim": "pricing stale", "origin": "cross_domain_signal", "confidence": 0.6, "falsifier": "x"},
    ]

    selected = filter_hypotheses_for_caps(hypotheses)

    assert len(selected) == 4
    assert sum(1 for item in selected if item["origin"] == "default_prior") == 2
    assert any(item["origin"] == "anomaly_generated" for item in selected)
    assert any(item["origin"] == "cross_domain_signal" for item in selected)


def test_coverage_critic_flags_high_surprise_unexplained_observations():
    ledger = normalize_ledger(
        "market",
        {
            "observations": [
                {
                    "id": "obs_market_001",
                    "domain": "market",
                    "claim": "Breakout occurred without volume confirmation",
                    "surprise_score": 0.82,
                    "why_it_matters": "It could be a failed breakout.",
                    "status": "unexplained",
                }
            ],
            "active_hypotheses": [
                {
                    "id": "h_market_001",
                    "claim": "Trend continuation",
                    "origin": "default_prior",
                    "support": ["different evidence"],
                    "against": [],
                    "confidence": 0.6,
                    "falsifier": "Close below support",
                }
            ],
        },
    )

    gaps = run_coverage_critic(ledger)

    assert gaps
    assert gaps[0]["severity"] == "high"
    assert gaps[0]["related_observations"] == ["obs_market_001"]


def test_fallback_tools_are_blocked_without_named_unresolved_question():
    fallback = [_Tool("get_price_action_summary"), _Tool("get_indicators")]
    state = {"market_ledger": normalize_ledger("market", {"question_backlog": []})}

    tools, question = select_question_gated_tools(
        state,
        "market",
        fallback,
        rounds_used=1,
    )

    assert tools == []
    assert question is None


def test_valid_tool_calls_are_linked_to_selected_question_ids():
    state = {}
    question = {
        "id": "q_market_001",
        "question": "Is the breakout confirmed by volume?",
        "cheapest_tool": "get_price_action_summary",
    }

    update = record_tool_call_links(
        state,
        "market",
        "get_price_action_summary",
        question,
        tool_calls_count=2,
    )

    assert update["analyst_tool_call_links"]["market"][0]["question_id"] == "q_market_001"
    assert update["analyst_tool_call_links"]["market"][0]["tool_name"] == "get_price_action_summary"
    assert update["analyst_tool_call_links"]["market"][0]["tool_calls_count"] == 2


def test_ledger_evidence_and_report_include_required_sections_and_strip_proposals():
    ledger = normalize_ledger(
        "market",
        {
            "observations": [
                {
                    "id": "obs_market_001",
                    "domain": "market",
                    "claim": "Price is above the 20-day high",
                    "surprise_score": 0.7,
                    "why_it_matters": "Breakout strength affects entry risk.",
                    "status": "explained",
                }
            ],
            "question_backlog": [
                {
                    "id": "q_market_001",
                    "question": "Is the breakout confirmed?",
                    "triggered_by": ["obs_market_001"],
                    "decision_relevance": 0.8,
                    "expected_information_gain": 0.7,
                    "evidence_surprise": 0.7,
                    "estimated_tool_cost": 0.5,
                    "cheapest_tool": "get_price_action_summary",
                    "stop_condition": "Price/volume confirmation reviewed",
                }
            ],
            "active_hypotheses": [
                {
                    "id": "h_market_001",
                    "claim": "Breakout is valid but extended",
                    "origin": "anomaly_generated",
                    "support": ["obs_market_001"],
                    "against": ["ATR elevated"],
                    "confidence": 0.66,
                    "falsifier": "Close back below breakout level",
                }
            ],
            "unexplained_but_decision_relevant": ["ATR expansion could change sizing"],
        },
    )

    report = build_ledger_report("market", ledger, "FINAL TRANSACTION PROPOSAL: BUY")
    evidence = build_ledger_evidence_summary("market", ledger)

    assert "## Domain Inference" in report
    assert "## Active Hypotheses" in report
    assert "## Key Observations" in report
    assert "## Questions Investigated" in report
    assert "## Discarded Explanations" in report
    assert "## Unexplained But Decision-Relevant" in report
    assert "## Watch Items / Falsifiers" in report
    assert "FINAL TRANSACTION PROPOSAL" not in report
    assert "BUY" not in strip_executable_proposals("FINAL TRANSACTION PROPOSAL: BUY")
    assert "Origin mix" in evidence
    assert "anomaly_generated" in evidence


def test_extract_ledger_and_metrics_from_final_output():
    output = """BEGIN_ANALYST_LEDGER_JSON
{"analyst_domain":"news","observations":[{"id":"obs_news_001","domain":"news","claim":"Positive headline faded","surprise_score":0.8,"why_it_matters":"Stale catalyst risk","status":"unexplained"}],"active_hypotheses":[{"id":"h_news_001","claim":"Catalyst priced in","origin":"anomaly_generated","support":["obs_news_001"],"against":[],"confidence":0.64,"falsifier":"fresh follow-through headline"}]}
END_ANALYST_LEDGER_JSON

## Domain Inference
News is supportive but fading.
"""

    ledger, memo = extract_ledger_and_memo("news", output)
    metrics = build_workbench_metrics(ledger)

    assert ledger["analyst_domain"] == "news"
    assert "BEGIN_ANALYST_LEDGER_JSON" not in memo
    assert metrics["observation_count"] == 1
    assert metrics["anomaly_count"] == 1
    assert metrics["hypothesis_origin_counts"]["anomaly_generated"] == 1
    assert metrics["default_prior_pct"] == 0


def test_finalize_invalid_ledger_output_creates_fallback_workbench_structure():
    finalized = finalize_analyst_workbench_output(
        "market",
        """## Domain Inference
AAOI has a strong breakout, but volume and stale current-day data create uncertainty.

## Key Observations
- Price is extended above major moving averages.
- Current-day indicator data is stale because the latest valid trading day is prior day.
- Volume confirmation is mixed.
""",
    )

    ledger = finalized["ledger"]
    report = finalized["report"]

    assert ledger["analyst_domain"] == "market"
    assert len(ledger["observations"]) >= 3
    assert ledger["active_hypotheses"]
    assert ledger["active_hypotheses"][0]["origin"] == "critic_generated"
    assert ledger["active_hypotheses"][0]["falsifier"]
    assert "missing_or_invalid_analyst_ledger_json" in ledger["critic_flags"]
    assert "## Active Hypotheses" in report
    assert "| Structured ledger recovery" in report


def test_initial_graph_state_includes_workbench_tracking_keys():
    state = Propagator().create_initial_state("MU", "2026-05-04")

    assert state["market_ledger"]["analyst_domain"] == "market"
    assert state["sentiment_ledger"]["analyst_domain"] == "sentiment"
    assert state["news_ledger"]["analyst_domain"] == "news"
    assert state["fundamentals_ledger"]["analyst_domain"] == "fundamentals"
    assert state["analyst_tool_call_links"] == {}
    assert state["analyst_tool_call_blocked_counts"] == {}
    assert state["analyst_workbench_metrics"] == {}


def test_history_reports_payload_includes_workbench_fields():
    final_state = {
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
        "market_ledger": normalize_ledger("market"),
        "sentiment_ledger": normalize_ledger("sentiment"),
        "news_ledger": normalize_ledger("news"),
        "fundamentals_ledger": normalize_ledger("fundamentals"),
        "analyst_workbench_metrics": {"market": {"observation_count": 3}},
        "analyst_tool_call_links": {"market": [{"question_id": "q_market_001"}]},
        "analyst_tool_call_blocked_counts": {"market:no_named_open_question": 1},
        "investment_debate_state": {"count": 0},
        "trader_investment_plan": "plan",
        "risk_debate_state": {"count": 0},
        "final_trade_decision": "decision",
    }

    reports = build_analysis_reports_payload(final_state)

    assert reports["market_ledger"]["analyst_domain"] == "market"
    assert reports["analyst_workbench_metrics"]["market"]["observation_count"] == 3
    assert reports["analyst_tool_call_links"]["market"][0]["question_id"] == "q_market_001"
    assert reports["analyst_tool_call_blocked_counts"]["market:no_named_open_question"] == 1


def test_saved_trading_history_reports_can_be_compacted_without_vendor_calls():
    db_path = Path("trading_history.db")
    if not db_path.exists():
        pytest.skip("local trading_history.db not present")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT reports FROM analysis_sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert row is not None

    reports = json.loads(row["reports"])
    state = {
        "market_report": reports.get("market_report", ""),
        "sentiment_report": reports.get("sentiment_report", ""),
        "news_report": reports.get("news_report", ""),
        "fundamentals_report": reports.get("fundamentals_report", ""),
    }

    context = format_analyst_evidence_context(state, max_chars_per_report=900)

    assert len(context) < sum(len(str(state[key])) for key in state)
    assert "FINAL TRANSACTION PROPOSAL" not in context
    assert "Analyst Evidence Context" in context
