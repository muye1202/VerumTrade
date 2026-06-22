from langchain_core.messages import AIMessage
import time
import json
import logging

from opentrace.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from opentrace.agents.utils.agent_runtime.evidence_graph import format_evidence_projection
from opentrace.dataflows.config import get_config
from opentrace.graph.debate_schema import (
    debate_validation_context_from_state,
    degrade_or_raise,
    evaluate_research_turns,
    format_contract_violation,
    intermediate_gates_hard,
    invoke_with_contract_repair,
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
            "reports": format_evidence_projection(state, "bear"),
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

Evidence graph projection available:

{sections["reports"]}
Conversation history of the debate: {sections["history_tail"]}
Last bull argument: {sections["current_response"]}
Reflections from similar situations and lessons learned: {sections["memories"]}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the stock. You must also address reflections and learn from lessons and mistakes you made in the past.

DEBATE CONTRACT:
- Every material claim must cite at least one evidence ID from the evidence graph projection.
- For each material claim, name the decision field affected: action, execution_mode, order_type, entry_condition, stop_loss, take_profit, position_size_pct, trigger_condition, time_horizon, or invalidation_condition.
- Include a concrete plan implication and falsification condition.
- If you cannot cite admissible evidence for a claim, write NO_ADMISSIBLE_EVIDENCE and do not use that claim to support the plan.
- End with RESEARCH_DEBATE_TURN_JSON followed by valid JSON containing turn_id, speaker, issue_id, position, claim, evidence_ids, rebuttal_to, plan_implication, falsification_condition, and confidence.
- plan_implication MUST be an object, not prose:
  {{"field":"execution_mode","proposed_value":"wait_for_trigger","rationale":"Avoid chasing overbought momentum."}}
- Use exactly one field per debate turn. If several fields are affected, choose the most important one.
"""

        config = get_config()
        debate_context = debate_validation_context_from_state(state)

        def _evaluate(content: str):
            return evaluate_research_turns(
                content,
                evidence_ids=debate_context["evidence_ids"],
                active_issue_ids=debate_context["issue_ids"],
                evidence_aliases=debate_context["evidence_aliases"],
                active_issues=debate_context["issues"],
            )

        def _check(content: str):
            _, violation = _evaluate(content)
            return format_contract_violation(violation)

        response = invoke_with_contract_repair(
            prompt,
            stage="bear_researcher",
            invoke=llm.invoke,
            check=_check,
            max_repair_attempts=int(
                config.get("debate_contract_repair_attempts", 2) or 0
            ),
        )

        argument = f"Bear Analyst: {response.content}"
        validation, violation = _evaluate(response.content)
        if violation:
            reason, detail = violation
            degraded = degrade_or_raise(
                "bear_researcher", reason, detail, hard=intermediate_gates_hard(config)
            )
            if degraded:
                validation = {**validation, "gate_degradation": degraded}
        all_turns = [*(state.get("research_debate_turns") or []), *validation["accepted_turns"]]

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "research_debate_turns": all_turns,
            "research_debate_validation": validation,
        }

    return bear_node
