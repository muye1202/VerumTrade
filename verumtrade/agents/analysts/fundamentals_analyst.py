from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from verumtrade.agents.utils.agent_runtime.agent_utils import get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement, get_insider_sentiment, get_insider_transactions, get_recent_sec_filings
from verumtrade.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from verumtrade.agents.utils.market_data.bundle_tools import (
    get_fundamentals_data_bundle,
    select_bundle_first_tools,
)
from verumtrade.dataflows.config import get_config
from verumtrade.agents.analysts.research_loop import run_analyst_research_turn
from verumtrade.agents.utils.market_data.sec_research_tools import SEC_RESEARCH_TOOLS
from verumtrade.agents.analysts.workbench import build_minimum_evidence_question


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
        catalyst_context = state.get("catalyst_report", "")
        catalyst_structured = state.get("catalyst_event_report_structured", {})
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}-{spec.weeks_range[1]} weeks"

        enable_bundle_tools = bool(get_config().get("enable_bundle_tools", True))
        fallback_tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_recent_sec_filings,
            get_insider_sentiment,
            get_insider_transactions,
            *SEC_RESEARCH_TOOLS,
        ]
        first_round_tools = select_bundle_first_tools(
            get_fundamentals_data_bundle,
            fallback_tools,
            enable_bundle_tools=enable_bundle_tools,
            rounds_used=0,
        )
        first_round_question = build_minimum_evidence_question(
            "fundamentals",
            getattr(get_fundamentals_data_bundle, "name", "get_fundamentals_data_bundle")
            if enable_bundle_tools
            else None,
        )

        system_message = (
            f"You are a fundamentals analyst supporting a {holding_text} swing trade decision. Focus on what can plausibly matter over {window_text} (quality, liquidity/financing risk, earnings sensitivity, and any near-term fundamental catalysts)."
            "\n\nWorkflow (tool-first, then write):"
            "\n1) Call `get_fundamentals(ticker=<ticker>, curr_date=<current_date>)` to pull the company overview/ratios."
            "\n2) Call `get_income_statement`, `get_balance_sheet`, and `get_cashflow` (quarterly) to identify recent acceleration/deceleration and balance-sheet constraints."
            "\n3) Call `get_insider_transactions` and `get_insider_sentiment` if available; if a vendor/tool returns missing data, note it and proceed."
            "\n4) Write the final report **without** further tool calls."
            "\n5) Prefer one batched tool-call response. If `get_fundamentals_data_bundle` is available, use it first."
            "\n6) Follow-up lookups happen only through the workbench: name the open question and its cheapest_tool in your ledger."
            " For questions about what the company actually disclosed, name list_sec_filings, read_sec_filing"
            " (exact filing passages with spans) or get_sec_financial_facts (XBRL values with exact period and unit)"
            " as cheapest_tool rather than summary tools."
            "\n\nReport requirements (keep it to-the-point and trade-relevant):"
            "\n- Near-term fundamental narrative: what changed recently and what could change next (don't invent dates)."
            "\n- Earnings sensitivity: which line items/segments/margins matter most; what the market is likely keying on."
            "\n- Balance-sheet/liquidity: cash, debt, liquidity runway, refinancing risk (if discernible)."
            "\n- Valuation/expectations: whether expectations look stretched vs recent fundamentals (use available ratios; avoid long debates)."
            "\n- Insider activity: summarize net buying/selling and any notable patterns."
            f"\n- Bottom line: bullish/bearish fundamental bias for a {holding_text} horizon + 2-3 concrete risks that would invalidate it."
            "\n- Do not output `FINAL TRANSACTION PROPOSAL`; provide domain bias and evidence only. The trader/risk judge owns executable BUY/HOLD/SELL decisions."
            f"\n\nEnd with a compact Markdown table summarizing: key metric(s), directionality, why it matters over {window_text}, and the risk if wrong."
        )
        context_tail = ""

        if portfolio_context:
            context_tail += (
                "\n\n---\nCURRENT PORTFOLIO CONTEXT (live brokerage snapshot):\n"
                + str(portfolio_context)
                + "\n\n**CRITICAL** Execution note: The system can place MARKET (execute now) or conditional orders (LIMIT/STOP/STOP_LIMIT/TRAILING_STOP). Your report MUST provide concrete numeric levels for: (1) entry/trigger, (2) stop-loss, (3) take-profit, and (4) holding horizon or time-stop for hold management. This applies to both active BUY/SELL setups and HOLD/watch scenarios. If confidence is low, still provide bounded watch levels and explicit invalidation logic instead of omitting levels.\n---"
            )

        if catalyst_context or catalyst_structured:
            context_tail += (
                "\n\n---\nCATALYST / EVENT-RISK CONTEXT:\n"
                + (str(catalyst_structured) if catalyst_structured else "")
                + "\n"
                + str(catalyst_context)
                + "\nUse filing, guidance, dilution, liquidity, insider, and corporate-action items as required inputs to the fundamentals view. Do not treat missing catalyst data as proof no event risk exists.\n---"
            )

        def compose_prompt(workbench_block, tools):
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
            return prompt.partial(
                system_message=system_message
                + "\n\n---\nANALYST WORKBENCH DISCOVERY LANE:\n"
                + workbench_block
                + context_tail,
                tool_names=", ".join(tool.name for tool in tools),
                current_date=current_date,
                ticker=ticker,
            )

        return run_analyst_research_turn(
            state,
            llm,
            counter_key="fundamentals",
            domain="fundamentals",
            compose_prompt=compose_prompt,
            first_round_tools=first_round_tools,
            first_round_question=first_round_question,
            follow_up_tools=fallback_tools,
        )

    return fundamentals_analyst_node
