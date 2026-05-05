import logging

from tradingagents.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from tradingagents.agents.utils.agent_runtime.evidence_graph import format_evidence_projection


logger = logging.getLogger(__name__)


def create_safe_debator(llm):
    def safe_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        safe_history = risk_debate_state.get("safe_history", "")

        current_risky_response = risk_debate_state.get("current_risky_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

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
                    "Neutral analyst latest:\n"
                    + cap_section(
                        "current_neutral_response",
                        current_neutral_response,
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
        prompt_diagnostics("safe_debator", sections, clipped)
        if clipped:
            logger.debug("Safe debator prompt sections were clipped by context budget.")

        prompt = f"""As the Safe/Conservative Risk Analyst, your primary objective is to protect assets, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation, carefully assessing potential losses, economic downturns, and market volatility. When evaluating the trader's decision or plan, critically examine high-risk elements, pointing out where the decision may expose the firm to undue risk and where more cautious alternatives could secure long-term gains. Here is the trader's decision:

{sections["trader_plan"]}

Your task is to actively counter the arguments of the Risky and Neutral Analysts, highlighting where their views may overlook potential threats or fail to prioritize sustainability. Respond directly to their points, drawing from the following evidence graph projection to build a convincing case for a low-risk approach adjustment to the trader's decision:

{sections["reports"]}
Here is the current conversation history: {sections["history_tail"]} {sections["current_response"]}. If there are no responses from the other viewpoints, do not halluncinate and just present your point.

Engage by questioning their optimism and emphasizing the potential downsides they may have overlooked. Address each of their counterpoints to showcase why a conservative stance is ultimately the safest path for the firm's assets. Focus on debating and critiquing their arguments to demonstrate the strength of a low-risk strategy over their approaches. Output conversationally as if you are speaking without any special formatting."""

        response = llm.invoke(prompt)

        argument = f"Safe Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "risky_history": risk_debate_state.get("risky_history", ""),
            "safe_history": safe_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Safe",
            "current_risky_response": risk_debate_state.get(
                "current_risky_response", ""
            ),
            "current_safe_response": argument,
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return safe_node
