from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from tradingagents.agents.utils.agent_runtime.agent_utils import get_news, get_company_news_window, get_global_news, get_news_sentiment, get_recent_sec_filings
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.agent_runtime.context_budget import build_report_evidence_summary
from tradingagents.agents.utils.market_data.bundle_tools import (
    get_news_data_bundle,
    select_bundle_first_tools,
)
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


def create_news_analyst(llm):
    def news_analyst_node(state):
        if state.get("news_report"):
            return {
                "news_report": state["news_report"],
            }

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        portfolio_context = state.get("portfolio_context", "")
        catalyst_context = state.get("catalyst_report", "")
        catalyst_structured = state.get("catalyst_event_report_structured", {})
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}–{spec.weeks_range[1]} weeks"

        enable_bundle_tools = bool(get_config().get("enable_bundle_tools", True))
        tool_round_cap = int(get_config().get("analyst_tool_round_cap", 4) or 0)
        global_tool_round_cap = int(get_config().get("max_tool_calls_total", 50) or 50)
        rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
        rounds_used = int(rounds.get("news", 0) or 0)
        total_rounds_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
        fallback_tools = [
            get_news,
            get_company_news_window,
            get_global_news,
            get_news_sentiment,
            get_recent_sec_filings,
        ]
        blocked_tooling_update = {}
        selected_question = None
        if rounds_used <= 0:
            tools = select_bundle_first_tools(
                get_news_data_bundle,
                fallback_tools,
                enable_bundle_tools=enable_bundle_tools,
                rounds_used=rounds_used,
            )
            selected_question = build_minimum_evidence_question(
                "news",
                getattr(get_news_data_bundle, "name", "get_news_data_bundle")
                if enable_bundle_tools
                else None,
            )
        else:
            tools, selected_question = select_question_gated_tools(
                state,
                "news",
                fallback_tools,
                rounds_used=rounds_used,
            )
            if not tools:
                blocked_tooling_update = count_blocked_tool_call(
                    state, "news", "no_named_open_question"
                )

        system_message = (
            f"You are a news + macro analyst supporting a {holding_text} swing trade decision. Your job is to identify *trade-relevant* catalysts and risks for {window_text}, not to produce a long general news recap."
            "\n\nWorkflow (tool-first, then write):"
            f"\n1) Pull company-specific news/sentiment for the last ~{spec.company_news_lookback_days} days using `get_company_news_window(ticker=<ticker>, curr_date=<current_date>, look_back_days={spec.company_news_lookback_days})` (fallback: `get_news`)."
            f"\n2) Pull macro/regime headlines using `get_global_news(curr_date=<current_date>, look_back_days={spec.global_news_lookback_days}, limit=10)`."
            "\n3) After you have data, write the final report **without** further tool calls."
            "\n4) Prefer one batched tool-call response. If `get_news_data_bundle` is available, use it first."
            "\n5) Allow at most one fallback tool round if data is missing/invalid."
            "\n\nReport requirements (to-the-point, trading oriented):"
            "\n- Company catalysts: summarize key storylines; map each to likely price impact direction and time window."
            "\n- Macro/regime: risk-on/off tone, rates/inflation themes, and how they could affect the ticker/sector."
            "\n- Sentiment/positioning signals from the vendor output (e.g., Alpha Vantage news sentiment scores) if present."
            f"\n- Event-driven risk: list 3–5 plausible upcoming catalysts/risks over {window_text} (don’t invent dates; describe them generically if unknown)."
            "\n- Bottom line: short-term news-driven bias (bullish/bearish/neutral) + what headline would invalidate it."
            "\n- Do not output `FINAL TRANSACTION PROPOSAL`; provide domain bias and evidence only. The trader/risk judge owns executable BUY/HOLD/SELL decisions."
            "\n\nEnd with a compact Markdown table: theme, bullish/bearish impulse, confidence, time horizon, and key watch item."
        )
        system_message += "\n\n---\nANALYST WORKBENCH DISCOVERY LANE:\n"
        system_message += build_workbench_prompt_block("news", selected_question)

        if portfolio_context:
            system_message += (
                "\n\n---\nCURRENT PORTFOLIO CONTEXT (live brokerage snapshot):\n"
                + str(portfolio_context)
                + "\n\n**CRITICAL** Execution note: The system can place MARKET (execute now) or conditional orders (LIMIT/STOP/STOP_LIMIT/TRAILING_STOP). Your report MUST provide concrete numeric levels for: (1) entry/trigger, (2) stop-loss, (3) take-profit, and (4) holding horizon or time-stop for hold management. This applies to both active BUY/SELL setups and HOLD/watch scenarios. If confidence is low, still provide bounded watch levels and explicit invalidation logic instead of omitting levels.\n---"
            )

        if catalyst_context or catalyst_structured:
            system_message += (
                "\n\n---\nCATALYST / EVENT-RISK CONTEXT:\n"
                + (str(catalyst_structured) if catalyst_structured else "")
                + "\n"
                + str(catalyst_context)
                + "\nUse recent_material_events, thesis_supporting_events, thesis_breaking_events, and evidence_table to avoid over-weighting routine headlines. Focus news analysis on narrative deltas not already explained by the catalyst report.\n---"
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
                    "For your reference, the current date is {current_date}. We are looking at the company {ticker}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        force_no_tools = (
            state.get("force_no_tools_for") == "news"
            or (tool_round_cap > 0 and rounds_used >= tool_round_cap)
            or (global_tool_round_cap > 0 and total_rounds_used >= global_tool_round_cap)
        )
        chain = prompt | (
            llm if force_no_tools or not tools else bind_tools_parallel_safe(llm, tools)
        )
        result = chain.invoke(state["messages"])
        tool_calls_count = len(getattr(result, "tool_calls", None) or [])
        tooling_state = build_tooling_state_update(state, "news", tool_calls_count)
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
                        "news",
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
            finalized = finalize_analyst_workbench_output("news", result.content)
            report = finalized["report"]
            ledger = finalized["ledger"]
            evidence = finalized["evidence"]
            workbench_metrics_update = merge_workbench_metrics(
                {**state, **blocked_tooling_update, **tool_link_update},
                "news",
                finalized["metrics"],
            )

        out = {
            "messages": [result],
            "news_report": report,
            "news_evidence": evidence or (build_report_evidence_summary("news", report) if report else ""),
            "force_no_tools_for": "",
            **tooling_state,
            **blocked_tooling_update,
            **tool_link_update,
            **workbench_metrics_update,
        }
        if ledger is not None:
            out["news_ledger"] = ledger
        return out

    return news_analyst_node
