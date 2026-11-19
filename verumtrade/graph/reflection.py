# Verumtrade/graph/reflection.py

import json
from typing import Dict, Any
from langchain_openai import ChatOpenAI


class Reflector:
    """Handles reflection on decisions and updating memory."""

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm
        self.reflection_system_prompt = self._get_reflection_prompt()

    def _get_reflection_prompt(self) -> str:
        """Get the system prompt for reflection."""
        return """
You are an expert financial analyst tasked with reviewing trading decisions/analysis and providing a comprehensive, step-by-step analysis. 
Your goal is to deliver detailed insights into investment decisions and highlight opportunities for improvement, adhering strictly to the following guidelines:

1. Reasoning (keep these three judgments separate):
   - Forecast accuracy: compare the recorded forecasts with how they resolved (see FORECAST RECORD). A forecast is judged against its own resolution rule, not against the trade's profit.
   - Decision quality: was the decision sound given the evidence available at the time? A profitable outcome does not prove the reasoning was good, and a loss does not prove it was bad; one realization cannot settle a probabilistic judgment.
   - Outcome and execution: what the returns were, and how much came from entry, sizing, stops, gaps, or the market rather than from the thesis.
   - Analyze the contributing factors to each success or mistake. Consider:
     - Market intelligence.
     - Technical indicators.
     - Technical signals.
     - Price movement analysis.
     - Overall market data analysis 
     - News analysis.
     - Social media and sentiment analysis.
     - Fundamental data analysis.
     - Weight the importance of each factor in the decision-making process.

2. Improvement:
   - Propose changes only where the reasoning, evidence handling, or forecasts were at fault given what was knowable then; do not fit lessons to a single lucky or unlucky outcome.
   - Provide a list of corrective actions, each tied to the specific forecast, evidence, or decision step it addresses.

3. Summary:
   - Summarize the lessons learned from the successes and mistakes.
   - Highlight how these lessons can be adapted for future trading scenarios and draw connections between similar situations to apply the knowledge gained.

4. Query:
   - Extract key insights from the summary into a concise sentence of no more than 1000 tokens.
   - Ensure the condensed sentence captures the essence of the lessons and reasoning for easy reference.

Adhere strictly to these instructions, and ensure your output is detailed, accurate, and actionable. You will also be given objective descriptions of the market from a price movements, technical indicator, news, and sentiment perspective to provide more context for your analysis.
"""

    def _extract_current_situation(self, current_state: Dict[str, Any]) -> str:
        """Extract the current market situation from the state."""
        curr_market_report = current_state["market_report"]
        curr_sentiment_report = current_state["sentiment_report"]
        curr_news_report = current_state["news_report"]
        curr_fundamentals_report = current_state["fundamentals_report"]

        return f"{curr_market_report}\n\n{curr_sentiment_report}\n\n{curr_news_report}\n\n{curr_fundamentals_report}"

    def _forecast_record(self, current_state: Dict[str, Any]) -> str:
        """Resolved forecasts for this ticker, scored separately from returns."""
        try:
            from verumtrade.research.revision import get_thesis_store, thesis_store_enabled

            if not thesis_store_enabled():
                return "No forecast store configured."
            store = get_thesis_store()
            ticker = str(current_state.get("company_of_interest") or "")
            forecasts = {f["forecast_id"]: f for f in store.forecasts(ticker)}
            lines = [json.dumps(store.forecast_scorecard(ticker), ensure_ascii=False)]
            for resolution in store.resolutions(list(forecasts))[-12:]:
                forecast = forecasts.get(resolution["forecast_id"]) or {}
                line = f"- [{forecast.get('kind')}] {forecast.get('proposition')} -> {resolution['outcome']}"
                if resolution.get("brier") is not None:
                    line += f" (Brier {resolution['brier']})"
                lines.append(line)
            return "\n".join(lines)
        except Exception as exc:  # the record is context, never a reason to skip reflection
            return f"Forecast record unavailable: {type(exc).__name__}"

    def _reflect_on_component(
        self, component_type: str, report: str, situation: str, returns_losses, forecast_record: str = ""
    ) -> str:
        """Generate reflection for a component."""
        messages = [
            ("system", self.reflection_system_prompt),
            (
                "human",
                f"Returns: {returns_losses}\n\nFORECAST RECORD:\n{forecast_record or 'Not available.'}"
                f"\n\nAnalysis/Decision: {report}\n\nObjective Market Reports for Reference: {situation}",
            ),
        ]

        result = self.quick_thinking_llm.invoke(messages).content
        return result

    def reflect_bull_researcher(self, current_state, returns_losses, bull_memory):
        """Reflect on bull researcher's analysis and update memory."""
        situation = self._extract_current_situation(current_state)
        bull_debate_history = current_state["investment_debate_state"]["bull_history"]

        result = self._reflect_on_component(
            "BULL", bull_debate_history, situation, returns_losses,
            self._forecast_record(current_state),
        )
        bull_memory.add_situations([(situation, result)])

    def reflect_bear_researcher(self, current_state, returns_losses, bear_memory):
        """Reflect on bear researcher's analysis and update memory."""
        situation = self._extract_current_situation(current_state)
        bear_debate_history = current_state["investment_debate_state"]["bear_history"]

        result = self._reflect_on_component(
            "BEAR", bear_debate_history, situation, returns_losses,
            self._forecast_record(current_state),
        )
        bear_memory.add_situations([(situation, result)])

    def reflect_trader(self, current_state, returns_losses, trader_memory):
        """Reflect on trader's decision and update memory."""
        situation = self._extract_current_situation(current_state)
        trader_decision = current_state["trader_investment_plan"]

        result = self._reflect_on_component(
            "TRADER", trader_decision, situation, returns_losses,
            self._forecast_record(current_state),
        )
        trader_memory.add_situations([(situation, result)])

    def reflect_invest_judge(self, current_state, returns_losses, invest_judge_memory):
        """Reflect on investment judge's decision and update memory."""
        situation = self._extract_current_situation(current_state)
        judge_decision = current_state["investment_debate_state"]["judge_decision"]

        result = self._reflect_on_component(
            "INVEST JUDGE", judge_decision, situation, returns_losses,
            self._forecast_record(current_state),
        )
        invest_judge_memory.add_situations([(situation, result)])

    def reflect_risk_manager(self, current_state, returns_losses, risk_manager_memory):
        """Reflect on risk manager's decision and update memory."""
        situation = self._extract_current_situation(current_state)
        judge_decision = current_state["risk_debate_state"]["judge_decision"]

        result = self._reflect_on_component(
            "RISK JUDGE", judge_decision, situation, returns_losses,
            self._forecast_record(current_state),
        )
        risk_manager_memory.add_situations([(situation, result)])
