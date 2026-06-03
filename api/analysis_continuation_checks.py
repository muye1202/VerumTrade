from types import SimpleNamespace

from api.models import AnalysisSession
from api.utils import (
    ANALYST_REPORT_KEYS,
    apply_previous_reports_to_state,
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
