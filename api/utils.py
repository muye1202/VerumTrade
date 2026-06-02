import json
import asyncio
from typing import Dict, Any, Iterable
from fastapi import WebSocket

from opentrace.graph.opentrace_graph import OpenTraceGraph
from opentrace.graph.provider_settings import serialize_provider_settings
from opentrace.graph.reasoning_trace import build_agent_reasoning_trace
from opentrace.default_config import DEFAULT_CONFIG
from opentrace.execution.portfolio_context import fetch_portfolio_context
from cli.analysis_utils import _msg_type_and_content, _extract_tool_calls
from api.database import SessionLocal
from api.models import AnalysisSession


REPORT_PAYLOAD_KEYS = [
    "market_report",
    "sentiment_report",
    "news_report",
    "catalyst_report",
    "fundamentals_report",
    "market_ledger",
    "sentiment_ledger",
    "news_ledger",
    "catalyst_ledger",
    "fundamentals_ledger",
    "catalyst_event_bundle",
    "catalyst_event_report_structured",
    "catalyst_parse_telemetry",
    "catalyst_evidence",
    "evidence_source_facts",
    "evidence_graph",
    "evidence_graph_audit",
    "decision_trace",
    "trader_decision_brief",
    "trade_setup_diagnosis",
    "scenario_analysis",
    "execution_plan_compiler",
    "trader_self_audit",
    "agent_reasoning_trace",
    "analyst_workbench_metrics",
    "analyst_tool_call_links",
    "analyst_tool_call_blocked_counts",
    "tool_cache_metrics",
    "vendor_telemetry",
    "investment_debate_state",
    "trader_investment_plan",
    "risk_debate_state",
    "final_trade_decision",
    "final_trade_decision_structured",
    "final_trade_decision_validation_error",
    "market_snapshot",
    "decision_guard",
    "llm_metrics",
]

