# opentrace/graph/propagation.py

from typing import Dict, Any, Optional
from opentrace.utils.market_session import (
    describe_us_market_session,
    format_market_session_context,
)
from opentrace.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from opentrace.agents.analysts.workbench import normalize_ledger
from opentrace.agents.utils.agent_runtime.evidence_graph import build_evidence_graph
from opentrace.graph.evidence_ledger_schema import validate_admissible_evidence
from opentrace.graph.reasoning_trace import empty_agent_reasoning_trace


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        portfolio_context: str = "",
        time_horizon: Optional[str] = None,
        macro_regime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create the initial state for the agent graph.

        ``macro_regime`` may be precomputed by the caller (preferred in async entry points, where
        it is built off-thread to avoid blocking the event loop). When omitted, it is built lazily
        here — safe for sync callers, and a no-op/empty dict when macro data is unavailable.
        """
        market_session = describe_us_market_session()
        horizon = get_time_horizon_spec(time_horizon).key
        empty_graph = build_evidence_graph({})
        empty_admissibility = validate_admissible_evidence([], time_horizon=horizon)
        if macro_regime is None:
            from opentrace.agents.utils.market_data.macro_regime import build_macro_regime_context

            macro_regime = build_macro_regime_context(str(trade_date))
        return {
            "messages": [("human", company_name)],
            "portfolio_context": portfolio_context,
            "company_of_interest": company_name,
            "trade_date": str(trade_date),
            "time_horizon": horizon,
            "market_session": market_session,
            "market_session_context": format_market_session_context(market_session),
            "macro_regime": macro_regime or {},
            "force_no_tools_for": "",
            "tool_round_counts": {},
            "tool_call_counts": {},
            "tool_call_total": 0,
            "tool_calls_issued_by_agent": {},
            "tool_calls_issued_total": 0,
            "analyst_tool_call_links": {},
            "analyst_tool_call_blocked_counts": {},
            "analyst_workbench_metrics": {},
            "tool_result_cache": {},
            "tool_cache_metrics": {},
            "vendor_telemetry": [],
            "llm_metrics": {},
            "investment_debate_state": {"history": "", "current_response": "", "count": 0},
            "risk_debate_state": {
                "history": "",
                "current_risky_response": "",
                "current_safe_response": "",
                "current_neutral_response": "",
                "count": 0,
            },
            "market_report": "",
            "fundamentals_report": "",
            "catalyst_report": "",
            "sentiment_report": "",
            "news_report": "",
            "market_evidence": "",
            "fundamentals_evidence": "",
            "catalyst_evidence": "",
            "sentiment_evidence": "",
            "news_evidence": "",
            "market_ledger": normalize_ledger("market"),
            "sentiment_ledger": normalize_ledger("sentiment"),
            "news_ledger": normalize_ledger("news"),
            "fundamentals_ledger": normalize_ledger("fundamentals"),
            "catalyst_ledger": normalize_ledger("catalyst"),
            "catalyst_event_bundle": {},
            "catalyst_event_report_structured": {},
            "catalyst_parse_telemetry": {},
            "evidence_source_facts": [],
            "evidence_graph": empty_graph,
            "evidence_graph_audit": [],
            "evidence_ledger": [],
            "admissibility_report": empty_admissibility,
            "critical_evidence_ids": [],
            "contested_issues": [],
            "research_debate_turns": [],
            "research_debate_validation": {"accepted_turns": [], "rejected_turns": []},
            "thesis_ledger": {},
            "thesis_ledger_validation": {},
            "trader_plan_v1": {},
            "trader_plan_validation": {},
            "risk_patches": [],
            "risk_patch_validation": [],
            "decision_diff": {},
            "decision_trace": {},
            "trader_decision_brief": {},
            "trade_setup_diagnosis": {},
            "scenario_analysis": {},
            "execution_plan_compiler": {},
            "trader_self_audit": {},
            "agent_reasoning_trace": empty_agent_reasoning_trace(
                ticker=company_name,
                trade_date=str(trade_date),
                time_horizon=horizon,
            ),
            "final_trade_decision": "",
            "final_trade_decision_structured": None,
            "final_trade_decision_validation_error": "",
            "market_snapshot": {},
            "decision_guard": {},
        }

    def get_graph_args(self) -> Dict[str, Any]:
        """Get arguments for the graph invocation."""
        return {
            "stream_mode": "values",
            "config": {"recursion_limit": self.max_recur_limit},
        }
