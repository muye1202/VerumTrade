from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_runtime.agent_utils import get_stock_data, get_indicators
from tradingagents.agents.utils.market_data.price_action_tools import get_price_action_summary
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.market_data.vwap_tools import (
    get_intraday_vwap_position,
    get_multi_day_vwap_context,
)
from tradingagents.agents.utils.market_data.options_flow_tools import (
    get_unusual_options_activity,
    get_options_sentiment_summary,
)
from tradingagents.agents.utils.market_data.dark_pool_tools import (
    get_dark_pool_short_volume,
    get_off_exchange_volume_context,
)
from tradingagents.agents.utils.market_data.short_interest_tools import (
    get_short_interest_data,
    get_squeeze_candidates_assessment,
)
from tradingagents.agents.utils.market_data.bundle_tools import (
    get_market_data_bundle,
    select_bundle_first_tools,
)
from tradingagents.agents.utils.agent_runtime.context_budget import build_report_evidence_summary
from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from tradingagents.agents.analysts.tooling import build_tooling_state_update
from tradingagents.agents.analysts.discovery_lane import (
    count_blocked_tool_call,
    merge_workbench_metrics,
    record_tool_call_links,
    select_question_gated_tools,
)
from tradingagents.agents.analysts.workbench import (
    build_minimum_evidence_question,
    build_workbench_prompt_block,
    finalize_analyst_workbench_output,
)


