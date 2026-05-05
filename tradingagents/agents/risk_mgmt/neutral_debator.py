import logging

from tradingagents.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from tradingagents.agents.utils.agent_runtime.evidence_graph import format_evidence_projection


logger = logging.getLogger(__name__)


def create_neutral_debator(llm):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        current_risky_response = risk_debate_state.get("current_risky_response", "")
        current_safe_response = risk_debate_state.get("current_safe_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        trader_decision = state["trader_investment_plan"]

        settings = get_budget_settings()
        sections_before = {
            "trader_plan": cap_section(
                "trader_plan", trader_decision, settings["section_max_chars_trader_plan"]
            ),
            "history_tail": cap_section(
                "history_tail", history, settings["section_max_chars_history"]
            ),
            "current_response": "\n\n".join(
                [
                    "Risky analyst latest:\n"
                    + cap_section(
                        "current_risky_response",
                        current_risky_response,
                        settings["section_max_chars_response"],
                    ),
                    "Safe analyst latest:\n"
                    + cap_section(
                        "current_safe_response",
                        current_safe_response,
                        settings["section_max_chars_response"],
                    ),
                ]
            ),
            "reports": format_evidence_projection(state, "risk"),
            "memories": "",
            "portfolio_context": "",
        }
        sections = cap_sections_with_soft_token_cap(
            sections_before, settings["soft_cap_tokens"]
        )
        clipped = sections != sections_before
        prompt_diagnostics("neutral_debator", sections, clipped)
        if clipped:
            logger.debug("Neutral debator prompt sections were clipped by context budget.")

        prompt = f"""As the Neutral Risk Analyst, your role is to provide a balanced perspective, weighing both the potential benefits and risks of the trader's decision or plan. You prioritize a well-rounded approach, evaluating the upsides and downsides while factoring in broader market trends, potential economic shifts, and diversification strategies.Here is the trader's decision:

{sections["trader_plan"]}

Your task is to challenge both the Risky and Safe Analysts, pointing out where each perspective may be overly optimistic or overly cautious. Use insights from the following evidence graph projection to support a moderate, sustainable strategy to adjust the trader's decision:

{sections["reports"]}
Here is the current conversation history: {sections["history_tail"]} {sections["current_response"]}. If there are no responses from the other viewpoints, do not halluncinate and just present your point.

Engage actively by analyzing both sides critically, addressing weaknesses in the risky and conservative arguments to advocate for a more balanced approach. Challenge each of their points to illustrate why a moderate risk strategy might offer the best of both worlds, providing growth potential while safeguarding against extreme volatility. Focus on debating rather than simply presenting data, aiming to show that a balanced view can lead to the most reliable outcomes. Output conversationally as if you are speaking without any special formatting."""

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "risky_history": risk_debate_state.get("risky_history", ""),
            "safe_history": risk_debate_state.get("safe_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_risky_response": risk_debate_state.get(
                "current_risky_response", ""
            ),
            "current_safe_response": risk_debate_state.get("current_safe_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
