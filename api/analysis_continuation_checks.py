import json
from types import SimpleNamespace

from api.models import AnalysisSession
from api.utils import (
    ANALYST_REPORT_KEYS,
    apply_previous_reports_to_state,
    build_analysis_reports_payload,
    extract_stream_reports_payload,
    find_previous_analysis_session,
    plan_continuation_analysts,
)
from opentrace.graph.opentrace_graph import OpenTraceGraph


def test_find_previous_analysis_session_prefers_latest_incomplete_matching_run():
    sessions = [
        AnalysisSession(
            id=1,
            ticker="MRVL",
            analysis_date="2026-05-26",
            time_horizon="2-3 months",
            status="completed",
            reports={"final_trade_decision": "HOLD"},
        ),
        AnalysisSession(
            id=2,
            ticker="MRVL",
            analysis_date="2026-05-26",
            time_horizon="2-3 months",
            status="running",
            reports={"market_report": "done"},
        ),
    ]
    query = SimpleNamespace(
        filter=lambda *args: query,
        order_by=lambda *args: query,
        all=lambda: sessions,
    )
    db = SimpleNamespace(query=lambda model: query)

    found = find_previous_analysis_session(
        db,
        ticker="mrvl",
        analysis_date="2026-05-26",
        time_horizon="2-3 months",
    )

    assert found.id == 2


def test_plan_continuation_analysts_skips_completed_analyst_reports():
    reports = {
        "catalyst_report": "catalyst done",
        "market_report": "market done",
        "sentiment_report": "social done",
    }

    assert plan_continuation_analysts(
        ["catalyst", "market", "social", "news", "fundamentals"],
        reports,
    ) == ["news", "fundamentals"]

    complete_reports = {report_key: "done" for report_key in ANALYST_REPORT_KEYS.values()}
    assert plan_continuation_analysts(
        ["catalyst", "market", "social", "news", "fundamentals"],
        complete_reports,
    ) == []

    assert OpenTraceGraph.normalize_selected_analysts([]) == []


def test_apply_previous_reports_to_state_restores_persisted_graph_state():
    state = {
        "market_report": "",
        "investment_debate_state": {"history": "", "count": 0},
    }
    reports = {
        "market_report": "existing market report",
        "investment_debate_state": {"history": "prior debate", "count": 2},
        "unknown_future_key": "ignored",
    }

    apply_previous_reports_to_state(state, reports)

    assert state["market_report"] == "existing market report"
    assert state["investment_debate_state"] == {"history": "prior debate", "count": 2}
    assert "unknown_future_key" not in state


def test_extract_stream_reports_payload_includes_debate_resume_fields():
    chunk = {
        "market_report": "market done",
        "evidence_ledger": [{"evidence_id": "E-MKT-001"}],
        "admissibility_report": {"valid": True},
        "critical_evidence_ids": ["E-MKT-001"],
        "contested_issues": [{"issue_id": "I-001"}],
        "research_debate_turns": [{"turn_id": "T1"}],
        "thesis_ledger": {"entries": []},
        "trader_plan_v1": {"action": "hold"},
        "risk_patches": [{"patch_id": "P1"}],
        "decision_diff": {"changed_fields": []},
        "unknown_future_key": "ignore me",
        "final_trade_decision": None,
    }

    reports = extract_stream_reports_payload(chunk)

    assert reports["market_report"] == "market done"
    assert reports["evidence_ledger"] == [{"evidence_id": "E-MKT-001"}]
    assert reports["admissibility_report"] == {"valid": True}
    assert reports["critical_evidence_ids"] == ["E-MKT-001"]
    assert reports["contested_issues"] == [{"issue_id": "I-001"}]
    assert reports["research_debate_turns"] == [{"turn_id": "T1"}]
    assert reports["thesis_ledger"] == {"entries": []}
    assert reports["trader_plan_v1"] == {"action": "hold"}
    assert reports["risk_patches"] == [{"patch_id": "P1"}]
    assert reports["decision_diff"] == {"changed_fields": []}
    assert "unknown_future_key" not in reports
    assert "final_trade_decision" not in reports


