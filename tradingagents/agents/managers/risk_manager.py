import logging

from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm.llm_rate_limit import invoke_with_backoff
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.agent_runtime.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    get_budget_settings,
    prompt_diagnostics,
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
        trader_plan = state["investment_plan"]

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for rec in past_memories:
            past_memory_str += rec["recommendation"] + "\n\n"

        portfolio_context = state.get("portfolio_context", "")

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
            "reports": "\n\n".join(
                [
                    "Market report:\n"
                    + cap_section(
                        "market_report",
                        market_research_report,
                        settings["section_max_chars_report"],
                    ),
                    "Sentiment report:\n"
                    + cap_section(
                        "sentiment_report",
                        sentiment_report,
                        settings["section_max_chars_report"],
                    ),
                    "News report:\n"
                    + cap_section(
                        "news_report", news_report, settings["section_max_chars_report"]
                    ),
                    "Fundamentals report:\n"
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
3. Size the recommendation relative to available cash and concentration limits.
4. Do not recommend adding to a position exceeding 20% of portfolio value.
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

Guidelines for Decision-Making:
1. **Summarize Key Arguments**: Extract the strongest points from each analyst, focusing on relevance to the context.
2. **Provide Rationale**: Support your recommendation with direct quotes and counterarguments from the debate.
3. **Refine the Trader's Plan**: Start with the trader's original plan, **{sections["trader_plan"]}**, and adjust it based on the analysts' insights.
4. **Learn from Past Mistakes**: Use lessons from **{sections["memories"]}** to address prior misjudgments and improve the decision you are making now to make sure you don't make a wrong BUY/SELL/HOLD call that loses money.

Reference analyst reports (compacted):
{sections["reports"]}

Deliverables:
- A clear and actionable recommendation: Buy, Sell, or Hold.
- Detailed reasoning anchored in the debate and past reflections.

---

**Analysts Debate History:**
{sections["history_tail"]}

---

Focus on actionable insights and continuous improvement.

YOUR OUTPUT MUST END WITH a structured decision:

---
  FINAL TRADING DECISION:
  - ACTION: BUY / SELL / HOLD
  - TICKER: [symbol]
  - QUANTITY: [INTEGER number of shares, or "N/A" for HOLD]
  - ORDER_TYPE: MARKET / LIMIT / STOP / STOP_LIMIT / TRAILING_STOP
  - TIME_IN_FORCE: DAY / GTC
  - LIMIT_PRICE: [required for LIMIT and STOP_LIMIT; otherwise "N/A"]
  - STOP_PRICE: [required for STOP and STOP_LIMIT; otherwise "N/A"]
  - TRAIL_PERCENT: [for TRAILING_STOP, percent like 3 for 3%; otherwise "N/A"]
  - TRAIL_PRICE: [for TRAILING_STOP, dollars like 1.25; otherwise "N/A"]
  - STOP_LOSS: [REQUIRED numeric price for BUY/SELL/HOLD]
  - TAKE_PROFIT: [REQUIRED numeric price for BUY/SELL/HOLD]
  - POSITION_SIZE_PCT: [% of portfolio]
  - TIME_HORIZON: [e.g., "1-3 days", "1-2 weeks"]
  - CONFIDENCE: HIGH / MEDIUM / LOW
  - RATIONALE: [2-3 sentence summary]
  ---

  QUANTITY RULES:
  - **CRITICAL**: For EVERY action (BUY/SELL/HOLD), you MUST provide concrete numeric STOP_LOSS and TAKE_PROFIT prices. Do NOT output N/A for either field.
  - **CRITICAL**: For HOLD with an existing position, STOP_LOSS and TAKE_PROFIT are hold-management boundaries.
  - **CRITICAL**: For HOLD with ZERO shares, STOP_LOSS and TAKE_PROFIT are watch levels (invalidation/trigger levels for potential future activation), and must still be numeric.
  - **CRITICAL**: If you cannot justify concrete numeric STOP_LOSS/TAKE_PROFIT levels from the analysis, output HOLD with conservative numeric watch levels rather than omitting prices.
  - MARKET means execute now (immediate attempt). LIMIT/STOP/STOP_LIMIT/TRAILING_STOP may execute later if triggered/filled.
  - If the regular market is CLOSED (pre-market/after-market/overnight/weekend), do NOT use MARKET; use LIMIT + TIME_IN_FORCE=DAY and provide a concrete LIMIT_PRICE.
  - For TRAILING_STOP you MUST set exactly ONE of TRAIL_PERCENT or TRAIL_PRICE (the other must be N/A).
  - For STOP you MUST set STOP_PRICE. For STOP_LIMIT you MUST set both STOP_PRICE and LIMIT_PRICE. For LIMIT you MUST set LIMIT_PRICE.
  - For BUY sizing, you may either provide QUANTITY as an integer number of shares OR set QUANTITY to "N/A" and set POSITION_SIZE_PCT (interpreted as % of available cash, e.g., 10 means 10%).
  - If ACTION is SELL, you MUST set QUANTITY explicitly if you intend anything other than a full exit.
  - QUANTITY must be a single integer on that line (e.g., "37"). Do NOT include any other numbers (no %s, no ranges, no "10% (~37 shares)").
  - SELL must not default to liquidating the whole position unless explicitly intended; if fully exiting, set QUANTITY equal to the exact number of shares currently held.
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
        }

    return risk_manager_node
