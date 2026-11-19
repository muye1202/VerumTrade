from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
from verumtrade.agents.utils.agent_runtime.agent_utils import get_news, get_company_news_window, get_global_news, get_news_sentiment, get_recent_sec_filings
from verumtrade.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from verumtrade.agents.utils.market_data.bundle_tools import (
    get_news_data_bundle,
    select_bundle_first_tools,
)
from verumtrade.agents.utils.market_data.macro_regime import format_macro_regime_markdown
from verumtrade.agents.utils.market_data.peer_read_through import format_sector_read_through_markdown
from verumtrade.dataflows.config import get_config
from verumtrade.agents.analysts.research_loop import run_analyst_research_turn
from verumtrade.agents.utils.market_data.sec_research_tools import list_sec_filings, read_sec_filing
from verumtrade.agents.analysts.workbench import build_minimum_evidence_question


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
        macro_regime = state.get("macro_regime", {}) or {}
        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        window_text = f"the next {spec.weeks_range[0]}–{spec.weeks_range[1]} weeks"

        enable_bundle_tools = bool(get_config().get("enable_bundle_tools", True))
        fallback_tools = [
            get_news,
            get_company_news_window,
            get_global_news,
            get_news_sentiment,
            get_recent_sec_filings,
            list_sec_filings,
            read_sec_filing,
        ]
        first_round_tools = select_bundle_first_tools(
            get_news_data_bundle,
            fallback_tools,
            enable_bundle_tools=enable_bundle_tools,
            rounds_used=0,
        )
        first_round_question = build_minimum_evidence_question(
            "news",
            getattr(get_news_data_bundle, "name", "get_news_data_bundle")
            if enable_bundle_tools
            else None,
        )

        system_message = (
            f"You are a news + macro analyst supporting a {holding_text} swing trade decision. Your job is to identify *trade-relevant* catalysts and risks for {window_text}, not to produce a long general news recap."
            "\n\nWorkflow (tool-first, then write):"
            f"\n1) Pull company-specific news/sentiment for the last ~{spec.company_news_lookback_days} days using `get_company_news_window(ticker=<ticker>, curr_date=<current_date>, look_back_days={spec.company_news_lookback_days})` (fallback: `get_news`)."
            f"\n2) Pull macro/regime headlines using `get_global_news(curr_date=<current_date>, look_back_days={spec.global_news_lookback_days}, limit=10)`."
            "\n3) After you have data, write the final report **without** further tool calls."
            "\n4) Prefer one batched tool-call response. If `get_news_data_bundle` is available, use it first."
            "\n5) Follow-up lookups happen only through the workbench: name the open question and its cheapest_tool in your ledger."
            " To check what a company actually filed, name list_sec_filings or read_sec_filing (exact filing"
            " passages with spans) as cheapest_tool; news summaries are not a substitute for the filing."
            "\n\nReport requirements (to-the-point, trading oriented):"
            "\n- Company catalysts: summarize key storylines; map each to likely price impact direction and time window."
            "\n- Macro/regime: risk-on/off tone, rates/inflation themes, and how they could affect the ticker/sector."
            "\n- Soft / second-order / foreign-jurisdiction signals: explicitly weight narrative catalysts that move *crowded baskets* with no company-specific news — a foreign policy chief's social-media trial balloon (e.g. a windfall-tax / 'citizen dividend' proposal), a peer's soft or cut guidance tone, an export-control / antitrust narrative, or a foreign-market (KOSPI/Taiwan) + FX shock. A parabolic, momentum-crowded name can unwind on these; do not require a dated, company-specific event to flag the risk."
            "\n- Sentiment/positioning signals from the vendor output (e.g., Alpha Vantage news sentiment scores) if present."
            f"\n- Event-driven risk: list 3–5 plausible upcoming catalysts/risks over {window_text} (don’t invent dates; describe them generically if unknown)."
            "\n- Bottom line: short-term news-driven bias (bullish/bearish/neutral) + what headline would invalidate it."
            "\n- Do not output `FINAL TRANSACTION PROPOSAL`; provide domain bias and evidence only. The trader/risk judge owns executable BUY/HOLD/SELL decisions."
            "\n\nEnd with a compact Markdown table: theme, bullish/bearish impulse, confidence, time horizon, and key watch item."
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
                + "\nUse recent_material_events, thesis_supporting_events, thesis_breaking_events, and evidence_table to avoid over-weighting routine headlines. Focus news analysis on narrative deltas not already explained by the catalyst report.\n---"
            )

        macro_regime_block = format_macro_regime_markdown(macro_regime)
        if macro_regime_block:
            context_tail += (
                "\n\n---\nMARKET REGIME CONTEXT (cross-asset / positioning snapshot for this run):\n"
                + macro_regime_block
                + "\n\nUse this to ground the macro/regime section and the event-driven risk list. "
                "Explicitly flag pullback vulnerability when the ticker's sector is extended/crowded "
                "(stretched momentum factor, parabolic sector run) or when the tape is turning risk-off "
                "(rates rising, oil spiking, VIX up). A soft/second-order catalyst (a peer's guidance "
                "tone, a policy trial balloon, a foreign-market shock) can unwind a crowded sector even "
                "with no company-specific bad news — call this out as a watch item.\n---"
            )

        sector_read_through_block = format_sector_read_through_markdown(
            state.get("sector_read_through", {}) or {}
        )
        if sector_read_through_block:
            context_tail += (
                "\n\n---\n"
                + sector_read_through_block
                + "\n\nThese are the ticker's basket peers. Weigh a peer's recent guidance/news tone as "
                "a read-through to the target: soft peer guidance or a peer miss can re-rate the whole "
                "crowded basket even with no news on the target itself. Surface it as a near-term risk "
                "with the peer's report date when one is given.\n---"
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
                        "For your reference, the current date is {current_date}. We are looking at the company {ticker}",
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
            counter_key="news",
            domain="news",
            compose_prompt=compose_prompt,
            first_round_tools=first_round_tools,
            first_round_question=first_round_question,
            follow_up_tools=fallback_tools,
        )

    return news_analyst_node
