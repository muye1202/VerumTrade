# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_runtime.agent_states import AgentState


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(
        self,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        max_tool_calls_per_analyst=4,
        max_tool_calls_total=50,
    ):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.max_tool_calls_per_analyst = max_tool_calls_per_analyst
        self.max_tool_calls_total = max_tool_calls_total

    def _can_use_tools(self, state: AgentState, analyst_key: str) -> bool:
        """
        Read-only tool-loop guard.

        Counter mutation is handled in analyst node return payloads so state updates
        are deterministic under LangGraph reducers.
        """
        rounds = (
            state.get("tool_round_counts")
            or state.get("tool_call_counts")
            or {}
        )
        per = int(rounds.get(analyst_key, 0) or 0)
        total = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)

        if self.max_tool_calls_total > 0 and total >= self.max_tool_calls_total:
            return False
        if self.max_tool_calls_per_analyst > 0 and per >= self.max_tool_calls_per_analyst:
            return False
        return True

    def _tool_route(
        self,
        state: AgentState,
        analyst_key: str,
        tools_node: str,
        clear_node: str,
        force_finalize_node: str,
    ) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if getattr(last_message, "tool_calls", None):
            if self._can_use_tools(state, analyst_key):
                return tools_node
            return force_finalize_node
        return clear_node

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        return self._tool_route(
            state,
            analyst_key="market",
            tools_node="tools_market",
            clear_node="Msg Clear Market",
            force_finalize_node="Force Finalize Market",
        )

    def should_continue_social(self, state: AgentState):
        """Determine if social media analysis should continue."""
        return self._tool_route(
            state,
            analyst_key="social",
            tools_node="tools_social",
            clear_node="Msg Clear Social",
            force_finalize_node="Force Finalize Social",
        )

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        return self._tool_route(
            state,
            analyst_key="news",
            tools_node="tools_news",
            clear_node="Msg Clear News",
            force_finalize_node="Force Finalize News",
        )

    def should_continue_catalyst(self, state: AgentState):
        """Determine if catalyst/event-risk analysis should continue."""
        return self._tool_route(
            state,
            analyst_key="catalyst",
            tools_node="tools_catalyst",
            clear_node="Msg Clear Catalyst",
            force_finalize_node="Force Finalize Catalyst",
        )

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        return self._tool_route(
            state,
            analyst_key="fundamentals",
            tools_node="tools_fundamentals",
            clear_node="Msg Clear Fundamentals",
            force_finalize_node="Force Finalize Fundamentals",
        )

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Risk Judge"
        if state["risk_debate_state"]["latest_speaker"].startswith("Risky"):
            return "Safe Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Safe"):
            return "Neutral Analyst"
        return "Risky Analyst"
