from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from tradingagents.agents.utils.agent_runtime.agent_utils import get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement, get_insider_sentiment, get_insider_transactions
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.agent_runtime.context_budget import cap_section, get_budget_settings
from tradingagents.agents.utils.market_data.bundle_tools import get_fundamentals_data_bundle
from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from tradingagents.agents.analysts.tooling import build_tooling_state_update


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        if state.get("fundamentals_report"):
            return {
                "fundamentals_report": state["fundamentals_report"],
            }

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]
        portfolio_context = state.get("portfolio_context", "")
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}–{spec.weeks_range[1]} weeks"

        enable_bundle_tools = bool(get_config().get("enable_bundle_tools", True))
        tool_round_cap = int(get_config().get("analyst_tool_round_cap", 2) or 2)
        global_tool_round_cap = int(get_config().get("max_tool_calls_total", 50) or 50)
        rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
        rounds_used = int(rounds.get("fundamentals", 0) or 0)
        total_rounds_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_insider_sentiment,
            get_insider_transactions,
        ]
        if enable_bundle_tools:
            tools = [get_fundamentals_data_bundle, *tools]

        system_message = (
            f"You are a fundamentals analyst supporting a {holding_text} swing trade decision. Focus on what can plausibly matter over {window_text} (quality, liquidity/financing risk, earnings sensitivity, and any near-term fundamental catalysts)."
            "\n\nWorkflow (tool-first, then write):"
            "\n1) Call `get_fundamentals(ticker=<ticker>, curr_date=<current_date>)` to pull the company overview/ratios."
            "\n2) Call `get_income_statement`, `get_balance_sheet`, and `get_cashflow` (quarterly) to identify recent acceleration/deceleration and balance-sheet constraints."
            "\n3) Call `get_insider_transactions` and `get_insider_sentiment` if available; if a vendor/tool returns missing data, note it and proceed."
            "\n4) Write the final report **without** further tool calls."
            "\n5) Prefer one batched tool-call response. If `get_fundamentals_data_bundle` is available, use it first."
            "\n6) Allow at most one fallback tool round if data is missing/invalid."
            "\n\nReport requirements (keep it to-the-point and trade-relevant):"
            "\n- Near-term fundamental narrative: what changed recently and what could change next (don’t invent dates)."
            "\n- Earnings sensitivity: which line items/segments/margins matter most; what the market is likely keying on."
            "\n- Balance-sheet/liquidity: cash, debt, liquidity runway, refinancing risk (if discernible)."
            "\n- Valuation/expectations: whether expectations look stretched vs recent fundamentals (use available ratios; avoid long debates)."
            "\n- Insider activity: summarize net buying/selling and any notable patterns."
            f"\n- Bottom line: bullish/bearish fundamental bias for a {holding_text} horizon + 2–3 concrete risks that would invalidate it."
            f"\n\nEnd with a compact Markdown table summarizing: key metric(s), directionality, why it matters over {window_text}, and the risk if wrong."
        )

        if portfolio_context:
            system_message += (
                "\n\n---\nCURRENT PORTFOLIO CONTEXT (live brokerage snapshot):\n"
                + str(portfolio_context)
                + "\n\n**CRITICAL** Execution note: The system can place MARKET (execute now) or conditional orders (LIMIT/STOP/STOP_LIMIT/TRAILING_STOP). Your report MUST provide concrete numeric levels for: (1) entry/trigger, (2) stop-loss, (3) take-profit, and (4) holding horizon or time-stop for hold management. This applies to both active BUY/SELL setups and HOLD/watch scenarios. If confidence is low, still provide bounded watch levels and explicit invalidation logic instead of omitting levels.\n---"
            )

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

        force_no_tools = (
            state.get("force_no_tools_for") == "fundamentals"
            or rounds_used >= tool_round_cap
            or total_rounds_used >= global_tool_round_cap
        )
        chain = prompt | (
            llm if force_no_tools else bind_tools_parallel_safe(llm, tools)
        )

        result = chain.invoke(state["messages"])
        tool_calls_count = len(getattr(result, "tool_calls", None) or [])
        tooling_state = build_tooling_state_update(state, "fundamentals", tool_calls_count)

        report = ""

        if tool_calls_count == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
            "force_no_tools_for": "",
            **tooling_state,
        }

    return fundamentals_analyst_node
