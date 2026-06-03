import logging

from opentrace.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from opentrace.agents.utils.agent_runtime.evidence_graph import format_evidence_projection
from opentrace.graph.debate_schema import require_risk_response_contract


logger = logging.getLogger(__name__)


def create_risky_debator(llm):
    def risky_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        risky_history = risk_debate_state.get("risky_history", "")

        current_safe_response = risk_debate_state.get("current_safe_response", "")
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
                    "Safe analyst latest:\n"
                    + cap_section(
                        "current_safe_response",
                        current_safe_response,
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
        prompt_diagnostics("risky_debator", sections, clipped)
        if clipped:
            logger.debug("Risky debator prompt sections were clipped by context budget.")

        prompt = f"""As the Risky Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the trader's decision or plan, focus intently on the potential upside, growth potential, and innovative benefits-even when these come with elevated risk. Use the provided market data and sentiment analysis to strengthen your arguments and challenge the opposing views. Specifically, respond directly to each point made by the conservative and neutral analysts, countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative. Here is the trader's decision:

{sections["trader_plan"]}

Your task is to create a compelling case for the trader's decision by questioning and critiquing the conservative and neutral stances to demonstrate why your high-reward perspective offers the best path forward. Incorporate insights from the following evidence graph projection into your arguments:

{sections["reports"]}
Here is the current conversation history: {sections["history_tail"]} {sections["current_response"]}. If there are no responses from the other viewpoints, do not halluncinate and just present your point.

Engage actively by addressing any specific concerns raised, refuting the weaknesses in their logic, and asserting the benefits of risk-taking to outpace market norms. Maintain a focus on debating and persuading, not just presenting data. Challenge each counterpoint to underscore why a high-risk approach is optimal.

RISK PATCH CONTRACT:
- End with exactly one of: PLAN_PATCH, REJECT_PATCH, or NO_MATERIAL_CHANGE.
- A PLAN_PATCH must modify one executable field: action, execution_mode, order_type, entry_price, entry_condition, stop_loss, take_profit, position_size_pct, max_loss_pct, trigger_condition, time_horizon, or invalidation_condition.
- Every PLAN_PATCH must cite evidence IDs from the evidence projection.
- Format PLAN_PATCH as valid JSON with patch_id, author, target_plan_version, patch_type, field, old_value, new_value, evidence_ids, reason, expected_effect, and materiality.
- Do not provide general commentary unless it is attached to a patch, rejection, or no-op rationale."""

        response = llm.invoke(prompt)
        require_risk_response_contract(response.content, stage="risky_debator")

        argument = f"Risky Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "risky_history": risky_history + "\n" + argument,
            "safe_history": risk_debate_state.get("safe_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Risky",
            "current_risky_response": argument,
            "current_safe_response": risk_debate_state.get("current_safe_response", ""),
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return risky_node
