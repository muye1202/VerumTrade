# TradingAgents/graph/propagation.py

from typing import Dict, Any, Optional
from tradingagents.utils.market_session import (
    describe_us_market_session,
    format_market_session_context,
)
from tradingagents.agents.utils.time_horizon import get_time_horizon_spec


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
    ) -> Dict[str, Any]:
        """Create the initial state for the agent graph."""
        market_session = describe_us_market_session()
        horizon = get_time_horizon_spec(time_horizon).key
        return {
            "messages": [("human", company_name)],
            "portfolio_context": portfolio_context,
            "company_of_interest": company_name,
            "trade_date": str(trade_date),
            "time_horizon": horizon,
            "market_session": market_session,
            "market_session_context": format_market_session_context(market_session),
            "tool_call_counts": {},
            "tool_call_total": 0,
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
            "sentiment_report": "",
            "news_report": "",
        }

    def get_graph_args(self) -> Dict[str, Any]:
        """Get arguments for the graph invocation."""
        return {
            "stream_mode": "values",
            "config": {"recursion_limit": self.max_recur_limit},
        }
