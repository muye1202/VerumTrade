import time
import json
import logging

from opentrace.dataflows.config import get_config
from opentrace.agents.utils.llm.llm_rate_limit import invoke_with_backoff
from opentrace.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from opentrace.agents.utils.agent_runtime.evidence_graph import format_evidence_projection
from opentrace.execution.decision_guard import build_market_snapshot
from opentrace.graph.debate_schema import require_valid_thesis_ledger


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
        market_snapshot = state.get("market_snapshot") or build_market_snapshot(
            symbol=state.get("company_of_interest", ""),
            market_report=market_research_report,
            quote=None,
            structured_decision=None,
            snapshot_source=config.get("decision_snapshot_source", "executor_quote_first"),
        )

        settings = get_budget_settings()
        sections_before = {
            "history_tail": cap_section(
                "history_tail", history, settings["section_max_chars_history"]
            ),
            "memories": cap_section(
                "memories", past_memory_str, settings["section_max_chars_memory"]
            ),
            "reports": format_evidence_projection(state, "research_manager"),
            "research_turns": cap_section(
                "research_turns",
                json.dumps(state.get("research_debate_turns") or [], ensure_ascii=False, indent=2),
                settings["section_max_chars_response"],
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
Sizing Guidance: Recommend an appropriate position size. The system will NOT ask the user for a sizing percentage; the trader may either specify an explicit share QUANTITY or omit QUANTITY and instead provide POSITION_SIZE_PCT (interpreted as % of available capital/effective buying power).
Thesis Ledger: Include a compact machine-readable JSON object named THESIS_LEDGER_JSON with winning_thesis, accepted_claims, rejected_claims, unresolved_uncertainties, and recommended_plan_constraints. Every accepted claim must cite evidence IDs or inference IDs from the evidence graph projection.
Take into account your past mistakes on similar situations. Use these insights to refine your decision-making and ensure you are learning and improving. Present your analysis conversationally, as if speaking naturally, without special formatting. 
 
Here are your past reflections on mistakes:
\"{sections["memories"]}\"

Here is the evidence graph projection. Cite inference IDs when selecting the thesis:
{sections["reports"]}

Canonical market snapshot for price anchoring:
{market_snapshot}

Here is the debate:
Debate History:
{sections["history_tail"]}

Accepted structured debate turns:
{sections["research_turns"]}"""

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

        thesis_ledger = _extract_thesis_ledger(response.content)
        thesis_validation = require_valid_thesis_ledger(
            thesis_ledger,
            stage="research_manager",
            evidence_ids=[
                str(item.get("evidence_id"))
                for item in state.get("evidence_ledger", []) or []
                if isinstance(item, dict) and item.get("evidence_id")
            ],
        )

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
            "thesis_ledger": thesis_ledger,
            "thesis_ledger_validation": thesis_validation,
            "market_snapshot": market_snapshot,
        }

    return research_manager_node


def _extract_thesis_ledger(text: str) -> dict:
    marker = "THESIS_LEDGER_JSON"
    content = str(text or "")
    idx = content.upper().find(marker)
    if idx < 0:
        return {}
    tail = content[idx + len(marker) :]
    start = tail.find("{")
    if start < 0:
        return {}
    depth = 0
    end = -1
    for pos, char in enumerate(tail[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end < 0:
        return {}
    try:
        parsed = json.loads(tail[start:end])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