ANALYST_REPORT_KEYS = {
    "catalyst": "catalyst_report",
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def _has_report_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def plan_continuation_analysts(
    requested_analysts: Iterable[str],
    previous_reports: Dict[str, Any] | None,
) -> list[str]:
    """Return only analyst stages that do not already have persisted reports."""
    reports = previous_reports or {}
    remaining = []
    for analyst in requested_analysts:
        analyst_key = analyst.value if hasattr(analyst, "value") else str(analyst)
        report_key = ANALYST_REPORT_KEYS.get(analyst_key)
        if report_key and _has_report_value(reports.get(report_key)):
            continue
        remaining.append(analyst_key)
    return remaining


def apply_previous_reports_to_state(
    state: Dict[str, Any],
    previous_reports: Dict[str, Any] | None,
) -> None:
    """Restore persisted report/state keys into a fresh graph initial state."""
    for key, value in (previous_reports or {}).items():
        if key in state and _has_report_value(value):
            state[key] = value


def find_previous_analysis_session(
    db,
    *,
    ticker: str,
    analysis_date: str,
    time_horizon: str | None = None,
    session_id: int | None = None,
) -> AnalysisSession | None:
    """Find the session to continue, preferring incomplete matching runs."""
    query = db.query(AnalysisSession)
    if session_id is not None:
        return query.filter(AnalysisSession.id == session_id).first()

    sessions = (
        query.filter(AnalysisSession.ticker == ticker.upper())
        .filter(AnalysisSession.analysis_date == analysis_date)
        .order_by(AnalysisSession.created_at.desc())
        .all()
    )
    if time_horizon:
        sessions = [s for s in sessions if s.time_horizon == time_horizon]

    incomplete = [
        s for s in sessions
        if (s.status or "").lower() != "completed"
        or not _has_report_value((s.reports or {}).get("final_trade_decision"))
    ]
    return incomplete[0] if incomplete else (sessions[0] if sessions else None)


def build_analysis_reports_payload(final_state: Dict[str, Any] | None) -> Dict[str, Any]:
    """Build the persisted/API report payload from the final graph state."""
    state = final_state or {}
    if "agent_reasoning_trace" not in state:
        state = {**state, "agent_reasoning_trace": build_agent_reasoning_trace(state)}
    return {
        key: state.get(key)
        for key in REPORT_PAYLOAD_KEYS
        if key in state and state.get(key) is not None
    }


async def stream_analysis_ws(req, websocket: WebSocket) -> Dict[str, Any]:
    """
    Runs the OpenTraceGraph analysis and streams intermediate messages,
    tool calls, and report updates over a WebSocket.
    """
    all_logs = []
    final_reports = {}
    previous_session = None
    continuation_analysts = list(req.analysts)

    if req.continue_previous:
        db = SessionLocal()
        try:
            previous_session = find_previous_analysis_session(
                db,
                ticker=req.ticker,
                analysis_date=req.analysis_date,
                time_horizon=req.time_horizon,
                session_id=req.continue_session_id,
            )
            if previous_session:
                all_logs = list(previous_session.logs or [])
                final_reports = dict(previous_session.reports or {})
                continuation_analysts = plan_continuation_analysts(req.analysts, final_reports)
        finally:
            db.close()

    # --- Create the session record immediately so artifacts survive interruptions ---
    ticker_label = f"MOCK {req.ticker}" if req.mock else req.ticker
    session_id = None
    if previous_session:
        session_id = previous_session.id
        _resume_db = SessionLocal()
        try:
            record = _resume_db.query(AnalysisSession).filter(AnalysisSession.id == session_id).first()
            if record:
                record.status = "running"
                _resume_db.commit()
        finally:
            _resume_db.close()
    else:
        _create_db = SessionLocal()
        try:
            db_record = AnalysisSession(
                ticker=ticker_label,
                analysis_date=req.analysis_date,
                time_horizon=req.time_horizon,
                logs=[],
                reports={},
                status="running",
            )
            _create_db.add(db_record)
            _create_db.commit()
            _create_db.refresh(db_record)
            session_id = db_record.id
        except Exception as e:
            print(f"Error creating session record: {e}")
        finally:
            _create_db.close()

    def _flush(status: str = "running") -> None:
        """Persist the current in-memory logs and reports to the DB record."""
        if session_id is None:
            return
        db = SessionLocal()
        try:
            record = db.query(AnalysisSession).filter(AnalysisSession.id == session_id).first()
            if record:
                record.logs = json.loads(json.dumps(all_logs, default=str))
                record.reports = json.loads(json.dumps(final_reports, default=str))
                record.status = status
                db.commit()
        except Exception as e:
            print(f"Error flushing session {session_id}: {e}")
        finally:
            db.close()

    if req.mock:
        await websocket.send_json({"event": "system", "content": f"MOCK MODE: Starting mock stream for {req.ticker}..."})
        await asyncio.sleep(1)
        await websocket.send_json({
            "event": "chunk",
            "updates": [{"event": "message", "type": "Reasoning", "content": "I am gathering data..."}]
        })
        await asyncio.sleep(1)
        await websocket.send_json({
            "event": "chunk",
            "updates": [{"event": "tool_call", "tool": "get_stock_data", "args": {"ticker": req.ticker}}]
        })
        await asyncio.sleep(1)
        await websocket.send_json({
            "event": "chunk",
            "updates": [{"event": "message", "type": "Reasoning", "content": "The stock looks good!"}],
            "reports": {"market_report": "### Mock Market Report\nEverything is going up."}
        })
        await asyncio.sleep(1)

        final_reports["market_report"] = "### Mock Market Report\nEverything is going up."
        _flush("completed")

        return {"final_trade_decision": f"MOCK BUY {req.ticker}"}

    config = DEFAULT_CONFIG.copy()
    requested_depth = req.research_depth
    debate_cap = int(config.get("max_debate_rounds_cap", requested_depth))
    risk_cap = int(config.get("max_risk_rounds_cap", requested_depth))
    
    config["max_debate_rounds"] = min(requested_depth, debate_cap)
    config["max_risk_discuss_rounds"] = min(requested_depth, risk_cap)
    config["max_recur_limit"] = max(config.get("max_recur_limit", 100), requested_depth * 120)
    
    config["quick_think_llm"] = req.shallow_thinker
    config["deep_think_llm"] = req.deep_thinker
    config["backend_url"] = req.backend_url if req.backend_url is not None else ""
    config["llm_provider"] = req.llm_provider.lower()
    config["provider_settings"] = serialize_provider_settings(req.provider_settings)
    if req.qwen_enable_thinking is not None:
        config["qwen_enable_thinking"] = req.qwen_enable_thinking
        config["qwen_enable_thinking_quick"] = req.qwen_enable_thinking
    if req.qwen_thinking_budget is not None:
        config["qwen_thinking_budget"] = req.qwen_thinking_budget
    if req.azure_foundry_enable_thinking is not None:
        config["azure_foundry_enable_thinking"] = req.azure_foundry_enable_thinking
    if req.azure_foundry_reasoning_effort is not None:
        config["azure_foundry_reasoning_effort"] = req.azure_foundry_reasoning_effort
    
    # Optional execution config
    if req.execution:
        config["alpaca_execution"] = {
            **config.get("alpaca_execution", {}),
            "enabled": req.execution.enabled,
            "paper_trading": req.execution.paper,
            "position_size_pct": req.execution.position_size_pct,
        }
        
    graph = OpenTraceGraph(
        continuation_analysts, config=config, debug=True
    )
    
    await websocket.send_json({"event": "system", "content": f"Fetching portfolio context for {req.ticker}..."})
    portfolio_ctx = fetch_portfolio_context(req.ticker)
    await websocket.send_json({"event": "system", "content": f"Portfolio context loaded."})
    
    init_agent_state = graph.propagator.create_initial_state(
        req.ticker,
        req.analysis_date,
        portfolio_context=portfolio_ctx,
        time_horizon=req.time_horizon,
    )
    if previous_session:
        apply_previous_reports_to_state(init_agent_state, final_reports)
        await websocket.send_json({
            "event": "system",
            "content": f"Continuing saved analysis session {previous_session.id} for {req.ticker}.",
        })
        await websocket.send_json({
            "event": "chunk",
            "updates": all_logs,
            "reports": final_reports,
        })
        skipped = [
            analyst for analyst in req.analysts
            if analyst not in continuation_analysts
        ]
        if skipped:
            await websocket.send_json({
                "event": "system",
                "content": f"Reusing completed analyst reports: {', '.join(skipped)}.",
            })
        if not continuation_analysts:
            await websocket.send_json({
                "event": "system",
                "content": "All analyst reports are already present; continuing from the trading decision pipeline.",
            })
    args = graph.propagator.get_graph_args()
    
    seen_messages = 0
    final_state = None

    await websocket.send_json({"event": "system", "content": "Starting analysis stream..."})

    try:
        async for chunk in graph.graph.astream(init_agent_state, **args):
            messages = chunk.get("messages") or []
            new_messages = messages[seen_messages:] if seen_messages <= len(messages) else []
            seen_messages = len(messages)

            chunk_updates = []
            for msg in new_messages:
                msg_type, content = _msg_type_and_content(msg)

                update_item = {
                    "event": "message",
                    "type": msg_type,
                    "content": content
                }
                chunk_updates.append(update_item)
                all_logs.append(update_item)

                for tool_name, tool_args in _extract_tool_calls(msg):
                    tool_item = {
                        "event": "tool_call",
                        "tool": tool_name,
                        "args": tool_args
                    }
                    chunk_updates.append(tool_item)
                    all_logs.append(tool_item)

            reports = {}
            # Analyst Team
            if chunk.get("market_report"):
                reports["market_report"] = chunk["market_report"]
            if chunk.get("sentiment_report"):
                reports["sentiment_report"] = chunk["sentiment_report"]
            if chunk.get("news_report"):
                reports["news_report"] = chunk["news_report"]
            if chunk.get("catalyst_report"):
                reports["catalyst_report"] = chunk["catalyst_report"]
            if chunk.get("catalyst_event_bundle"):
                reports["catalyst_event_bundle"] = chunk["catalyst_event_bundle"]
            if chunk.get("catalyst_event_report_structured"):
                reports["catalyst_event_report_structured"] = chunk["catalyst_event_report_structured"]
            if chunk.get("catalyst_parse_telemetry"):
                reports["catalyst_parse_telemetry"] = chunk["catalyst_parse_telemetry"]
            if chunk.get("fundamentals_report"):
                reports["fundamentals_report"] = chunk["fundamentals_report"]

            if chunk.get("evidence_source_facts"):
                reports["evidence_source_facts"] = chunk["evidence_source_facts"]
            if chunk.get("evidence_graph"):
                reports["evidence_graph"] = chunk["evidence_graph"]
            if chunk.get("evidence_graph_audit"):
                reports["evidence_graph_audit"] = chunk["evidence_graph_audit"]
            if chunk.get("decision_trace"):
                reports["decision_trace"] = chunk["decision_trace"]
            if chunk.get("trader_decision_brief"):
                reports["trader_decision_brief"] = chunk["trader_decision_brief"]
            if chunk.get("trade_setup_diagnosis"):
                reports["trade_setup_diagnosis"] = chunk["trade_setup_diagnosis"]
            if chunk.get("scenario_analysis"):
                reports["scenario_analysis"] = chunk["scenario_analysis"]
            if chunk.get("execution_plan_compiler"):
                reports["execution_plan_compiler"] = chunk["execution_plan_compiler"]
            if chunk.get("trader_self_audit"):
                reports["trader_self_audit"] = chunk["trader_self_audit"]
            if chunk.get("agent_reasoning_trace"):
                reports["agent_reasoning_trace"] = chunk["agent_reasoning_trace"]

            # Debate State
            if chunk.get("investment_debate_state"):
                reports["investment_debate_state"] = chunk["investment_debate_state"]

            # Trading Team
            if chunk.get("trader_investment_plan"):
                reports["trader_investment_plan"] = chunk["trader_investment_plan"]

            # Risk State
            if chunk.get("risk_debate_state"):
                reports["risk_debate_state"] = chunk["risk_debate_state"]

            if chunk.get("final_trade_decision"):
                reports["final_trade_decision"] = chunk["final_trade_decision"]

            if chunk_updates or reports:
                payload = {
                    "event": "chunk",
                    "updates": chunk_updates,
                    "reports": reports
                }
                await websocket.send_json(payload)

            final_reports.update(reports)
            final_state = chunk

            # Flush to DB whenever a new report section arrives so partial
            # results survive an interruption.
            if reports:
                _flush()

    except Exception:
        _flush("interrupted")
        raise

    if final_state:
        final_state["agent_reasoning_trace"] = build_agent_reasoning_trace(final_state)
        final_reports["agent_reasoning_trace"] = final_state["agent_reasoning_trace"]
        await websocket.send_json(
            {
                "event": "chunk",
                "updates": [],
                "reports": {
                    "agent_reasoning_trace": final_state["agent_reasoning_trace"],
                },
            }
        )

    # Final flush: merge full state payload and mark completed.
    if final_state:
        final_reports.update(build_analysis_reports_payload(final_state))
    _flush("completed")

    return final_state

async def run_analysis_sync(req) -> Dict[str, Any]:
    """
    Runs the OpenTraceGraph analysis synchronously and returns the final state.
    Used for REST API endpoints where streaming isn't required.
    """
    config = DEFAULT_CONFIG.copy()
    requested_depth = req.research_depth
    debate_cap = int(config.get("max_debate_rounds_cap", requested_depth))
    risk_cap = int(config.get("max_risk_rounds_cap", requested_depth))

    config["max_debate_rounds"] = min(requested_depth, debate_cap)
    config["max_risk_discuss_rounds"] = min(requested_depth, risk_cap)
    config["max_recur_limit"] = max(config.get("max_recur_limit", 100), requested_depth * 120)

    config["quick_think_llm"] = req.shallow_thinker
    config["deep_think_llm"] = req.deep_thinker
    config["backend_url"] = req.backend_url if req.backend_url is not None else ""
    config["llm_provider"] = req.llm_provider.lower()
    config["provider_settings"] = serialize_provider_settings(req.provider_settings)
    if req.qwen_enable_thinking is not None:
        config["qwen_enable_thinking"] = req.qwen_enable_thinking
        config["qwen_enable_thinking_quick"] = req.qwen_enable_thinking
    if req.qwen_thinking_budget is not None:
        config["qwen_thinking_budget"] = req.qwen_thinking_budget
    if req.azure_foundry_enable_thinking is not None:
        config["azure_foundry_enable_thinking"] = req.azure_foundry_enable_thinking
    if req.azure_foundry_reasoning_effort is not None:
        config["azure_foundry_reasoning_effort"] = req.azure_foundry_reasoning_effort

    previous_session = None
    previous_reports = {}
    continuation_analysts = list(req.analysts)
    if req.continue_previous:
        db = SessionLocal()
        try:
            previous_session = find_previous_analysis_session(
                db,
                ticker=req.ticker,
                analysis_date=req.analysis_date,
                time_horizon=req.time_horizon,
                session_id=req.continue_session_id,
            )
            if previous_session:
                previous_reports = dict(previous_session.reports or {})
                continuation_analysts = plan_continuation_analysts(req.analysts, previous_reports)
        finally:
            db.close()

    graph = OpenTraceGraph(
        continuation_analysts, config=config, debug=False
    )

    portfolio_ctx = fetch_portfolio_context(req.ticker)

    init_agent_state = graph.propagator.create_initial_state(
        req.ticker,
        req.analysis_date,
        portfolio_context=portfolio_ctx,
        time_horizon=req.time_horizon,
    )
    if previous_session:
        apply_previous_reports_to_state(init_agent_state, previous_reports)
    args = graph.propagator.get_graph_args()

    # Create session record immediately
    session_id = None
    if previous_session:
        session_id = previous_session.id
        _resume_db = SessionLocal()
        try:
            record = _resume_db.query(AnalysisSession).filter(AnalysisSession.id == session_id).first()
            if record:
                record.status = "running"
                _resume_db.commit()
        finally:
            _resume_db.close()
    else:
        _create_db = SessionLocal()
        try:
            db_record = AnalysisSession(
                ticker=req.ticker,
                analysis_date=req.analysis_date,
                time_horizon=req.time_horizon,
                logs=[],
                reports={},
                status="running",
            )
            _create_db.add(db_record)
            _create_db.commit()
            _create_db.refresh(db_record)
            session_id = db_record.id
        except Exception as e:
            print(f"Error creating session record: {e}")
        finally:
            _create_db.close()

    final_state = None
    status = "interrupted"
    try:
        async for chunk in graph.graph.astream(init_agent_state, **args):
            final_state = chunk
        status = "completed"
    finally:
        if final_state:
            final_state["agent_reasoning_trace"] = build_agent_reasoning_trace(final_state)
        # Persist whatever we have, completed or not
        if session_id is not None:
            db = SessionLocal()
            try:
                record = db.query(AnalysisSession).filter(AnalysisSession.id == session_id).first()
                if record:
                    reports = build_analysis_reports_payload(final_state) if final_state else {}
                    if previous_reports:
                        reports = {**previous_reports, **reports}
                    record.reports = json.loads(json.dumps(reports, default=str))
                    record.status = status
                    db.commit()
            except Exception as e:
                print(f"Error saving sync session {session_id}: {e}")
            finally:
                db.close()

    return final_state
