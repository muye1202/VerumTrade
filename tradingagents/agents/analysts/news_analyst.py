from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from tradingagents.agents.utils.agent_runtime.agent_utils import get_news, get_company_news_window, get_global_news
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        portfolio_context = state.get("portfolio_context", "")
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}–{spec.weeks_range[1]} weeks"

        tools = [
            get_news,
            get_company_news_window,
            get_global_news,
        ]

        system_message = (
            f"You are a news + macro analyst supporting a {holding_text} swing trade decision. Your job is to identify *trade-relevant* catalysts and risks for {window_text}, not to produce a long general news recap."
            "\n\nWorkflow (tool-first, then write):"
            f"\n1) Pull company-specific news/sentiment for the last ~{spec.company_news_lookback_days} days using `get_company_news_window(ticker=<ticker>, curr_date=<current_date>, look_back_days={spec.company_news_lookback_days})` (fallback: `get_news`)."
            f"\n2) Pull macro/regime headlines using `get_global_news(curr_date=<current_date>, look_back_days={spec.global_news_lookback_days}, limit=10)`."
            "\n3) After you have data, write the final report **without** further tool calls."
            "\n\nReport requirements (to-the-point, trading oriented):"
            "\n- Company catalysts: summarize key storylines; map each to likely price impact direction and time window."
            "\n- Macro/regime: risk-on/off tone, rates/inflation themes, and how they could affect the ticker/sector."
            "\n- Sentiment/positioning signals from the vendor output (e.g., Alpha Vantage news sentiment scores) if present."
            f"\n- Event-driven risk: list 3–5 plausible upcoming catalysts/risks over {window_text} (don’t invent dates; describe them generically if unknown)."
            "\n- Bottom line: short-term news-driven bias (bullish/bearish/neutral) + what headline would invalidate it."
            "\n\nEnd with a compact Markdown table: theme, bullish/bearish impulse, confidence, time horizon, and key watch item."
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
                    "For your reference, the current date is {current_date}. We are looking at the company {ticker}",
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
            "news_report": report,
        }

    return news_analyst_node
