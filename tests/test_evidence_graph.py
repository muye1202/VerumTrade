import json
from types import SimpleNamespace

from verumtrade.agents.analysts.workbench import normalize_ledger
from verumtrade.agents.utils.agent_runtime.evidence_graph import (
    build_decision_trace,
    build_evidence_graph,
    create_capture_evidence_facts_node,
    extract_evidence_facts_from_messages,
    format_evidence_projection,
    merge_evidence_facts,
)
from verumtrade.agents.utils.market_data.bundle_tools import format_evidence_bundle
from verumtrade.graph.propagation import Propagator
from api.utils import build_analysis_reports_payload


def _market_packet():
    return format_evidence_bundle(
        "Market Data Bundle",
        "MU",
        "2026-05-04",
        {
            "price_action_summary": "\n".join(
                [
                    "## Price-action snapshot",
                    "- Last close: 542.21 above 20D high 545.91 breakout trigger",
                    "- ATR(14): 28.94, elevated risk for chasing",
                    "- Volume expanded 1.8x on the breakout",
                ]
            ),
            "intraday_vwap_position": "No intraday data available for MU on 2026-05-04.",
        },
        max_chars=3000,
    )


def _state_with_vendor_facts():
    facts = json.loads(_market_packet())["facts"]
    return {
        "company_of_interest": "MU",
        "trade_date": "2026-05-04",
        "market_report": "RAW MARKET " * 200,
        "evidence_source_facts": facts,
        "market_ledger": normalize_ledger(
            "market",
            {
                "observations": [
                    {
                        "id": "obs_market_001",
                        "domain": "market",
                        "claim": "Breakout is supported by price action and volume",
                        "source_fact_ids": [facts[0]["id"]],
                        "surprise_score": 0.72,
                        "why_it_matters": "Breakout quality affects entry timing.",
                        "status": "explained",
                    },
                    {
                        "id": "obs_market_002",
                        "domain": "market",
                        "claim": "Unverified options chatter supports upside",
                        "source_fact_ids": [],
                        "surprise_score": 0.64,
                        "why_it_matters": "Unsupported claims should not become source truth.",
                        "status": "unexplained",
                    },
                ],
                "active_hypotheses": [
                    {
                        "id": "h_market_001",
                        "claim": "Breakout remains valid but overextended",
                        "origin": "anomaly_generated",
                        "support": ["obs_market_001"],
                        "against": ["obs_market_002"],
                        "confidence": 0.78,
                        "falsifier": "Close back below the breakout level",
                    }
                ],
            },
        ),
        "sentiment_ledger": normalize_ledger("sentiment"),
        "news_ledger": normalize_ledger("news"),
        "fundamentals_ledger": normalize_ledger("fundamentals"),
    }


def test_bundle_format_emits_stable_vendor_fact_ids_and_keeps_text_alias():
    packet = json.loads(_market_packet())
    first = packet["facts"][0]

    assert first["id"] == "fact_market_price_action_summary_001"
    assert first["domain"] == "market"
    assert first["claim"] == first["text"]
    assert first["source"] == "price_action_summary"
    assert first["section"] == "price_action_summary"
    assert first["as_of"] == "2026-05-04"
    assert 0.0 <= first["confidence"] <= 1.0
    assert packet["missing_data"][0]["section"] == "intraday_vwap_position"


def test_capture_helper_extracts_bundle_facts_from_tool_messages_and_dedupes():
    message = SimpleNamespace(content=_market_packet())

    facts = extract_evidence_facts_from_messages([message])
    merged = merge_evidence_facts(facts, facts)

    assert facts
    assert len(merged) == len(facts)
    assert facts[0]["id"].startswith("fact_market_")
    assert facts[0]["claim"] == facts[0]["text"]


def test_capture_node_updates_source_facts_before_analyst_finalization():
    node = create_capture_evidence_facts_node("market")
    update = node({"messages": [SimpleNamespace(content=_market_packet())]})

    assert update["evidence_source_facts"]
    assert update["evidence_graph"]["facts"] == update["evidence_source_facts"]
    assert update["evidence_graph"]["inferences"] == []


