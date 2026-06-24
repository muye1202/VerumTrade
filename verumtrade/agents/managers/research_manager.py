import time
import json
import logging

from verumtrade.dataflows.config import get_config
from verumtrade.agents.utils.llm.llm_rate_limit import invoke_with_backoff
from verumtrade.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from verumtrade.agents.utils.agent_runtime.evidence_graph import format_evidence_projection
from verumtrade.execution.decision_guard import build_market_snapshot
from verumtrade.graph.debate_schema import (
    debate_validation_context_from_state,
    degrade_or_raise,
    intermediate_gates_hard,
    invoke_with_contract_repair,
    validate_thesis_ledger,
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
Thesis Ledger: Include a compact machine-readable JSON object named THESIS_LEDGER_JSON with winning_thesis, accepted_claims, rejected_claims, unresolved_uncertainties, and recommended_plan_constraints. Every accepted claim must cite canonical E-* evidence IDs from the ADMISSIBLE EVIDENCE LEDGER or inference IDs from the evidence graph projection. Do not use source_ref values when an E-* evidence ID is available.
THESIS_LEDGER_JSON schema:
{{
  "winning_thesis": "string",
  "accepted_claims": [
    {{
      "claim_id": "C-001",
      "claim": "string",
      "evidence_ids": ["E-MKT-001"],
      "effect": "execution_mode=wait_for_trigger"
    }}
  ],
  "rejected_claims": [
    {{
      "claim_id": "C-002",
      "reason": "string",
      "evidence_ids": ["E-MKT-002"]
    }}
  ],
  "unresolved_uncertainties": [
    {{
      "uncertainty": "string",
      "decision_effect": "may widen stop_loss"
    }}
  ],
  "recommended_plan_constraints": {{
    "execution_mode": "wait_for_trigger"
  }}
}}
Each unresolved_uncertainties item MUST be an object with "uncertainty" and "decision_effect" string fields (never a bare string). Use [] when there are none.
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

        debate_context = debate_validation_context_from_state(state)

        def _invoke(repair_prompt: str):
            return invoke_with_backoff(
                llm,
                repair_prompt,
                key="research_manager",
                min_interval_s=float(config.get("research_manager_min_delay_s", 0.0) or 0.0),
                max_retries=int(config.get("research_manager_max_retries", 6) or 6),
                base_backoff_s=float(config.get("research_manager_backoff_base_s", 1.0) or 1.0),
                max_backoff_s=float(config.get("research_manager_backoff_max_s", 30.0) or 30.0),
            )

        def _check(content: str):
            ledger = _normalize_thesis_ledger(
                _extract_thesis_ledger(content),
                state.get("research_debate_turns") or [],
                evidence_aliases=debate_context["evidence_aliases"],
            )
            result = validate_thesis_ledger(
                ledger,
                evidence_ids=debate_context["evidence_ids"],
                evidence_aliases=debate_context["evidence_aliases"],
            )
            return None if result["valid"] else result["violations"]

        response = invoke_with_contract_repair(
            prompt,
            stage="research_manager",
            invoke=_invoke,
            check=_check,
            max_repair_attempts=int(
                config.get("debate_contract_repair_attempts", 2) or 0
            ),
        )

        new_investment_debate_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": response.content,
            "count": investment_debate_state["count"],
        }

        thesis_ledger = _normalize_thesis_ledger(
            _extract_thesis_ledger(response.content),
            state.get("research_debate_turns") or [],
            evidence_aliases=debate_context["evidence_aliases"],
        )
        thesis_validation = validate_thesis_ledger(
            thesis_ledger,
            evidence_ids=debate_context["evidence_ids"],
            evidence_aliases=debate_context["evidence_aliases"],
        )
        if not thesis_validation["valid"]:
            degraded = degrade_or_raise(
                "research_manager",
                "invalid THESIS_LEDGER_JSON",
                thesis_validation["violations"],
                hard=intermediate_gates_hard(config),
            )
            if degraded:
                thesis_validation = {**thesis_validation, "gate_degradation": degraded}

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
            "thesis_ledger": thesis_ledger,
            "thesis_ledger_validation": thesis_validation,
            "market_snapshot": market_snapshot,
        }

    return research_manager_node


