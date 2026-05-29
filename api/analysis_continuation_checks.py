from types import SimpleNamespace

from api.models import AnalysisSession
from api.utils import (
    ANALYST_REPORT_KEYS,
    apply_previous_reports_to_state,
    find_previous_analysis_session,
    plan_continuation_analysts,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph


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

    assert TradingAgentsGraph.normalize_selected_analysts([]) == []


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
