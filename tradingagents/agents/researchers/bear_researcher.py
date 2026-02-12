from langchain_core.messages import AIMessage
import time
import json
import logging

from tradingagents.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)


logger = logging.getLogger(__name__)


def create_bear_researcher(llm, memory):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        settings = get_budget_settings()
        sections_before = {
            "history_tail": cap_section(
                "history_tail", history, settings["section_max_chars_history"]
            ),
            "current_response": cap_section(
                "current_response",
                current_response,
                settings["section_max_chars_response"],
            ),
            "reports": "\n\n".join(
                [
                    "Market research report:\n"
                    + cap_section(
                        "market_report",
                        market_research_report,
                        settings["section_max_chars_report"],
                    ),
                    "Social media sentiment report:\n"
                    + cap_section(
                        "sentiment_report",
                        sentiment_report,
                        settings["section_max_chars_report"],
                    ),
                    "Latest world affairs news:\n"
                    + cap_section(
                        "news_report", news_report, settings["section_max_chars_report"]
                    ),
                    "Company fundamentals report:\n"
                    + cap_section(
                        "fundamentals_report",
                        fundamentals_report,
                        settings["section_max_chars_report"],
                    ),
                ]
            ),
            "memories": cap_section(
                "memories", past_memory_str, settings["section_max_chars_memory"]
            ),
        }
        sections = cap_sections_with_soft_token_cap(
            sections_before, settings["soft_cap_tokens"]
        )
        clipped = sections != sections_before
        prompt_diagnostics("bear_researcher", sections, clipped)
        if clipped:
            logger.debug("Bear researcher prompt sections were clipped by context budget.")

        prompt = f"""You are a Bear Analyst making the case against investing in the stock. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{sections["reports"]}
Conversation history of the debate: {sections["history_tail"]}
Last bull argument: {sections["current_response"]}
Reflections from similar situations and lessons learned: {sections["memories"]}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the stock. You must also address reflections and learn from lessons and mistakes you made in the past.
"""

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