def _normalize_thesis_ledger(
    thesis_ledger: dict,
    research_turns: list[dict],
    *,
    evidence_aliases: dict[str, list[str]] | None = None,
) -> dict:
    if not isinstance(thesis_ledger, dict) or not thesis_ledger:
        return {}
    normalized = dict(thesis_ledger)
    aliases = evidence_aliases or {}
    accepted_claims = normalized.get("accepted_claims")
    if isinstance(accepted_claims, list):
        normalized_claims = []
        for idx, raw_claim in enumerate(accepted_claims, 1):
            if not isinstance(raw_claim, dict):
                normalized_claims.append(raw_claim)
                continue
            claim = dict(raw_claim)
            if not str(claim.get("claim_id") or "").strip():
                claim["claim_id"] = f"C-{idx:03d}"
            claim["evidence_ids"] = _normalize_evidence_refs(
                claim.get("evidence_ids") or [],
                aliases,
            )
            if not str(claim.get("effect") or "").strip():
                effect = _effect_for_claim(claim, research_turns)
                if effect:
                    claim["effect"] = effect
            normalized_claims.append(claim)
        normalized["accepted_claims"] = normalized_claims

    uncertainties = normalized.get("unresolved_uncertainties")
    if isinstance(uncertainties, list):
        normalized["unresolved_uncertainties"] = [
            item
            for item in (_coerce_uncertainty(raw) for raw in uncertainties)
            if item is not None
        ]

    rejected_claims = normalized.get("rejected_claims")
    if isinstance(rejected_claims, list):
        normalized_rejected = []
        for idx, raw_claim in enumerate(rejected_claims, 1):
            claim = _coerce_rejected_claim(raw_claim, idx)
            if claim is not None:
                claim["evidence_ids"] = _normalize_evidence_refs(
                    claim.get("evidence_ids") or [],
                    aliases,
                )
                normalized_rejected.append(claim)
        normalized["rejected_claims"] = normalized_rejected

    constraints = normalized.get("recommended_plan_constraints")
    if not isinstance(constraints, dict) or not constraints:
        derived = _constraints_from_claims(normalized.get("accepted_claims") or [])
        if not derived:
            derived = _constraints_from_turns(research_turns)
        if derived:
            normalized["recommended_plan_constraints"] = derived
    return normalized


def _normalize_evidence_refs(
    refs: list,
    evidence_aliases: dict[str, list[str]],
) -> list[str]:
    normalized: list[str] = []
    for raw_ref in refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        normalized.extend(evidence_aliases.get(ref, [ref]))
    return list(dict.fromkeys(normalized))


def _coerce_uncertainty(raw) -> dict | None:
    """Heal an unresolved_uncertainties item into the {uncertainty, decision_effect} shape.

    Models frequently emit bare strings here; wrap them rather than hard-faulting,
    since this field is advisory and does not feed the trade decision.
    """
    if isinstance(raw, dict):
        uncertainty = str(raw.get("uncertainty") or "").strip()
        if not uncertainty:
            return None
        return {
            "uncertainty": uncertainty,
            "decision_effect": str(raw.get("decision_effect") or "").strip(),
        }
    text = str(raw or "").strip()
    if not text:
        return None
    return {"uncertainty": text, "decision_effect": ""}


def _coerce_rejected_claim(raw, idx: int) -> dict | None:
    """Heal a rejected_claims item into the {claim_id, reason, evidence_ids} shape."""
    if isinstance(raw, dict):
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            return None
        claim_id = str(raw.get("claim_id") or "").strip() or f"R-{idx:03d}"
        evidence_ids = [str(item) for item in raw.get("evidence_ids") or [] if str(item)]
        return {"claim_id": claim_id, "reason": reason, "evidence_ids": evidence_ids}
    reason = str(raw or "").strip()
    if not reason:
        return None
    return {"claim_id": f"R-{idx:03d}", "reason": reason, "evidence_ids": []}


def _effect_for_claim(claim: dict, research_turns: list[dict]) -> str:
    claim_refs = {str(item) for item in claim.get("evidence_ids") or [] if str(item)}
    claim_text = str(claim.get("claim") or "").strip().lower()
    best_turn = None
    best_score = 0
    for turn in research_turns:
        if not isinstance(turn, dict):
            continue
        turn_refs = {str(item) for item in turn.get("evidence_ids") or [] if str(item)}
        overlap = len(claim_refs & turn_refs)
        if claim_text and claim_text == str(turn.get("claim") or "").strip().lower():
            overlap += 2
        if overlap > best_score:
            best_score = overlap
            best_turn = turn
    implication = best_turn.get("plan_implication") if isinstance(best_turn, dict) else None
    if not isinstance(implication, dict):
        return ""
    field = str(implication.get("field") or "").strip()
    proposed_value = str(implication.get("proposed_value") or "").strip()
    if not field or not proposed_value:
        return ""
    return f"{field}={proposed_value}"


def _constraints_from_claims(accepted_claims: list) -> dict:
    constraints = {}
    for claim in accepted_claims:
        if not isinstance(claim, dict):
            continue
        effect = str(claim.get("effect") or "").strip()
        if "=" not in effect:
            continue
        field, value = effect.split("=", 1)
        field = field.strip()
        value = value.strip()
        if field and value:
            constraints[field] = value
    return constraints


def _constraints_from_turns(research_turns: list[dict]) -> dict:
    constraints = {}
    for turn in research_turns:
        if not isinstance(turn, dict):
            continue
        implication = turn.get("plan_implication")
        if not isinstance(implication, dict):
            continue
        field = str(implication.get("field") or "").strip()
        proposed_value = implication.get("proposed_value")
        if field and proposed_value is not None:
            constraints[field] = proposed_value
    return constraints


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
