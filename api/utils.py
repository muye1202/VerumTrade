import json
import asyncio
from typing import Dict, Any
from fastapi import WebSocket

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.execution.portfolio_context import fetch_portfolio_context
from cli.analysis_utils import _msg_type_and_content, _extract_tool_calls
from api.database import SessionLocal
from api.models import AnalysisSession


REPORT_PAYLOAD_KEYS = [
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "market_ledger",
    "sentiment_ledger",
    "news_ledger",
    "fundamentals_ledger",
    "evidence_source_facts",
    "evidence_graph",
    "evidence_graph_audit",
    "decision_trace",
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


def build_analysis_reports_payload(final_state: Dict[str, Any] | None) -> Dict[str, Any]:
    """Build the persisted/API report payload from the final graph state."""
    state = final_state or {}
    return {
        key: state.get(key)
        for key in REPORT_PAYLOAD_KEYS
        if key in state and state.get(key) is not None
    }


async def stream_analysis_ws(req, websocket: WebSocket) -> Dict[str, Any]:
    """
    Runs the TradingAgentsGraph analysis and streams intermediate messages,
    tool calls, and report updates over a WebSocket.
    """
    all_logs = []
    final_reports = {}

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
        
        final_reports = {"market_report": "### Mock Market Report\nEverything is going up."}
        
        # Save mock session to DB for testing UI
        db = SessionLocal()
        try:
            safe_logs = json.loads(json.dumps(all_logs, default=str))
            safe_reports = json.loads(json.dumps(final_reports, default=str))
            db_session = AnalysisSession(
                ticker=f"MOCK {req.ticker}",
                analysis_date=req.analysis_date,
                time_horizon=req.time_horizon,
                logs=safe_logs,
                reports=safe_reports,
            )
            db.add(db_session)
            db.commit()
        except Exception as e:
            print(f"Error saving mock history: {e}")
        finally:
            db.close()
            
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
    
    # Optional execution config
    if req.execution:
        config["alpaca_execution"] = {
            **config.get("alpaca_execution", {}),
            "enabled": req.execution.enabled,
            "paper_trading": req.execution.paper,
            "position_size_pct": req.execution.position_size_pct,
        }
        
    graph = TradingAgentsGraph(
        req.analysts, config=config, debug=True
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
    args = graph.propagator.get_graph_args()
    
    seen_messages = 0
    final_state = None
    
    await websocket.send_json({"event": "system", "content": "Starting analysis stream..."})
    
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
        
    # Save real session to DB
    db = SessionLocal()
    try:
        # Round-trip through json.dumps/loads to coerce any non-serializable Python
        # objects (LangChain messages, datetimes, Pydantic models, etc.) to plain
        # strings before SQLAlchemy tries to persist them as JSON.
        safe_logs = json.loads(json.dumps(all_logs, default=str))
        if final_state:
            final_reports.update(build_analysis_reports_payload(final_state))
        safe_reports = json.loads(json.dumps(final_reports, default=str))

        db_session = AnalysisSession(
            ticker=req.ticker,
            analysis_date=req.analysis_date,
            time_horizon=req.time_horizon,
            logs=safe_logs,
            reports=safe_reports,
        )
        db.add(db_session)
        db.commit()
    except Exception as e:
        print(f"Error saving history: {e}")
        try:
            await websocket.send_json({
                "event": "system",
                "content": f"Warning: session could not be saved to history: {e}",
            })
        except Exception:
            pass
    finally:
        db.close()
        
    return final_state

async def run_analysis_sync(req) -> Dict[str, Any]:
    """
    Runs the TradingAgentsGraph analysis synchronously and returns the final state.
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
        
    graph = TradingAgentsGraph(
        req.analysts, config=config, debug=False
    )
    
    portfolio_ctx = fetch_portfolio_context(req.ticker)
    
    init_agent_state = graph.propagator.create_initial_state(
        req.ticker,
        req.analysis_date,
        portfolio_context=portfolio_ctx,
        time_horizon=req.time_horizon,
    )
    args = graph.propagator.get_graph_args()
    
    final_state = None
    async for chunk in graph.graph.astream(init_agent_state, **args):
        final_state = chunk
        
    return final_state
