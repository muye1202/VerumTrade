import logging
import json
import re

from verumtrade.dataflows.config import get_config
from verumtrade.agents.utils.llm.llm_rate_limit import invoke_with_backoff
from verumtrade.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from verumtrade.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from verumtrade.agents.utils.agent_runtime.evidence_graph import (
    build_decision_trace,
    format_evidence_projection,
)
from verumtrade.agents.trader.decision_brief import build_trader_plan_v1
from verumtrade.execution.decision_guard import build_market_snapshot
from verumtrade.agents.utils.market_data.macro_regime import format_macro_regime_markdown
from verumtrade.agents.utils.market_data.pullback_vulnerability import (
    format_pullback_vulnerability_markdown,
    compute_positioning_gate,
)
from verumtrade.graph.evidence_ledger_schema import build_evidence_ledger
from verumtrade.graph.plan_patch_schema import (
    apply_valid_plan_patches,
    extract_plan_patches_from_text,
    validate_plan_patches,
)
from verumtrade.graph.debate_schema import invoke_with_contract_repair
from verumtrade.graph.decision_schema import (
    extract_decision_json_block,
    validate_final_decision_contract,
    validate_structured_decision,
)


logger = logging.getLogger(__name__)


def create_risk_manager(llm, memory):
    def risk_manager_node(state) -> dict:
        config = get_config()

        company_name = state["company_of_interest"]
        market_session_context = state.get("market_session_context", "")
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        trading_days_text = (
            f"~{spec.trading_days_range[0]}-{spec.trading_days_range[1]} trading days"
        )

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        sentiment_report = state["sentiment_report"]
        catalyst_report = state.get("catalyst_report", "")
        catalyst_structured = state.get("catalyst_event_report_structured", {})
        trader_plan = state.get("trader_investment_plan") or state["investment_plan"]
        trader_intent = _extract_trader_execution_intent(trader_plan)

        curr_situation = f"{catalyst_report}\n\n{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for rec in past_memories:
            past_memory_str += rec["recommendation"] + "\n\n"

        portfolio_context = state.get("portfolio_context", "")
        market_snapshot = state.get("market_snapshot", {}) or build_market_snapshot(
            symbol=company_name,
            market_report=market_research_report,
            quote=None,
            structured_decision=None,
            snapshot_source=config.get("decision_snapshot_source", "executor_quote_first"),
        )
        trader_plan_v1 = state.get("trader_plan_v1")
        if not isinstance(trader_plan_v1, dict) or not trader_plan_v1:
            trader_plan_v1 = build_trader_plan_v1(state)
        evidence_ledger = state.get("evidence_ledger")
        if not isinstance(evidence_ledger, list):
            evidence_ledger = build_evidence_ledger(state)
        risk_patches = extract_plan_patches_from_text(history)
        risk_patch_validation = validate_plan_patches(
            risk_patches,
            trader_plan=trader_plan_v1,
            evidence_ids=[
                str(item.get("evidence_id"))
                for item in evidence_ledger
                if isinstance(item, dict) and item.get("evidence_id")
            ],
        )
        enforced_trader_plan_v1 = apply_valid_plan_patches(
            trader_plan_v1,
            risk_patch_validation,
        )
        trader_plan_with_enforcement = (
            f"{trader_plan}\n\n"
            "ENFORCED TRADER PLAN V1 JSON:\n"
            f"{json.dumps(enforced_trader_plan_v1, ensure_ascii=False, indent=2)}"
        )

        settings = get_budget_settings()
        sections_before = {
            "trader_plan": cap_section(
                "trader_plan",
                trader_plan_with_enforcement,
                settings["section_max_chars_trader_plan"],
            ),
            "history_tail": cap_section(
                "history_tail", history, settings["section_max_chars_history"]
            ),
            "portfolio_context": cap_section(
                "portfolio_context",
                portfolio_context,
                settings["section_max_chars_portfolio"],
            ),
            "memories": cap_section(
                "memories", past_memory_str, settings["section_max_chars_memory"]
            ),
            "current_response": cap_section(
                "market_session_context",
                market_session_context,
                settings["section_max_chars_response"],
            ),
            "reports": format_evidence_projection(state, "risk"),
            "catalyst": cap_section(
                "catalyst",
                f"{catalyst_structured}\n\n{catalyst_report}",
                settings["section_max_chars_response"],
            ),
            "risk_patch_validation": cap_section(
                "risk_patch_validation",
                f"RISK PATCH VALIDATION JSON:\n{json.dumps(risk_patch_validation, ensure_ascii=False, indent=2)}",
                settings["section_max_chars_response"],
            ),
        }
        sections = cap_sections_with_soft_token_cap(
            sections_before, settings["soft_cap_tokens"]
        )
        clipped = sections != sections_before
        prompt_diagnostics("risk_manager", sections, clipped)
        if clipped:
            logger.debug("Risk manager prompt sections were clipped by context budget.")

        portfolio_block = ""
        if sections["portfolio_context"]:
            portfolio_block = f"""

---
**CURRENT PORTFOLIO STATE (from live brokerage):**
{sections["portfolio_context"]}

CRITICAL PORTFOLIO RULES:
1. If the portfolio shows ZERO shares, SELL is NOT valid. Choose BUY or HOLD only.
2. If there is an existing position, factor in unrealized P&L when deciding.
3. Size the recommendation relative to available capital (effective buying power) and concentration limits.
4. Do not recommend adding to a position exceeding 20% of portfolio value.
---
"""
        snapshot_block = ""
        if market_snapshot:
            snapshot_block = f"""
---
CANONICAL MARKET SNAPSHOT (anchor all prices to this):
{market_snapshot}
---
"""
        macro_regime_block = ""
        macro_regime_md = format_macro_regime_markdown(state.get("macro_regime", {}) or {})
        if macro_regime_md:
            macro_regime_block = f"""
---
MARKET REGIME / POSITIONING CONTEXT (cross-asset snapshot for this run):
{macro_regime_md}

Treat this as a pullback-risk overlay. If the tape is risk-off, rates are rising, oil is spiking,
or the ticker sits in a crowded/extended momentum sector, lean conservative on sizing and timing
(prefer reduced size, tighter invalidation, a v1 HOLD, or — only when you can name a concrete
trigger — a wait-for-trigger v2 plan) unless admissible evidence directly offsets the regime risk.
A crowded sector can unwind on a soft/second-order catalyst with no company-specific bad news — do
not assume single-name strength immunizes against it.
---
"""
        pullback_vuln = state.get("pullback_vulnerability", {}) or {}
        pullback_vuln_block = ""
        pullback_vuln_md = format_pullback_vulnerability_markdown(pullback_vuln)
        if pullback_vuln_md:
            pullback_vuln_block = f"""
---
PULLBACK VULNERABILITY (per-ticker, 0-100; higher = more vulnerable to a sharp pullback):
{pullback_vuln_md}

OVERRIDE RULE: If the rating is HIGH or CRITICAL, treat it as a conservative override on any
BUY/add: prefer reduced position size, a tighter stop / invalidation, a v1 HOLD, or — only when you
can name a concrete trigger — a wait-for-trigger (v2) entry, unless admissible evidence directly
offsets the vulnerability (e.g., a confirmed positive catalyst with durable follow-through). A high
score does NOT by itself force SELL/HOLD — it tempers sizing and entry aggressiveness for new
exposure and tightens risk controls on existing exposure. Explicitly state in your narrative whether
you follow or override this signal and why.
---
"""

        # Tier-4 deterministic positioning-risk gate: fires only when a HIGH/CRITICAL per-ticker
        # vulnerability coincides with a fragile tape (risk-off / rates-up / oil / foreign stress).
        # Symmetric to the catalyst-risk gate; produces an explicit, user-facing warning rather than
        # a silent HOLD. Computed in code so it is deterministic and surfaced in returned state.
        positioning_gate = {}
        positioning_gate_block = ""
        if bool(config.get("enable_positioning_gate", True)):
            positioning_gate = compute_positioning_gate(
                pullback_vuln, state.get("macro_regime", {}) or {}
            )
            if positioning_gate.get("triggered"):
                positioning_gate_block = f"""
---
POSITIONING-RISK GATE (TRIGGERED — {positioning_gate.get('severity', 'HIGH')}):
{positioning_gate.get('warning_text', '')}

This gate is symmetric to the catalyst-risk override and has ALREADY fired deterministically (the
conditions are met). You MUST address it explicitly: either (a) follow it — reduce size, tighten the
stop/invalidation, choose a v1 HOLD, or name a concrete wait-for-trigger (v2) entry — or (b) override
it, in which case you must cite the specific admissible evidence that offsets it and set
`override_reason` in the canonical JSON. Do not ignore it silently. It does NOT by itself force
SELL/HOLD; it tempers sizing/entry for new exposure and tightens controls on existing exposure.
---
"""

        prompt = f"""As the Risk Management Judge and Debate Facilitator, your goal is to evaluate the debate between three risk analysts-Risky, Neutral, and Safe/Conservative-and determine the best course of action for the trader.
Your decision must result in a clear recommendation: Buy, Sell, or Hold. Choose Hold only if strongly justified by specific arguments, not as a fallback when all sides seem valid. Strive for clarity and decisiveness.

CRITICAL: Your decision must be PORTFOLIO-AWARE and result in a clear, actionable recommendation.

TARGET HOLDING PERIOD (user-selected):
- HOLDING_PERIOD: {holding_text}
- EQUIVALENT_TRADING_DAYS: {trading_days_text}

CURRENT MARKET SESSION CONTEXT:
{sections["current_response"]}
{portfolio_block}
{snapshot_block}
{macro_regime_block}
{pullback_vuln_block}
{positioning_gate_block}

Guidelines for Decision-Making:
1. **Summarize Key Arguments**: Extract the strongest points from each analyst, focusing on relevance to the context.
2. **Provide Rationale**: Support your recommendation with direct quotes and counterarguments from the debate.
3. **Refine the Trader's Plan**: Start with the trader's original plan, **{sections["trader_plan"]}**, and adjust it based on the analysts' insights.
4. **Learn from Past Mistakes**: Use lessons from **{sections["memories"]}** to address prior misjudgments and improve the decision you are making now to make sure you don't make a wrong BUY/SELL/HOLD call that loses money.

Reference evidence graph projection. Cite weakest assumptions and evidence IDs in the narrative; do not add them to the strict JSON:
{sections["reports"]}

Catalyst/Event-Risk context:
{sections["catalyst"]}

If catalyst risk is HIGH or CRITICAL, treat it as a conservative override signal: prefer HOLD, reduced sizing, wait-for-trigger, or execution freeze unless the risk is directly resolved in the evidence. If recommended_action is freeze_new_buys, reduce_position, exit_review, or risk_judge_review, explicitly address whether you follow or override it.

Deliverables:
- A clear and actionable recommendation: Buy, Sell, or Hold.
- Detailed reasoning anchored in the debate and past reflections.
- Preserve Trader-selected mode unless a hard risk/executability constraint requires override.
- Explicitly state whether the final decision materially changes the Trader proposal.
- List accepted risk patches and rejected risk patches. If there is no material change, explain why all patches failed evidence, materiality, or portfolio validation.

---

**Analysts Debate History:**
{sections["history_tail"]}

---
{sections["risk_patch_validation"]}

---

Focus on actionable insights and continuous improvement.

OUTPUT CONTRACT (STRICT):
- Provide your normal reasoning narrative first.
- Include a compact DECISION_DIFF section before the JSON block:
  - FROM_TRADER_PLAN: summarize changed executable fields.
  - TO_FINAL_DECISION: summarize final executable fields.
  - ACCEPTED_PATCHES: patch IDs or [].
  - REJECTED_PATCHES: patch IDs with reasons or [].
  - NO_MATERIAL_CHANGE_REASON: null or a concrete reason.
- Then END your response with exactly one canonical JSON block between tags:
  BEGIN_DECISION_JSON
  {{ ... }}
  END_DECISION_JSON
- The executor uses ONLY this JSON block for trading execution.
- The canonical JSON block must include final trace fields:
  - `rationale_evidence_ids`: evidence IDs supporting the final executable fields.
  - `accepted_patches`: accepted patch IDs, or [].
  - `rejected_patches`: rejected patch IDs or objects with reasons, or [].
  - `no_material_change_reason`: null when there is a material change; otherwise a concrete reason.
- Trader-selected execution intent for this ticker: `{trader_intent}`.

JSON RULES:
- Valid JSON only (no markdown inside JSON, no comments, no trailing commas).
- Use `null` for missing values (never "N/A", "NA", "-", or "NONE").
- Numeric fields must be numbers, not formatted strings (no `%`, commas, or currency symbols).
- `quantity` must be an integer or null.
- `decision_version` must be "v1" or "v2".

MODE SELECTION (decision_version <-> execution_intent) — READ CAREFULLY, THIS IS THE #1 SOURCE OF REJECTED DECISIONS:
- These two fields MUST be consistent. There are EXACTLY two legal pairings — any other combination is rejected by the executor:
  - `decision_version: "v1"` PAIRED WITH `execution_intent: "act_now"` — an immediate BUY/SELL/HOLD acted on right now.
  - `decision_version: "v2"` PAIRED WITH `execution_intent: "wait_for_trigger"` — a conditional plan that REQUIRES a non-empty `execution_plan` of trigger branches.
- NEVER emit `v1` with `wait_for_trigger`. NEVER emit `v2` with `act_now`. NEVER emit `v2` with an empty `execution_plan`.
- If you want to be CAUTIOUS but you do NOT have a concrete conditional trigger (a specific price/volume/schedule/event to wait for), DO NOT use v2. Use `v1` + `act_now` + `action: "HOLD"` — a deliberate decision to take no position now. This is the correct, simplest way to express "wait and see" / conservative caution, and it is preferred over a half-specified v2 plan.
- Only use `v2` when you can name at least ONE concrete `execution_plan` branch with real `conditions` (price/volume/schedule/event) AND a complete `action_template`.
- Treat Trader intent as primary mode selector; only override when hard constraints require it.
- If you override Trader mode, include `override_reason` in canonical JSON and explain override in narrative.
- Anchor all prices to market_snapshot.reference_price. `limit_price` (when used) must be within the current bid/ask range — it is the actual execution price of the order being placed right now, not a hypothetical future trigger. `stop_loss` and `take_profit` must be realistic risk levels relative to reference_price.

Use `decision_version: "v1"` for immediate single-action decisions:
{{
  "action": "BUY | SELL | HOLD",
  "ticker": "{company_name}",
  "quantity": 37,
  "order_type": "MARKET | LIMIT | STOP | STOP_LIMIT | TRAILING_STOP",
  "time_in_force": "DAY | GTC",
  "extended_hours": false,
  "limit_price": 179.0,
  "stop_price": null,
  "trail_percent": null,
  "trail_price": null,
  "stop_loss": 165.0,
  "take_profit": 200.0,
  "position_size_pct": 0.08,
  "time_horizon": "{holding_text}",
  "confidence": "HIGH | MEDIUM | LOW",
  "rationale": "2-3 sentence summary",
  "decision_version": "v1",
  "execution_intent": "act_now",
  "override_reason": null,
  "rationale_evidence_ids": ["E-MKT-001"],
  "accepted_patches": ["P-SAFE-001"],
  "rejected_patches": [],
  "no_material_change_reason": null
}}

Use `decision_version: "v2"` for conditional scenario playbooks:
{{
  "decision_version": "v2",
  "ticker": "{company_name}",
  "plan_mode": "immediate | conditional",
  "execution_plan": [
    {{
      "branch_id": "post_earnings_breakout",
      "priority": 1,
      "conditions": {{
        "price": {{"close_above": 80.0}},
        "volume": {{"volume_ratio_min": 1.5}},
        "schedule": {{"valid_from": "2026-02-26", "valid_to": "2026-03-05", "session_constraint": "MARKET_HOURS"}}
      }},
      "event_conditions": [
        {{"event_key": "neutron_commentary_clean", "requires_confirmation": true, "expected_value": "true"}}
      ],
      "action_template": {{
        "action": "BUY",
        "quantity": null,
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "extended_hours": false,
        "limit_price": 81.0,
        "stop_price": null,
        "trail_percent": null,
        "trail_price": null,
        "stop_loss": 72.0,
        "take_profit": 95.0,
        "position_size_pct": 0.06,
        "time_horizon": "{holding_text}",
        "confidence": "MEDIUM",
        "rationale": "Conditional breakout entry after binary event."
      }}
    }}
  ],
  "default_action": "post_earnings_breakout",
  "time_horizon": "{holding_text}",
  "confidence": "MEDIUM",
  "rationale": "Scenario-based plan for post-event execution.",
  "action": "HOLD",
  "execution_intent": "wait_for_trigger",
  "override_reason": null,
  "rationale_evidence_ids": ["E-MKT-001"],
  "accepted_patches": [],
  "rejected_patches": [{{"patch_id": "P-RISKY-001", "reason": "Insufficient admissible evidence."}}],
  "no_material_change_reason": "All proposed patches failed evidence, materiality, or portfolio validation."
}}

Validation-critical constraints:
- For EVERY action (BUY/SELL/HOLD), provide numeric `stop_loss` and `take_profit`.
- BUY requires at least one of: `quantity` or `position_size_pct`.
- SELL requires explicit `quantity`.
- LIMIT requires `limit_price`.
- STOP requires `stop_price`.
- STOP_LIMIT requires BOTH `stop_price` and `limit_price`.
- TRAILING_STOP requires exactly one of `trail_percent` or `trail_price`.
- HOLD-specific rules (when action is HOLD):
  - Set `order_type` to "MARKET" — the executor does not submit any order for HOLD; MARKET is the correct sentinel.
  - Set `limit_price` to null.
  - Set `quantity` to null and `position_size_pct` to null.
  - Set `stop_loss` and `take_profit` to realistic numeric risk levels relative to reference_price.
- For BUY or SELL when market is closed, do not use MARKET; use LIMIT with a concrete `limit_price` near the current bid/ask, and DAY time_in_force. For HOLD, always use `order_type: "MARKET"` and `limit_price: null` regardless of market session.

- For v2 scenario trees, put conditional logic in execution_plan and event_conditions.
- Include a short "price anchor rationale" in your narrative before the JSON block, describing % distance from the snapshot reference.
  """

        def _invoke(repair_prompt: str):
            return invoke_with_backoff(
                llm,
                repair_prompt,
                key="risk_manager",
                min_interval_s=float(config.get("risk_manager_min_delay_s", 0.0) or 0.0),
                max_retries=int(config.get("risk_manager_max_retries", 6) or 6),
                base_backoff_s=float(config.get("risk_manager_backoff_base_s", 1.0) or 1.0),
                max_backoff_s=float(config.get("risk_manager_backoff_max_s", 30.0) or 30.0),
            )

        def _check(content: str):
            # Mirrors _attach_canonical_decision so the repair criterion matches the
            # final hard gate exactly: extract -> structured validation -> contract.
            raw, raw_err = extract_decision_json_block(content)
            if raw_err:
                return raw_err
            structured, err = validate_structured_decision(
                raw or {}, expected_ticker=company_name
            )
            if err:
                return err
            violations = validate_final_decision_contract(
                structured if isinstance(structured, dict) else {}
            )
            if violations:
                return violations
            return None

        response = invoke_with_contract_repair(
            prompt,
            stage="risk_manager",
            invoke=_invoke,
            check=_check,
            max_repair_attempts=int(
                config.get("debate_contract_repair_attempts", 2) or 0
            ),
        )
        new_risk_debate_state = {
            "judge_decision": response.content,
            "history": risk_debate_state["history"],
            "risky_history": risk_debate_state["risky_history"],
            "safe_history": risk_debate_state["safe_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_risky_response": risk_debate_state["current_risky_response"],
            "current_safe_response": risk_debate_state["current_safe_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": response.content,
            "trader_plan_v1": enforced_trader_plan_v1,
            "risk_patches": risk_patches,
            "risk_patch_validation": risk_patch_validation,
            "decision_trace": build_decision_trace(
                {**state, "final_trade_decision": response.content},
                response.content,
            ),
            "market_snapshot": market_snapshot,
            "positioning_warning": positioning_gate,
        }

    return risk_manager_node


def _extract_trader_execution_intent(trader_plan_text: str) -> str:
    text = str(trader_plan_text or "")
    m = re.search(
        r"EXECUTION[_\s-]*INTENT\s*:\s*(ACT_NOW|WAIT_FOR_TRIGGER|ACT NOW|WAIT FOR TRIGGER)",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return "UNSPECIFIED"
    return m.group(1).upper().replace(" ", "_")