def test_saved_debate_report_shape_survives_api_payload_extraction():
    reports = {
        "evidence_ledger": [
            {
                "evidence_id": "E-SEN-005",
                "ticker": "SNDK",
                "source_agent": "sentiment_analyst",
                "source_tool": "news_bundle",
                "source_ref": "company_news_window_001",
                "observed_at": "2026-06-03",
                "claim": "Q3 results and analyst upgrades support upside if breakout confirms.",
                "fact_type": "news",
                "polarity": "bullish",
                "time_horizon": "2-3 months",
                "confidence": 0.72,
                "materiality": 0.8,
                "criticality": 0.58,
                "supports": ["prefer_wait_for_trigger"],
                "contradicts": [],
            }
        ],
        "admissibility_report": {
            "accepted_evidence_ids": ["E-SEN-005"],
            "downgraded_evidence": [],
            "rejected_evidence": [],
        },
        "contested_issues": [
            {
                "issue_id": "I-001",
                "question": "Does the admissible evidence support a long bias, or should action remain HOLD?",
                "candidate_evidence": ["E-SEN-005"],
                "decision_fields_at_risk": ["action", "execution_mode"],
            }
        ],
        "research_debate_turns": [
            {
                "turn_id": "T2_bull_response",
                "speaker": "Bull",
                "issue_id": "I-001",
                "position": "INITIATE_PARTIAL_LONG_ON_CONFIRMED_BREAKOUT",
                "claim": "Initiate a disciplined partial long on a validated breakout.",
                "evidence_ids": ["E-SEN-005"],
                "rebuttal_to": "Bear_last_argument",
                "plan_implication": {
                    "field": "action",
                    "proposed_value": "initiate_partial_long_on_confirmed_breakout",
                },
                "confidence": 0.72,
            }
        ],
        "thesis_ledger": {
            "winning_thesis": "INITIATE_PARTIAL_LONG_ON_CONFIRMED_BREAKOUT_ABOVE_20D_HIGH_(1804)",
            "accepted_claims": [
                {
                    "claim_id": "C-001",
                    "claim": "Initiate a disciplined partial long on a validated breakout.",
                    "evidence_ids": ["I-001", "E-SEN-005"],
                    "effect": "execution_mode=wait_for_trigger",
                }
            ],
            "rejected_claims": [],
            "unresolved_uncertainties": [
                "Primary 8-K / 10-Q / Form-4 filings remain unverified and materially relevant."
            ],
            "recommended_plan_constraints": {"execution_mode": "wait_for_trigger"},
        },
        "trader_plan_v1": {
            "plan_id": "trader_plan_v1",
            "action": "HOLD",
            "execution_mode": "wait_for_trigger",
            "order_type": "MARKET",
            "position_size_pct": None,
            "entry_condition": "setup_confirmation",
            "stop_loss": 94.0,
            "take_profit": 110.0,
            "rationale_links": {
                "action": ["execution_plan_compiler"],
                "execution_mode": ["execution_plan_compiler"],
            },
        },
        "risk_patches": [
            {
                "patch_id": "patch-risky-2026-06-03-001",
                "author": "Risky Risk Analyst",
                "target_plan_version": "v1",
                "patch_type": "modification",
                "field": "position_size_pct",
                "old_value": "N/A",
                "new_value": 25,
                "evidence_ids": ["E-SEN-005"],
                "reason": "Capture early upside on a validated breakout.",
                "expected_effect": "Capture early upside while preserving capital.",
                "materiality": "material",
            }
        ],
        "risk_patch_validation": [
            {
                "patch_id": "patch-risky-2026-06-03-001",
                "valid": False,
                "reason": "patch targets stale plan version",
            }
        ],
        "investment_debate_state": {
            "history": "Bull Analyst: Buy on confirmed breakout.\nBear Analyst: Hold until filings are verified.",
            "judge_decision": "Research Manager: HOLD until confirmation.",
            "count": 3,
        },
        "risk_debate_state": {
            "history": "Risky Analyst: Use a material initial tranche.\nSafe Analyst: Wait for filings.\nNeutral Analyst: Keep the trigger.",
            "judge_decision": "Risk Manager: HOLD and wait for trigger confirmation.",
            "count": 3,
        },
        "final_trade_decision": (
            "BEGIN_DECISION_JSON "
            '{"action":"HOLD","ticker":"SNDK","position_size_pct":null,'
            '"stop_loss":94.0,"take_profit":110.0,'
            '"execution_intent":"wait_for_trigger","rationale":"Hold remains appropriate."}'
            " END_DECISION_JSON"
        ),
    }

    stream_payload = extract_stream_reports_payload(reports)
    final_payload = build_analysis_reports_payload(reports)

    assert stream_payload["research_debate_turns"][0]["speaker"] == "Bull"
    assert stream_payload["thesis_ledger"]["unresolved_uncertainties"][0].startswith("Primary 8-K")
    assert stream_payload["risk_patch_validation"][0]["valid"] is False
    assert final_payload["evidence_ledger"][0]["source_ref"] == "company_news_window_001"
    assert final_payload["trader_plan_v1"]["position_size_pct"] is None
    assert "BEGIN_DECISION_JSON" in final_payload["final_trade_decision"]
    json.dumps(stream_payload)
    json.dumps(final_payload)