def test_graph_uses_vendor_facts_as_truth_and_flags_unsupported_ledger_claims():
    graph = build_evidence_graph(_state_with_vendor_facts())

    vendor_ids = {fact["id"] for fact in graph["facts"] if fact.get("source_type") == "vendor"}
    recovery = [fact for fact in graph["facts"] if fact.get("source_type") == "ledger_recovery"]
    market_inf = graph["inferences"][0]
    audit_codes = {issue["code"] for issue in graph["audit_issues"]}

    assert "fact_market_price_action_summary_001" in vendor_ids
    assert recovery
    assert recovery[0]["confidence"] == 0.35
    assert "unsupported_ledger_observation" in audit_codes
    assert "fact_market_price_action_summary_001" in market_inf["depends_on"]
    assert recovery[0]["id"] in market_inf["depends_on"]


def test_projections_are_audience_specific_and_do_not_include_raw_reports():
    state = _state_with_vendor_facts()
    state["evidence_graph"] = build_evidence_graph(state)

    bull = format_evidence_projection(state, "bull")
    bear = format_evidence_projection(state, "bear")
    trader = format_evidence_projection(state, "trader")
    risk = format_evidence_projection(state, "risk")

    assert "# Evidence Graph Projection: Bull" in bull
    assert "Bullish inferences" in bull
    assert "RAW MARKET" not in bull
    assert "Weak assumptions" in bear
    assert "Chosen thesis evidence" in trader
    assert "Evidence quality and weakest assumptions" in risk
    assert bull != bear


def test_projection_includes_compact_catalyst_risk_snapshot():
    state = _state_with_vendor_facts()
    state["catalyst_event_report_structured"] = {
        "event_risk_rating": "HIGH",
        "recommended_action": "risk_judge_review",
        "near_term_catalysts": ["Expected Q2 earnings date"],
        "thesis_breaking_events": ["Guidance cut risk"],
        "risk_controls": ["Do not add before verification."],
        "data_quality_notes": ["company_news_raw contaminated, score 0.73"],
    }
    state["catalyst_event_bundle"] = {
        "recent_events": [
            {
                "title": "Intel raises guidance",
                "relevance_score": 0.92,
                "materiality_score": 0.78,
            }
        ],
        "source_quality": {
            "company_news_raw": {"status": "contaminated", "contamination_score": 0.73}
        },
    }

    projection = format_evidence_projection(state, "bear")

    assert "CATALYST RISK SNAPSHOT" in projection
    assert "- rating: HIGH" in projection
    assert "- action: risk_judge_review" in projection
    assert "Intel raises guidance, relevance 0.92, materiality 0.78" in projection
    assert "company_news_raw contaminated, score 0.73" in projection


def test_decision_trace_links_decision_to_vendor_facts():
    state = _state_with_vendor_facts()
    state["evidence_graph"] = build_evidence_graph(state)
    trace = build_decision_trace(state, "Final answer: BUY because the breakout remains valid.")

    assert trace["decision"]["action"] == "BUY"
    assert trace["inference_ids"]
    assert "fact_market_price_action_summary_001" in trace["fact_ids"]
    assert "price_action_summary" in trace["source_labels"]
    assert not any(issue["code"] == "trace_missing_fact_links" for issue in trace["audit_issues"])


def test_initial_state_and_payload_include_vendor_fact_graph_fields():
    initial_state = Propagator().create_initial_state("MU", "2026-05-04")

    assert initial_state["evidence_source_facts"] == []
    assert initial_state["evidence_graph"]["facts"] == []
    assert initial_state["evidence_graph_audit"] == []
    assert initial_state["decision_trace"] == {}

    final_state = {
        "evidence_source_facts": [{"id": "fact_market_price_action_summary_001"}],
        "evidence_graph": {"facts": [{"id": "fact_market_price_action_summary_001"}]},
        "evidence_graph_audit": [{"code": "unsupported_ledger_observation"}],
        "decision_trace": {"decision": {"action": "BUY"}},
    }
    reports = build_analysis_reports_payload(final_state)

    assert reports["evidence_source_facts"][0]["id"] == "fact_market_price_action_summary_001"
    assert reports["evidence_graph"]["facts"][0]["id"] == "fact_market_price_action_summary_001"
    assert reports["evidence_graph_audit"][0]["code"] == "unsupported_ledger_observation"
    assert reports["decision_trace"]["decision"]["action"] == "BUY"