def create_market_analyst(llm):

    def market_analyst_node(state):
        if state.get("market_report"):
            return {
                "market_report": state["market_report"],
            }

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        portfolio_context = state.get("portfolio_context", "")
        market_session_context = state.get("market_session_context", "")
        catalyst_context = state.get("catalyst_report", "")
        catalyst_structured = state.get("catalyst_event_report_structured", {})
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}–{spec.weeks_range[1]} weeks"
        trading_days_text = (
            f"~{spec.trading_days_range[0]}–{spec.trading_days_range[1]} trading days"
        )

        enable_bundle_tools = bool(get_config().get("enable_bundle_tools", True))
        tool_round_cap = int(get_config().get("analyst_tool_round_cap", 4) or 0)
        global_tool_round_cap = int(get_config().get("max_tool_calls_total", 50) or 50)
        rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
        rounds_used = int(rounds.get("market", 0) or 0)
        total_rounds_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
        fallback_tools = [
            # Core tools
            get_stock_data,
            get_indicators,
            get_price_action_summary,
            # VWAP positioning (Alpaca free)
            get_intraday_vwap_position,
            get_multi_day_vwap_context,
            # Options flow (Yahoo free)
            get_unusual_options_activity,
            get_options_sentiment_summary,
            # Dark pool / off-exchange (FINRA free)
            get_dark_pool_short_volume,
            get_off_exchange_volume_context,
            # Short interest (Yahoo + FINRA free)
            get_short_interest_data,
            get_squeeze_candidates_assessment,
        ]
        blocked_tooling_update = {}
        selected_question = None
        if rounds_used <= 0:
            tools = select_bundle_first_tools(
                get_market_data_bundle,
                fallback_tools,
                enable_bundle_tools=enable_bundle_tools,
                rounds_used=rounds_used,
            )
            selected_question = build_minimum_evidence_question(
                "market",
                getattr(get_market_data_bundle, "name", "get_market_data_bundle")
                if enable_bundle_tools
                else None,
            )
        else:
            tools, selected_question = select_question_gated_tools(
                state,
                "market",
                fallback_tools,
                rounds_used=rounds_used,
            )
            if not tools:
                blocked_tooling_update = count_blocked_tool_call(
                    state, "market", "no_named_open_question"
                )

        system_message = (
            f"""You are a swing-trade market analyst supporting a {holding_text} hold. Your goal is to produce a concise, decision-grade technical + price-action report for the target ticker using daily data.

Operating horizon and output:
- Target holding period: {trading_days_text} ({holding_text}).
- Prioritize: trend regime, momentum/mean reversion, volatility/liquidity, actionable levels, and a risk-aware trade plan.
- Avoid: long-term investing narratives; focus on what matters for {window_text}.

Workflow (tool-first, then write):
1) Call `get_price_action_summary(symbol=<ticker>, curr_date=<current_date>)` to ground the analysis (returns/vol/ATR, key levels, volume + gap risk).
2) Select 4–6 indicators (max 6) from the allowed list below that add *non-redundant* information for a {holding_text} horizon.
3) For each selected indicator, call `get_indicators(symbol=<ticker>, indicator=..., curr_date=<current_date>, look_back_days=90)` (use ~90 days for context). Use the exact indicator names.
   - If a vendor replies that an indicator isn't available (e.g., VWMA on Alpha Vantage), substitute another from the list and continue.
4) Call VWAP tools for entry timing context:
   - `get_intraday_vwap_position(symbol, curr_date)` for current session positioning
   - `get_multi_day_vwap_context(symbol, curr_date)` for trend confirmation
5) Check institutional flow signals:
   - `get_unusual_options_activity(symbol, curr_date)` for smart money positioning
   - `get_dark_pool_short_volume(symbol, curr_date)` for off-exchange activity
   - `get_short_interest_data(symbol, curr_date)` for squeeze risk assessment
6) After gathering data, write the final report **without** further tool calls.

Hard constraint for this run:
- Emit one batched tool-call response whenever possible.
- If `get_market_data_bundle` is available, use it first.
- You may use one fallback tool round only if data is missing/invalid.
- After tool data is available, do not emit more tool calls.

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

Advanced Institutional Flow Tools (Free-Tier Data):

VWAP Positioning:
- `get_intraday_vwap_position(symbol, curr_date)`: Current price vs session VWAP, time spent above/below, entry timing guidance.
- `get_multi_day_vwap_context(symbol, curr_date, lookback_days=5)`: Multi-day VWAP trend for confirming swing direction.

Options Flow Analysis:
- `get_unusual_options_activity(symbol, curr_date)`: Scan for high volume/OI contracts indicating informed trading. EOD data.
- `get_options_sentiment_summary(symbol, curr_date)`: Quick put/call ratio and sentiment read.

Dark Pool / Off-Exchange:
- `get_dark_pool_short_volume(symbol, curr_date)`: Daily short volume from FINRA (T+1). Proxies institutional selling pressure.
- `get_off_exchange_volume_context(symbol, curr_date)`: Estimated dark pool volume and institutional activity signals.

Short Interest:
- `get_short_interest_data(symbol, curr_date)`: Short % of float, days to cover, squeeze potential assessment.
- `get_squeeze_candidates_assessment(symbol, curr_date)`: Detailed squeeze scoring if short interest is elevated.

Advanced Tool Usage Notes:
- VWAP tools use Alpaca data (real-time if market open)
- Options flow is EOD data from Yahoo - useful for swing, not intraday signals
- Dark pool short volume is T+1 (next-day) data from FINRA
- Short interest from Yahoo has ~1-2 week lag
- For real-time institutional flow, recommend paid services in report
- Call VWAP tools for entry timing (buy below VWAP, sell above)
- Call options tools when you need sentiment confirmation or suspect unusual activity
- Call dark pool/short tools when assessing institutional positioning or squeeze risk

Report requirements (keep it to-the-point, but specific):
- Regime: trend vs range + evidence (levels, momentum, vol).
- Key levels: supports/resistances, invalidation level(s), breakout/breakdown triggers.
- Volatility/liquidity: ATR% implications for stop placement; volume + gap risk notes.
- VWAP positioning: Where is price relative to session VWAP? Entry timing implications.
- Options sentiment: Put/call ratio, any unusual activity detected.
- Institutional flow: Short volume trends, dark pool activity signals.
- Squeeze risk: If short interest >10%, assess squeeze potential.
- Setup(s) for {window_text}: 1–2 candidate trade plans with entry zone, stop, 1–2 targets, and a time-stop.
- Risks/catalysts: what news/earnings/macro surprises could break the setup (don't invent dates).
- Do not output `FINAL TRANSACTION PROPOSAL`; provide domain bias and evidence only. The trader/risk judge owns executable BUY/HOLD/SELL decisions.
- End with a compact Markdown table summarizing: regime, bias, key levels, trigger, stop, targets, time horizon, and top risks.
"""
        )
        system_message += "\n\n---\nANALYST WORKBENCH DISCOVERY LANE:\n"
        system_message += build_workbench_prompt_block("market", selected_question)

        if portfolio_context:
            system_message += (
                "\n\n---\nCURRENT PORTFOLIO CONTEXT (live brokerage snapshot):\n"
                + str(portfolio_context)
                + "\n\n**CRITICAL** Execution note: The system can place MARKET (execute now) or conditional orders (LIMIT/STOP/STOP_LIMIT/TRAILING_STOP). Your report MUST provide concrete numeric levels for: (1) entry/trigger, (2) stop-loss, (3) take-profit, and (4) holding horizon or time-stop for hold management. This applies to both active BUY/SELL setups and HOLD/watch scenarios. If confidence is low, still provide bounded watch levels and explicit invalidation logic instead of omitting levels.\n---"
            )

        if market_session_context:
            system_message += "\n\n---\n" + str(market_session_context).strip() + "\n---"

        if catalyst_context or catalyst_structured:
            system_message += (
                "\n\n---\nCATALYST / EVENT-RISK CONTEXT:\n"
                + (str(catalyst_structured) if catalyst_structured else "")
                + "\n"
                + str(catalyst_context)
                + "\nUse this to interpret price/volume action around known events, near-term timing risk, and price-volume shocks. Do not duplicate the catalyst report; incorporate it into technical bias and risk levels.\n---"
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
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

        force_no_tools = (
            state.get("force_no_tools_for") == "market"
            or (tool_round_cap > 0 and rounds_used >= tool_round_cap)
            or (global_tool_round_cap > 0 and total_rounds_used >= global_tool_round_cap)
        )
        chain = prompt | (
            llm if force_no_tools or not tools else bind_tools_parallel_safe(llm, tools)
        )

        result = chain.invoke(state["messages"])
        tool_calls_count = len(getattr(result, "tool_calls", None) or [])
        tooling_state = build_tooling_state_update(state, "market", tool_calls_count)
        link_state = {**state, **blocked_tooling_update}
        if tool_calls_count > 0:
            for tool_call in getattr(result, "tool_calls", None) or []:
                tool_name = (
                    tool_call.get("name")
                    if isinstance(tool_call, dict)
                    else getattr(tool_call, "name", "")
                )
                link_state.update(
                    record_tool_call_links(
                        link_state,
                        "market",
                        str(tool_name or ""),
                        selected_question,
                        tool_calls_count=1,
                    )
                )
        tool_link_update = {
            "analyst_tool_call_links": link_state.get(
                "analyst_tool_call_links",
                state.get("analyst_tool_call_links", {}),
            )
        }

        report = ""
        ledger = None
        evidence = ""
        workbench_metrics_update = {}

        if tool_calls_count == 0:
            finalized = finalize_analyst_workbench_output("market", result.content)
            report = finalized["report"]
            ledger = finalized["ledger"]
            evidence = finalized["evidence"]
            workbench_metrics_update = merge_workbench_metrics(
                {**state, **blocked_tooling_update, **tool_link_update},
                "market",
                finalized["metrics"],
            )
       
        out = {
            "messages": [result],
            "market_report": report,
            "market_evidence": evidence or (build_report_evidence_summary("market", report) if report else ""),
            "force_no_tools_for": "",
            **tooling_state,
            **blocked_tooling_update,
            **tool_link_update,
            **workbench_metrics_update,
        }
        if ledger is not None:
            out["market_ledger"] = ledger
        return out

    return market_analyst_node
