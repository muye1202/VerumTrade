import time
import json
import logging

from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm_rate_limit import invoke_with_backoff
from tradingagents.agents.utils.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)


logger = logging.getLogger(__name__)


def create_research_manager(llm, memory):
    def research_manager_node(state) -> dict:
        config = get_config()
        history = state["investment_debate_state"].get("history", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        investment_debate_state = state["investment_debate_state"]

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
            "memories": cap_section(
                "memories", past_memory_str, settings["section_max_chars_memory"]
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
        }
        sections = cap_sections_with_soft_token_cap(
            sections_before, settings["soft_cap_tokens"]
        )
        clipped = sections != sections_before
        prompt_diagnostics("research_manager", sections, clipped)
        if clipped:
            logger.debug("Research manager prompt sections were clipped by context budget.")

        prompt = f"""As the portfolio manager and debate facilitator, your role is to critically evaluate this round of debate and make a definitive decision: align with the bear analyst, the bull analyst, or choose Hold only if it is strongly justified based on the arguments presented.

Summarize the key points from both sides concisely, focusing on the most compelling evidence or reasoning. Your recommendation—Buy, Sell, or Hold—must be clear and actionable. Avoid defaulting to Hold simply because both sides have valid points; commit to a stance grounded in the debate's strongest arguments.

Additionally, develop a detailed investment plan for the trader. This should include:

Your Recommendation: A decisive stance supported by the most convincing arguments.
Rationale: An explanation of why these arguments lead to your conclusion.
Strategic Actions: Concrete steps for implementing the recommendation.
Sizing Guidance: Recommend an appropriate position size. The system will NOT ask the user for a sizing percentage; the trader may either specify an explicit share QUANTITY or omit QUANTITY and instead provide POSITION_SIZE_PCT (interpreted as % of available cash).
Take into account your past mistakes on similar situations. Use these insights to refine your decision-making and ensure you are learning and improving. Present your analysis conversationally, as if speaking naturally, without special formatting. 
 
Here are your past reflections on mistakes:
\"{sections["memories"]}\"

Here are compacted analyst reports:
{sections["reports"]}

Here is the debate:
Debate History:
{sections["history_tail"]}"""

        response = invoke_with_backoff(
            llm,
            prompt,
            key="research_manager",
            min_interval_s=float(config.get("research_manager_min_delay_s", 0.0) or 0.0),
            max_retries=int(config.get("research_manager_max_retries", 6) or 6),
            base_backoff_s=float(config.get("research_manager_backoff_base_s", 1.0) or 1.0),
            max_backoff_s=float(config.get("research_manager_backoff_max_s", 30.0) or 30.0),
        )

        new_investment_debate_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": response.content,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

    return research_manager_node
