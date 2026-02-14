from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from tradingagents.agents.utils.agent_runtime.agent_utils import get_news, get_company_news_window
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.agents.utils.market_data.bundle_tools import get_sentiment_data_bundle
from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from tradingagents.agents.analysts.tooling import build_tooling_state_update


def create_social_media_analyst(llm):
    def social_media_analyst_node(state):
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
        rounds_used = int(rounds.get("social", 0) or 0)
        total_rounds_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
        tools = [
            get_news,
            get_company_news_window,
        ]
        if enable_bundle_tools:
            tools = [get_sentiment_data_bundle, *tools]

        system_message = (
            f"You are a sentiment/attention analyst supporting a {holding_text} swing trade. Use the available data sources (news + any sentiment fields returned by the vendor) as a proxy for crowd attention and narrative momentum."
            "\n\nImportant: Depending on the configured vendor, you may not have direct social-media posts. If you only have news sentiment, be explicit about that limitation and avoid claiming you analyzed social posts when you did not."
            "\n\nWorkflow (tool-first, then write):"
            f"\n1) Pull the last ~{spec.sentiment_lookback_days} days of company news/sentiment using `get_company_news_window(ticker=<ticker>, curr_date=<current_date>, look_back_days={spec.sentiment_lookback_days})` (fallback: `get_news`)."
            "\n2) Extract: dominant narrative themes, sentiment trajectory, disagreement/polarization, and any abrupt sentiment shifts."
            "\n3) Write the final report **without** further tool calls."
            "\n4) Prefer one batched tool-call response. If `get_sentiment_data_bundle` is available, use it first."
            "\n5) Allow at most one fallback tool round if data is missing/invalid."
            "\n\nReport requirements (trade-relevant and specific):"
            "\n- Narrative map: top 3 themes (what/why/so-what)."
            "\n- Sentiment: direction over time; identify any inflection points and what triggered them."
            "\n- Reflexivity risk: how sentiment could drive short-term flow (breakouts, squeezes, fades)."
            f"\n- Watch items: 3 concrete headlines/posts-types that would likely move the stock over {window_text}."
            "\n\nEnd with a compact Markdown table: theme, sentiment (bull/bear), confidence, catalyst/watch item, and likely price reaction."
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
                    "For your reference, the current date is {current_date}. The current company we want to analyze is {ticker}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        force_no_tools = (
            state.get("force_no_tools_for") == "social"
            or rounds_used >= tool_round_cap
            or total_rounds_used >= global_tool_round_cap
        )
        chain = prompt | (
            llm if force_no_tools else bind_tools_parallel_safe(llm, tools)
        )

        result = chain.invoke(state["messages"])
        tool_calls_count = len(getattr(result, "tool_calls", None) or [])
        tooling_state = build_tooling_state_update(state, "social", tool_calls_count)

        report = ""

        if tool_calls_count == 0:
            report = result.content

        return {
            "messages": [result],
            "sentiment_report": report,
            "force_no_tools_for": "",
            **tooling_state,
        }

    return social_media_analyst_node
