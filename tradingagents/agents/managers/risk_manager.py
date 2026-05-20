import logging
import re

from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm.llm_rate_limit import invoke_with_backoff
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
)
from tradingagents.agents.utils.agent_runtime.evidence_graph import (
    build_decision_trace,
    format_evidence_projection,
)
from tradingagents.execution.decision_guard import build_market_snapshot


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

        settings = get_budget_settings()
        sections_before = {
            "trader_plan": cap_section(
                "trader_plan", trader_plan, settings["section_max_chars_trader_plan"]
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

---

**Analysts Debate History:**
{sections["history_tail"]}

---

Focus on actionable insights and continuous improvement.

OUTPUT CONTRACT (STRICT):
- Provide your normal reasoning narrative first.
- Then END your response with exactly one canonical JSON block between tags:
  BEGIN_DECISION_JSON
  {{ ... }}
  END_DECISION_JSON
- The executor uses ONLY this JSON block for trading execution.
- Trader-selected execution intent for this ticker: `{trader_intent}`.

JSON RULES:
- Valid JSON only (no markdown inside JSON, no comments, no trailing commas).
- Use `null` for missing values (never "N/A", "NA", "-", or "NONE").
- Numeric fields must be numbers, not formatted strings (no `%`, commas, or currency symbols).
- `quantity` must be an integer or null.
- `decision_version` must be "v1" or "v2".
- `execution_intent` is REQUIRED and must be:
  - `act_now` for `v1`
  - `wait_for_trigger` for `v2`
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
  "override_reason": null
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
  "override_reason": null
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

        response = invoke_with_backoff(
            llm,
            prompt,
            key="risk_manager",
            min_interval_s=float(config.get("risk_manager_min_delay_s", 0.0) or 0.0),
            max_retries=int(config.get("risk_manager_max_retries", 6) or 6),
            base_backoff_s=float(config.get("risk_manager_backoff_base_s", 1.0) or 1.0),
            max_backoff_s=float(config.get("risk_manager_backoff_max_s", 30.0) or 30.0),
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
            "decision_trace": build_decision_trace(
                {**state, "final_trade_decision": response.content},
                response.content,
            ),
            "market_snapshot": market_snapshot,
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



