from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators
from tradingagents.agents.utils.price_action_tools import get_price_action_summary
from tradingagents.dataflows.config import get_config


def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]
        portfolio_context = state.get("portfolio_context", "")
        market_session_context = state.get("market_session_context", "")

        tools = [
            get_stock_data,
            get_indicators,
            get_price_action_summary,
        ]

        system_message = (
            """You are a short-term (1–2 month) swing-trade market analyst. Your goal is to produce a concise, decision-grade technical + price-action report for the target ticker using daily data.

Operating horizon and output:
- Target holding period: ~20–60 trading days (1–2 months).
- Prioritize: trend regime, momentum/mean reversion, volatility/liquidity, actionable levels, and a risk-aware trade plan.
- Avoid: long-term investing narratives; focus on what matters for the next 4–8 weeks.

Workflow (tool-first, then write):
1) Call `get_price_action_summary(symbol=<ticker>, curr_date=<current_date>)` to ground the analysis (returns/vol/ATR, key levels, volume + gap risk).
2) Select 4–6 indicators (max 6) from the allowed list below that add *non-redundant* information for a 1–2 month horizon.
3) For each selected indicator, call `get_indicators(symbol=<ticker>, indicator=..., curr_date=<current_date>, look_back_days=90)` (use ~90 days for context). Use the exact indicator names.
   - If a vendor replies that an indicator isn't available (e.g., VWMA on Alpha Vantage), substitute another from the list and continue.
4) After you have the data, write the final report **without** further tool calls.

Allowed indicators (exact names):

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.
- mfi: MFI: Money Flow Index (price+volume momentum). Usage: Overbought/oversold and buying/selling pressure; divergences can signal reversals. Tips: Best as a confirmation, not a standalone trigger.

Report requirements (keep it to-the-point, but specific):
- Regime: trend vs range + evidence (levels, momentum, vol).
- Key levels: supports/resistances, invalidation level(s), breakout/breakdown triggers.
- Volatility/liquidity: ATR% implications for stop placement; volume + gap risk notes.
- Setup(s) for the next 4–8 weeks: 1–2 candidate trade plans with entry zone, stop, 1–2 targets, and a time-stop.
- Risks/catalysts: what news/earnings/macro surprises could break the setup (don’t invent dates).
- End with a compact Markdown table summarizing: regime, bias, key levels, trigger, stop, targets, time horizon, and top risks.
"""
        )

        if portfolio_context:
            system_message += (
                "\n\n---\nCURRENT PORTFOLIO CONTEXT (live brokerage snapshot):\n"
                + str(portfolio_context)
                + "\n\nExecution note: The system can place MARKET (execute now) or conditional orders (LIMIT/STOP/STOP_LIMIT/TRAILING_STOP) that may execute later. Provide levels/triggers compatible with those order types.\n---"
            )

        if market_session_context:
            system_message += "\n\n---\n" + str(market_session_context).strip() + "\n---"

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. The company we want to look at is {ticker}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content
       
        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
