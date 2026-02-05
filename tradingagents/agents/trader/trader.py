import functools
import time
import json


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
- Size your recommendation relative to available cash and buying power.
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

        messages = [
            {
                "role": "system",
                "content": f"""You are a professional trading agent analyzing market data to make investment decisions. You must produce a PORTFOLIO-AWARE recommendation.

  Based on your analysis, provide a specific, actionable recommendation. Do not forget to utilize lessons from past decisions. Here are reflections from similar situations: {past_memory_str}

  {market_session_block}

  YOUR OUTPUT MUST END WITH A STRUCTURED TRADING DECISION in exactly this format:

  ---
  FINAL TRANSACTION PROPOSAL:
  - ACTION: BUY / SELL / HOLD
  - TICKER: {company_name}
  - QUANTITY: [INTEGER number of shares, or "N/A" for HOLD]
  - ORDER_TYPE: MARKET / LIMIT / STOP / STOP_LIMIT / TRAILING_STOP
  - TIME_IN_FORCE: DAY / GTC
  - LIMIT_PRICE: [required for LIMIT and STOP_LIMIT; otherwise "N/A"]
  - STOP_PRICE: [required for STOP and STOP_LIMIT; otherwise "N/A"]
  - TRAIL_PERCENT: [for TRAILING_STOP, percent like 3 for 3%; otherwise "N/A"]
  - TRAIL_PRICE: [for TRAILING_STOP, dollars like 1.25; otherwise "N/A"]
  - STOP_LOSS: [price, or "N/A"]
  - TAKE_PROFIT: [price target, or "N/A"]
  - TIME_HORIZON: [e.g., "1-3 days", "1-2 weeks", "swing trade"]
  - CONFIDENCE: HIGH / MEDIUM / LOW
  - RATIONALE: [one-sentence summary]
  ---

  IMPORTANT RULES:
  - MARKET means execute now (immediate attempt). LIMIT/STOP/STOP_LIMIT/TRAILING_STOP may execute later if triggered/filled.
  - If the regular market is CLOSED (pre-market/after-market/overnight/weekend), do NOT use MARKET; use LIMIT + TIME_IN_FORCE=DAY and provide a concrete LIMIT_PRICE.
  - For TRAILING_STOP you MUST set exactly ONE of TRAIL_PERCENT or TRAIL_PRICE (the other must be N/A).
  - For STOP you MUST set STOP_PRICE. For STOP_LIMIT you MUST set both STOP_PRICE and LIMIT_PRICE. For LIMIT you MUST set LIMIT_PRICE.
  - If you are unsure about conditional order parameters, use MARKET or LIMIT with a clear LIMIT_PRICE.
  - If you have ZERO position and the analysis is bearish, recommend HOLD (pass), NOT SELL.
  - SELL is only valid if you currently hold shares.
  - Size your QUANTITY based on available cash/buying power (suggest 5-15% of portfolio for medium confidence, up to 20% for high confidence).
  - QUANTITY must be a single integer on that line (e.g., "37"). Do NOT include percentages, ranges, approximations, or any other numbers on the QUANTITY line.
  - If you want to fully exit a position, set QUANTITY equal to the exact number of shares currently held (from the portfolio state above).""",
            },
            context,
        ]

        result = llm.invoke(messages)

        return {
            "messages": [result],
            "trader_investment_plan": result.content,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
