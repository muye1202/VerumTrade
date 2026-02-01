# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(
        self,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        max_tool_calls_per_analyst=8,
        max_tool_calls_total=50,
    ):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.max_tool_calls_per_analyst = max_tool_calls_per_analyst
        self.max_tool_calls_total = max_tool_calls_total

    def _should_use_tools(self, state: AgentState, analyst_key: str) -> bool:
        # Initialize fields for older saved states.
        state.setdefault("tool_call_counts", {})
        state.setdefault("tool_call_total", 0)

        per = state["tool_call_counts"].get(analyst_key, 0)
        total = state["tool_call_total"]

        if total >= self.max_tool_calls_total:
            return False
        if per >= self.max_tool_calls_per_analyst:
            return False

        # Mutate counters in-state so the guard is effective across loop iterations.
        state["tool_call_counts"][analyst_key] = per + 1
        state["tool_call_total"] = total + 1
        return True

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            if self._should_use_tools(state, "market"):
                return "tools_market"
            return "Msg Clear Market"
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """Determine if social media analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            if self._should_use_tools(state, "social"):
                return "tools_social"
            return "Msg Clear Social"
        return "Msg Clear Social"

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            if self._should_use_tools(state, "news"):
                return "tools_news"
            return "Msg Clear News"
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            if self._should_use_tools(state, "fundamentals"):
                return "tools_fundamentals"
            return "Msg Clear Fundamentals"
        return "Msg Clear Fundamentals"

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
