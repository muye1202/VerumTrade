import functools
import time
import json

from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.dataflows.config import get_config
from tradingagents.execution.decision_guard import build_market_snapshot


def create_trader(llm, memory):
    def trader_node(state, name):
        company_name = state["company_of_interest"]
        investment_plan = state["investment_plan"]
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        portfolio_context = state.get("portfolio_context", "")
        market_session_context = state.get("market_session_context", "")
        market_snapshot = state.get("market_snapshot", {}) or build_market_snapshot(
            symbol=company_name,
            market_report=market_research_report,
            quote=None,
            structured_decision=None,
            snapshot_source=get_config().get("decision_snapshot_source", "executor_quote_first"),
        )
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        trading_days_text = (
            f"~{spec.trading_days_range[0]}–{spec.trading_days_range[1]} trading days"
        )

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        if past_memories:
            for i, rec in enumerate(past_memories, 1):
                past_memory_str += rec["recommendation"] + "\n\n"
        else:
            past_memory_str = "No past memories found."

        # Build portfolio awareness block
        portfolio_block = ""
        if portfolio_context:
            portfolio_block = f"""

CRITICAL — CURRENT PORTFOLIO STATE:
{portfolio_context}

You MUST factor in the portfolio state above when making your decision:
- If you hold ZERO shares, do NOT recommend SELL (there is nothing to sell).
- If you already hold a large position, consider whether adding more increases concentration risk.
- Size your recommendation relative to available capital (effective buying power) and concentration risk.
"""

        context = {
            "role": "user",
            "content": f"""Based on a comprehensive analysis by a team of analysts, here is an investment plan tailored for {company_name}. This plan incorporates insights from current technical market trends, macroeconomic indicators, and social media sentiment.

Proposed Investment Plan: {investment_plan}
{portfolio_block}
Leverage these insights to make an informed and strategic decision.""",
        }

        market_session_block = ""
        if market_session_context:
            market_session_block = f"\n\n{market_session_context}\n"
        market_snapshot_block = ""
        if market_snapshot:
            market_snapshot_block = (
                "\n\nCANONICAL MARKET SNAPSHOT (anchor all numeric levels to this):\n"
                f"{market_snapshot}\n"
            )

        messages = [
            {
                "role": "system",
                "content": f"""You are a professional trading agent analyzing market data to make investment decisions. You must produce a PORTFOLIO-AWARE recommendation.

  Target holding period for this run: {holding_text} ({trading_days_text}). Calibrate entries/stops/targets/time-stops to this horizon.

  Based on your analysis, provide a specific, actionable recommendation. Do not forget to utilize lessons from past decisions. Here are reflections from similar situations: {past_memory_str}

  {market_session_block}
  {market_snapshot_block}

  YOUR OUTPUT MUST END WITH A STRUCTURED TRADING DECISION in exactly this format:

  ---
  FINAL TRANSACTION PROPOSAL:
  - EXECUTION_INTENT: ACT_NOW / WAIT_FOR_TRIGGER
  - ACTION: BUY / SELL / HOLD
  - TICKER: {company_name}
  - QUANTITY: [INTEGER number of shares, or "N/A" for HOLD]
  - ORDER_TYPE: MARKET / LIMIT / STOP / STOP_LIMIT / TRAILING_STOP
  - TIME_IN_FORCE: DAY / GTC
  - LIMIT_PRICE: [required for LIMIT and STOP_LIMIT; otherwise "N/A"]
  - STOP_PRICE: [required for STOP and STOP_LIMIT; otherwise "N/A"]
  - TRAIL_PERCENT: [for TRAILING_STOP, percent like 3 for 3%; otherwise "N/A"]
  - TRAIL_PRICE: [for TRAILING_STOP, dollars like 1.25; otherwise "N/A"]
  - STOP_LOSS: [REQUIRED numeric price for BUY/SELL/HOLD]
  - TAKE_PROFIT: [REQUIRED numeric price for BUY/SELL/HOLD]
  - POSITION_SIZE_PCT: [for BUY only: percent of available capital/effective buying power (e.g., 10 for 10%); otherwise "N/A"]
  - TIME_HORIZON: [e.g., "1-3 days", "1-2 weeks", "swing trade"]
  - CONFIDENCE: HIGH / MEDIUM / LOW
  - RATIONALE: [one-sentence summary]
  ---

  IMPORTANT RULES:
  - **CRITICAL**: You must choose exactly one `EXECUTION_INTENT`:
    - `ACT_NOW` when this setup should be executed immediately.
    - `WAIT_FOR_TRIGGER` when this setup should be monitored and activated later by explicit conditions.
  - **CRITICAL**: If `EXECUTION_INTENT` is `WAIT_FOR_TRIGGER`, prefer HOLD action now and include clear trigger levels/conditions in your narrative.
  - **CRITICAL**: Anchor LIMIT/STOP/STOP_LOSS/TAKE_PROFIT to `market_snapshot.reference_price` when available.
  - **CRITICAL**: Any numeric level must be within +/-30% of `market_snapshot.reference_price` when that reference exists.
  - **CRITICAL**: Briefly justify numeric levels as % distance from reference in your narrative.
  - **CRITICAL**: For EVERY action (BUY/SELL/HOLD), you MUST provide concrete numeric STOP_LOSS and TAKE_PROFIT prices. Do NOT output N/A for either field.
  - **CRITICAL**: For HOLD with an existing position, STOP_LOSS and TAKE_PROFIT are hold-management boundaries.
  - **CRITICAL**: For HOLD with ZERO shares, STOP_LOSS and TAKE_PROFIT are watch levels (invalidation/trigger levels for potential future activation), and must still be numeric.
  - **CRITICAL**: If you cannot justify concrete numeric STOP_LOSS/TAKE_PROFIT levels from the analysis, output HOLD with conservative numeric watch levels rather than omitting prices.
  - MARKET means execute now (immediate attempt). LIMIT/STOP/STOP_LIMIT/TRAILING_STOP may execute later if triggered/filled.
  - If the regular market is CLOSED (pre-market/after-market/overnight/weekend), do NOT use MARKET; use LIMIT + TIME_IN_FORCE=DAY and provide a concrete LIMIT_PRICE.
  - For TRAILING_STOP you MUST set exactly ONE of TRAIL_PERCENT or TRAIL_PRICE (the other must be N/A).
  - For STOP you MUST set STOP_PRICE. For STOP_LIMIT you MUST set both STOP_PRICE and LIMIT_PRICE. For LIMIT you MUST set LIMIT_PRICE.
  - If you are unsure about conditional order parameters, use MARKET or LIMIT with a clear LIMIT_PRICE.
  - If you have ZERO position and the analysis is bearish, recommend HOLD (pass), NOT SELL.
  - SELL is only valid if you currently hold shares.
  - SIZING: You may either (A) provide QUANTITY as an integer number of shares, or (B) set QUANTITY to "N/A" and provide POSITION_SIZE_PCT for BUY sizing. POSITION_SIZE_PCT is interpreted as % of available capital/effective buying power (e.g., 10 means 10%). The executor will compute an integer share quantity based on the latest quote and apply concentration/max-size caps.
  - If ACTION is SELL, you MUST provide QUANTITY explicitly (otherwise execution may default to selling the full position).
  - If you provide QUANTITY, size it based on available capital/effective buying power (suggest 5-15% for medium confidence, up to 20% for high confidence).
  - QUANTITY must be a single integer on that line (e.g., "37"). Do NOT include percentages, ranges, approximations, or any other numbers on the QUANTITY line.
  - If you want to fully exit a position, set QUANTITY equal to the exact number of shares currently held (from the portfolio state above).""",
            },
            context,
        ]

        result = llm.invoke(messages)

        return {
            "messages": [result],
            "trader_investment_plan": result.content,
            "market_snapshot": market_snapshot,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
