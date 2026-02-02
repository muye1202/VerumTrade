import time
import json

from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm_rate_limit import invoke_with_backoff

def create_risk_manager(llm, memory):
    def risk_manager_node(state) -> dict:
        config = get_config()

        company_name = state["company_of_interest"]

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["news_report"]
        sentiment_report = state["sentiment_report"]
        trader_plan = state["investment_plan"]

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # NOTE: add in current state of user's portfolio
        portfolio_context = state.get("portfolio_context", "")
        portfolio_block = ""
        if portfolio_context:
            portfolio_block = f"""

---
**CURRENT PORTFOLIO STATE (from live brokerage):**
{portfolio_context}

CRITICAL PORTFOLIO RULES:
1. If the portfolio shows ZERO shares, SELL is NOT valid. Choose BUY or HOLD only.
2. If there is an existing position, factor in unrealized P&L when deciding.
3. Size the recommendation relative to available cash and concentration limits.
4. Do not recommend adding to a position exceeding 20% of portfolio value.
---
"""

        prompt = f"""As the Risk Management Judge and Debate Facilitator, your goal is to evaluate the debate between three risk analysts—Risky, Neutral, and Safe/Conservative—and determine the best course of action for the trader. 
Your decision must result in a clear recommendation: Buy, Sell, or Hold. Choose Hold only if strongly justified by specific arguments, not as a fallback when all sides seem valid. Strive for clarity and decisiveness.

CRITICAL: Your decision must be PORTFOLIO-AWARE and result in a clear, actionable recommendation.
{portfolio_block}

Guidelines for Decision-Making:
1. **Summarize Key Arguments**: Extract the strongest points from each analyst, focusing on relevance to the context.
2. **Provide Rationale**: Support your recommendation with direct quotes and counterarguments from the debate.
3. **Refine the Trader's Plan**: Start with the trader's original plan, **{trader_plan}**, and adjust it based on the analysts' insights.
4. **Learn from Past Mistakes**: Use lessons from **{past_memory_str}** to address prior misjudgments and improve the decision you are making now to make sure you don't make a wrong BUY/SELL/HOLD call that loses money.

Deliverables:
- A clear and actionable recommendation: Buy, Sell, or Hold.
- Detailed reasoning anchored in the debate and past reflections.

---

**Analysts Debate History:**  
{history}

---

Focus on actionable insights and continuous improvement.

YOUR OUTPUT MUST END WITH a structured decision:

---
  FINAL TRADING DECISION:
  - ACTION: BUY / SELL / HOLD
  - TICKER: [symbol]
  - QUANTITY: [INTEGER number of shares, or "N/A" for HOLD]
  - ORDER_TYPE: MARKET / LIMIT
  - LIMIT_PRICE: [target price if LIMIT, otherwise "N/A"]
  - STOP_LOSS: [stop-loss price or "N/A"]
  - TAKE_PROFIT: [profit target or "N/A"]
  - POSITION_SIZE_PCT: [% of portfolio]
  - TIME_HORIZON: [e.g., "1-3 days", "1-2 weeks"]
  - CONFIDENCE: HIGH / MEDIUM / LOW
  - RATIONALE: [2-3 sentence summary]
  ---

  QUANTITY RULES:
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
