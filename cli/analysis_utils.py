import datetime
import json
import time
import asyncio
from pathlib import Path
from functools import wraps
from typing import Optional

from rich import box
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.rule import Rule

from tradingagents.execution.portfolio_context import fetch_portfolio_context
from tradingagents.execution.execution_kwargs import executor_kwargs_from_structured
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.utils.report_sanitization import strip_thinking_blocks as _strip_thinking_blocks
from tradingagents.agents.utils.llm.llm_metrics import (
    snapshot_llm_api_calls,
    diff_llm_api_calls,
)

# Injected from `cli/main.py` via `init_analysis_context`
console = None
message_buffer = None
get_user_selections = None
setup_executor = None
create_layout = None
update_display = None
display_complete_report = None


def init_analysis_context(
    *,
    console,
    message_buffer,
    get_user_selections,
    setup_executor,
    create_layout,
    update_display,
    display_complete_report,
):
    globals()["console"] = console
    globals()["message_buffer"] = message_buffer
    globals()["get_user_selections"] = get_user_selections
    globals()["setup_executor"] = setup_executor
    globals()["create_layout"] = create_layout
    globals()["update_display"] = update_display
    globals()["display_complete_report"] = display_complete_report

    # Stash original MessageBuffer methods once so we can restore them between
    # multi-ticker runs (prevents log-wrapper stacking).
    if message_buffer is not None:
        if not hasattr(message_buffer, "_orig_add_message"):
            message_buffer._orig_add_message = message_buffer.add_message
        if not hasattr(message_buffer, "_orig_add_tool_call"):
            message_buffer._orig_add_tool_call = message_buffer.add_tool_call
        if not hasattr(message_buffer, "_orig_update_report_section"):
            message_buffer._orig_update_report_section = message_buffer.update_report_section

def update_research_team_status(status, mbuf=None):
    """Update status for all research team members and trader."""
    buf = mbuf if mbuf is not None else message_buffer
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager", "Trader"]
    for agent in research_team:
        buf.update_agent_status(agent, status)

def extract_content_string(content):
    """Extract string content from various message formats."""
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
        if isinstance(content.get("message"), dict):
            return extract_content_string(content.get("message"))
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    elif isinstance(content, list):
        # Handle Anthropic's list format
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
                elif item.get('type') == 'tool_use':
                    text_parts.append(f"[Tool: {item.get('name', 'unknown')}]")
                elif isinstance(item.get("text"), str):
                    text_parts.append(item.get("text", ""))
                elif isinstance(item.get("content"), str):
                    text_parts.append(item.get("content", ""))
            else:
                text_parts.append(str(item))
        return ' '.join(text_parts)
    else:
        return str(content)

def _reset_message_buffer() -> None:
    """Reset the shared MessageBuffer to a clean state (per ticker)."""
    if message_buffer is None:
        return

    try:
        message_buffer.messages.clear()
    except Exception:
        pass
    try:
        message_buffer.tool_calls.clear()
    except Exception:
        pass

    try:
        for agent in message_buffer.agent_status:
            message_buffer.agent_status[agent] = "pending"
        message_buffer.current_agent = None
    except Exception:
        pass

    try:
        for section in message_buffer.report_sections:
            message_buffer.report_sections[section] = None
        message_buffer.current_report = None
        message_buffer.final_report = None
    except Exception:
        pass


def _msg_type_and_content(msg):
    """Classify a LangGraph message and extract its string content."""
    if isinstance(msg, tuple) and len(msg) == 2:
        role, content = msg
        role = str(role).lower()
        if role in ("human", "user"):
            return "User", extract_content_string(content)
        if role in ("ai", "assistant"):
            return "Reasoning", extract_content_string(content)
        if role == "tool":
            return "ToolResult", extract_content_string(content)
        return "System", extract_content_string(content)

    if isinstance(msg, dict):
        role = str(msg.get("role", "")).lower()
        content = msg.get("content", msg)
        if role in ("assistant", "ai"):
            return "Reasoning", extract_content_string(content)
        if role in ("user", "human"):
            return "User", extract_content_string(content)
        if role == "tool":
            return "ToolResult", extract_content_string(content)
        return "System", extract_content_string(content)

    msg_kind = getattr(msg, "type", None)
    content = getattr(msg, "content", msg)
    if msg_kind == "ai":
        return "Reasoning", extract_content_string(content)
    if msg_kind in ("human", "user"):
        return "User", extract_content_string(content)
    if msg_kind == "tool":
        return "ToolResult", extract_content_string(content)
    if msg_kind == "system":
        return "System", extract_content_string(content)
    if hasattr(msg, "content"):
        return "Reasoning", extract_content_string(content)
    return "System", str(msg)


def _extract_tool_calls(msg):
    """Extract (name, args) pairs from a LangGraph message."""
    calls = []

    # LangChain-style tool_calls (already parsed).
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = tc.get("name") or tc.get("function", {}).get("name")
                args = tc.get("args") or tc.get("function", {}).get("arguments")
                if name:
                    calls.append((name, args))
            else:
                name = getattr(tc, "name", None)
                args = getattr(tc, "args", None)
                if name:
                    calls.append((name, args))
        return calls

    # OpenAI-compatible shape sometimes stored under additional_kwargs.
    additional = getattr(msg, "additional_kwargs", None)
    if isinstance(additional, dict) and additional.get("tool_calls"):
        for tc in additional.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments")
            if name:
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                calls.append((name, args))
        return calls

    # Dict messages with tool_calls.
    if isinstance(msg, dict) and msg.get("tool_calls"):
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            args = fn.get("arguments") or tc.get("args")
            if name:
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                calls.append((name, args))

    return calls


def process_analysis_stream(graph, init_agent_state, args, mbuf, update_display_fn, layout, selected_analysts):
    """
    Stream graph chunks and update the Live display.

    Args:
        graph: TradingAgentsGraph instance (uses graph.graph.astream)
        init_agent_state: Initial state dict for the graph
        args: Graph invocation args from propagator.get_graph_args()
        mbuf: MessageBuffer instance
        update_display_fn: Callable to refresh the Live layout
        layout: Rich Layout object
        selected_analysts: List of analyst name strings (e.g. ["market", "news", "fundamentals"])

    Returns:
        (trace, final_state) tuple
    """
    async def _run_stream():
        # Normalise analyst names to plain strings for comparison.
        analyst_names = [
            a.value if hasattr(a, "value") else str(a) for a in selected_analysts
        ]

        def _start_next_after(completed_key: str):
            order = ["catalyst", "market", "social", "news", "fundamentals"]
            labels = {
                "catalyst": "Catalyst Analyst",
                "market": "Market Analyst",
                "social": "Social Analyst",
                "news": "News Analyst",
                "fundamentals": "Fundamentals Analyst",
            }
            selected = [key for key in order if key in analyst_names]
            if completed_key not in selected:
                return
            idx = selected.index(completed_key)
            if idx + 1 < len(selected):
                mbuf.update_agent_status(labels[selected[idx + 1]], "in_progress")
            else:
                update_research_team_status("in_progress", mbuf=mbuf)

        trace = []
        final_state = None
        seen_messages = 0

        async for chunk in graph.graph.astream(init_agent_state, **args):
            messages = chunk.get("messages") or []
            new_messages = messages[seen_messages:] if seen_messages <= len(messages) else []
            seen_messages = len(messages)

            for msg in new_messages:
                msg_type, content = _msg_type_and_content(msg)
                mbuf.add_message(msg_type, content)

                for tool_name, tool_args in _extract_tool_calls(msg):
                    mbuf.add_tool_call(tool_name, tool_args)

            # Update reports and agent status based on chunk content
            # Analyst Team Reports
            if "catalyst_report" in chunk and chunk["catalyst_report"]:
                mbuf.update_report_section("catalyst_report", chunk["catalyst_report"])
                mbuf.update_agent_status("Catalyst Analyst", "completed")
                _start_next_after("catalyst")

            if "market_report" in chunk and chunk["market_report"]:
                mbuf.update_report_section("market_report", chunk["market_report"])
                mbuf.update_agent_status("Market Analyst", "completed")
                _start_next_after("market")

            if "sentiment_report" in chunk and chunk["sentiment_report"]:
                mbuf.update_report_section("sentiment_report", chunk["sentiment_report"])
                mbuf.update_agent_status("Social Analyst", "completed")
                _start_next_after("social")

            if "news_report" in chunk and chunk["news_report"]:
                mbuf.update_report_section("news_report", chunk["news_report"])
                mbuf.update_agent_status("News Analyst", "completed")
                _start_next_after("news")

            if "fundamentals_report" in chunk and chunk["fundamentals_report"]:
                mbuf.update_report_section("fundamentals_report", chunk["fundamentals_report"])
                mbuf.update_agent_status("Fundamentals Analyst", "completed")
                _start_next_after("fundamentals")

            # Research Team - Handle Investment Debate State
            if "investment_debate_state" in chunk and chunk["investment_debate_state"]:
                debate_state = chunk["investment_debate_state"]

                if "bull_history" in debate_state and debate_state["bull_history"]:
                    update_research_team_status("in_progress", mbuf=mbuf)
                    bull_responses = debate_state["bull_history"].split("\n")
                    latest_bull = bull_responses[-1] if bull_responses else ""
                    if latest_bull:
                        mbuf.add_message("Reasoning", latest_bull)
                        mbuf.update_report_section(
                            "investment_plan",
                            f"### Bull Researcher Analysis\n{latest_bull}",
                        )

                if "bear_history" in debate_state and debate_state["bear_history"]:
                    update_research_team_status("in_progress", mbuf=mbuf)
                    bear_responses = debate_state["bear_history"].split("\n")
                    latest_bear = bear_responses[-1] if bear_responses else ""
                    if latest_bear:
                        mbuf.add_message("Reasoning", latest_bear)
                        mbuf.update_report_section(
                            "investment_plan",
                            f"{mbuf.report_sections['investment_plan']}\n\n### Bear Researcher Analysis\n{latest_bear}",
                        )

                if "judge_decision" in debate_state and debate_state["judge_decision"]:
                    update_research_team_status("in_progress", mbuf=mbuf)
                    mbuf.add_message(
                        "Reasoning",
                        f"Research Manager: {debate_state['judge_decision']}",
                    )
                    mbuf.update_report_section(
                        "investment_plan",
                        f"{mbuf.report_sections['investment_plan']}\n\n### Research Manager Decision\n{debate_state['judge_decision']}",
                    )
                    update_research_team_status("completed", mbuf=mbuf)
                    mbuf.update_agent_status("Risky Analyst", "in_progress")

            # Trading Team
            if "trader_investment_plan" in chunk and chunk["trader_investment_plan"]:
                mbuf.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                mbuf.update_agent_status("Risky Analyst", "in_progress")

            # Risk Management Team - Handle Risk Debate State
            if "risk_debate_state" in chunk and chunk["risk_debate_state"]:
                risk_state = chunk["risk_debate_state"]

                if "current_risky_response" in risk_state and risk_state["current_risky_response"]:
                    mbuf.update_agent_status("Risky Analyst", "in_progress")
                    mbuf.add_message(
                        "Reasoning",
                        f"Risky Analyst: {risk_state['current_risky_response']}",
                    )
                    mbuf.update_report_section(
                        "final_trade_decision",
                        f"### Risky Analyst Analysis\n{risk_state['current_risky_response']}",
                    )

                if "current_safe_response" in risk_state and risk_state["current_safe_response"]:
                    mbuf.update_agent_status("Safe Analyst", "in_progress")
                    mbuf.add_message(
                        "Reasoning",
                        f"Safe Analyst: {risk_state['current_safe_response']}",
                    )
                    mbuf.update_report_section(
                        "final_trade_decision",
                        f"### Safe Analyst Analysis\n{risk_state['current_safe_response']}",
                    )

                if "current_neutral_response" in risk_state and risk_state["current_neutral_response"]:
                    mbuf.update_agent_status("Neutral Analyst", "in_progress")
                    mbuf.add_message(
                        "Reasoning",
                        f"Neutral Analyst: {risk_state['current_neutral_response']}",
                    )
                    mbuf.update_report_section(
                        "final_trade_decision",
                        f"### Neutral Analyst Analysis\n{risk_state['current_neutral_response']}",
                    )

                if "judge_decision" in risk_state and risk_state["judge_decision"]:
                    mbuf.update_agent_status("Portfolio Manager", "in_progress")
                    mbuf.add_message(
                        "Reasoning",
                        f"Portfolio Manager: {risk_state['judge_decision']}",
                    )
                    mbuf.update_report_section(
                        "final_trade_decision",
                        f"### Portfolio Manager Decision\n{risk_state['judge_decision']}",
                    )
                    mbuf.update_agent_status("Risky Analyst", "completed")
                    mbuf.update_agent_status("Safe Analyst", "completed")
                    mbuf.update_agent_status("Neutral Analyst", "completed")
                    mbuf.update_agent_status("Portfolio Manager", "completed")

            # Update the display
            update_display_fn(layout)

            trace.append(chunk)
            final_state = chunk

        return trace, final_state

    return asyncio.run(_run_stream())


def run_single_ticker_analysis(
    selections: dict,
    *,
    batch_idx: int | None = None,
    batch_total: int | None = None,
) -> dict:
    run_started_at = datetime.datetime.now()

    # Ensure we start clean for each ticker.
    _reset_message_buffer()

    # Restore original methods before re-wrapping (prevents wrapper stacking).
    if message_buffer is not None:
        if hasattr(message_buffer, "_orig_add_message"):
            message_buffer.add_message = message_buffer._orig_add_message
        if hasattr(message_buffer, "_orig_add_tool_call"):
            message_buffer.add_tool_call = message_buffer._orig_add_tool_call
        if hasattr(message_buffer, "_orig_update_report_section"):
            message_buffer.update_report_section = message_buffer._orig_update_report_section

    if selections.get("analysis_mode") in ("portfolio", "discovery"):
        raise ValueError("run_single_ticker_analysis only supports single-ticker mode selections.")

    if not selections.get("ticker"):
        raise ValueError("Missing required selection: ticker")

    # Optional batch header
    if console is not None and batch_total and batch_total > 1 and batch_idx is not None:
        console.print()
        console.print(Rule(f"[bold green]Batch {batch_idx + 1}/{batch_total}: {selections['ticker']}[/bold green]"))
        console.print()

    # ---- Existing single-ticker analysis flow (moved from run_analysis) ----

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    requested_depth = int(selections["research_depth"])
    debate_cap = int(config.get("max_debate_rounds_cap", requested_depth))
    risk_cap = int(config.get("max_risk_rounds_cap", requested_depth))
    config["max_debate_rounds"] = min(requested_depth, debate_cap)
    config["max_risk_discuss_rounds"] = min(requested_depth, risk_cap)
    if console is not None and (
        config["max_debate_rounds"] < requested_depth
        or config["max_risk_discuss_rounds"] < requested_depth
    ):
        console.print(
            "[yellow]Context guard: clamped research depth "
            f"(requested={requested_depth}, debate={config['max_debate_rounds']}, "
            f"risk={config['max_risk_discuss_rounds']}).[/yellow]"
        )
    # LangGraph recursion limit needs to be higher for deeper debate/retry loops.
    config["max_recur_limit"] = max(config.get("max_recur_limit", 100), selections["research_depth"] * 120)
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()


    # Persist execution preferences into config (graph/other components may read this).
    if "alpaca_execution" in config:
        exec_sel = selections.get("execution") or {}
        config["alpaca_execution"] = {
            **config.get("alpaca_execution", {}),
            "enabled": bool(exec_sel.get("enabled", False)),
            "paper_trading": bool(exec_sel.get("paper", True)),
            "position_size_pct": float(exec_sel.get("position_size_pct", config.get("alpaca_execution", {}).get("position_size_pct", 0.10) or 0.10)),
        }


    # Initialize the graph
    graph = TradingAgentsGraph(
        [analyst.value for analyst in selections["analysts"]], config=config, debug=True
    )

    # Create result directory
    results_dir = Path(config["results_dir"]) / "stocks" / selections["analysis_date"] / selections["ticker"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    # Add Alpaca executor (logs next to run results)
    execution_log_dir = results_dir / "execution_logs"
    executor = setup_executor(selections.get("execution", {}), log_dir=execution_log_dir)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            # Keep the log robust across providers (may emit unicode / non-str content).
            content = str(content).replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper
    
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            if isinstance(args, dict):
                args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            else:
                args_str = str(args)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            sanitized = _strip_thinking_blocks(content)
            func(section_name, sanitized)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                section_content = _strip_thinking_blocks(obj.report_sections[section_name])
                if section_content:
                    file_name = f"{section_name}.md"
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(section_content)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # Now start the display layout
    layout = create_layout()

    with Live(layout, refresh_per_second=4) as live:
        # Initial display
        update_display(layout)

        # Add initial messages
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        message_buffer.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        horizon_label = get_time_horizon_spec(selections.get("time_horizon")).label
        message_buffer.add_message("System", f"Target holding period: {horizon_label}")
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout)

        # Reset agent statuses
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "pending")

        # Reset report sections
        for section in message_buffer.report_sections:
            message_buffer.report_sections[section] = None
        message_buffer.current_report = None
        message_buffer.final_report = None

        # Update agent status to in_progress for the first analyst
        first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")
        update_display(layout)

        # Create spinner text
        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        update_display(layout, spinner_text)

        # Fetch live portfolio state for agent awareness (safe fallback if brokerage unavailable)
        portfolio_ctx = fetch_portfolio_context(selections["ticker"])
        if "Portfolio data unavailable" in portfolio_ctx:
            message_buffer.add_message(
                "System",
                f"Portfolio context unavailable; using fallback assumptions for {selections['ticker']}",
            )
        else:
            message_buffer.add_message(
                "System", f"Portfolio context loaded for {selections['ticker']}"
            )

        # Load existing reports if skipping is enabled
        existing_reports = {}
        if selections.get("skip_completed_analysts"):
            report_mapping = {
                "catalyst_report": "catalyst_report.md",
                "market_report": "market_report.md",
                "sentiment_report": "sentiment_report.md",
                "news_report": "news_report.md",
                "fundamentals_report": "fundamentals_report.md",
            }
            for state_key, filename in report_mapping.items():
                report_path = report_dir / filename
                if report_path.exists():
                    try:
                        with open(report_path, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                            if content:
                                existing_reports[state_key] = content
                                message_buffer.add_message(
                                    "System", f"Loaded existing {filename} to skip re-analysis"
                                )
                                # Update dashboard immediately
                                message_buffer.update_report_section(state_key, content)
                                m_name = state_key.replace("_report", " Analyst").replace("sentiment", "Social").title()
                                if m_name == "Fundamentals Analyst":
                                    m_name = "Fundamentals Analyst"
                                message_buffer.update_agent_status(m_name, "completed")
                    except Exception as e:
                        message_buffer.add_message("System", f"Failed to load existing {filename}: {e}")
            if existing_reports:
                update_display(layout)


        # Initialize state and get graph args
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"],
            selections["analysis_date"],
            portfolio_context=portfolio_ctx,
            time_horizon=selections.get("time_horizon"),
        )
        # Inject existing reports into the initial state
        for k, v in existing_reports.items():
            init_agent_state[k] = v

        args = graph.propagator.get_graph_args()

        # Stream the analysis
        execution_result = None
        execution_error: Optional[str] = None
        execution_portfolio_summary: Optional[dict] = None
        llm_snapshot_before = snapshot_llm_api_calls()
        trace, final_state = process_analysis_stream(
            graph, init_agent_state, args,
            message_buffer, update_display, layout,
            selections["analysts"],
        )
        llm_snapshot_after = snapshot_llm_api_calls()

        # Get final state and decision
        if final_state:
            llm_metrics = diff_llm_api_calls(llm_snapshot_before, llm_snapshot_after)
            rounds = final_state.get("tool_round_counts") or final_state.get("tool_call_counts") or {}
            issued = final_state.get("tool_calls_issued_by_agent") or {}
            cache_metrics = final_state.get("tool_cache_metrics") or {}
            vendor_events = final_state.get("vendor_telemetry") or []
            llm_metrics.update(
                {
                    "analyst_tool_rounds_by_agent": dict(rounds),
                    "analyst_tool_rounds_total": int(
                        final_state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0
                    ),
                    "tool_calls_issued_by_agent": dict(issued),
                    "tool_calls_issued_total": int(
                        final_state.get("tool_calls_issued_total", sum(int(v or 0) for v in issued.values())) or 0
                    ),
                    "tool_cache_metrics": dict(cache_metrics),
                    "vendor_telemetry_event_count": len(vendor_events) if isinstance(vendor_events, list) else 0,
                }
            )
            final_state["llm_metrics"] = llm_metrics
            graph._attach_canonical_decision(
                final_state, expected_ticker=selections["ticker"]
            )
            graph._enforce_decision_guard(
                final_state,
                expected_ticker=selections["ticker"],
                executor=executor,
            )
            structured = final_state.get("final_trade_decision_structured")
            decision = (structured or {}).get("action") or graph.process_signal(
                final_state["final_trade_decision"]
            )

            # Execute trade if executor is configured
            if executor:
                try:
                    console.print("\n")
                    console.print(Rule("[bold yellow]Executing Trade[/bold yellow]"))
                    console.print()

                    if not isinstance(structured, dict):
                        execution_result = {
                            "ticker": selections["ticker"],
                            "signal": decision,
                            "trade_date": selections["analysis_date"],
                            "executed": False,
                            "error": "Structured decision missing or invalid; execution aborted",
                            "decision_source": "final_trade_decision_structured",
                            "decision_version": None,
                            "decision_validation_ok": False,
                            "decision_validation_error": (
                                final_state.get("final_trade_decision_validation_error", "")
                                or "structured decision unavailable"
                            ),
                            "decision_price_guard_error": (
                                (final_state.get("decision_guard") or {}).get("price_guard_error", "")
                            ),
                            "market_snapshot_reference_price": (
                                (final_state.get("market_snapshot") or {}).get("reference_price")
                            ),
                            "market_snapshot_source": (
                                (final_state.get("market_snapshot") or {}).get("source")
                            ),
                        }
                    elif (final_state.get("decision_guard") or {}).get("price_guard_error"):
                        execution_result = {
                            "ticker": selections["ticker"],
                            "signal": decision,
                            "trade_date": selections["analysis_date"],
                            "executed": False,
                            "error": "Decision failed price guard; execution aborted",
                            "decision_source": "final_trade_decision_structured",
                            "decision_version": (
                                (final_state.get("final_trade_decision_structured") or {}).get("decision_version")
                            ),
                            "decision_validation_ok": False,
                            "decision_validation_error": (
                                final_state.get("final_trade_decision_validation_error", "")
                                or "decision failed price guard"
                            ),
                            "decision_price_guard_error": (
                                (final_state.get("decision_guard") or {}).get("price_guard_error", "")
                            ),
                            "market_snapshot_reference_price": (
                                (final_state.get("market_snapshot") or {}).get("reference_price")
                            ),
                            "market_snapshot_source": (
                                (final_state.get("market_snapshot") or {}).get("source")
                            ),
                        }
                    else:
                        # Execute the signal
                        execution_result = executor.execute_signal(
                            ticker=selections["ticker"],
                            signal=decision,
                            analysis_state=final_state,
                            trade_date=selections["analysis_date"],
                            agent_quantity=structured.get("quantity"),
                            agent_limit_price=structured.get("limit_price"),
                            **executor_kwargs_from_structured(structured),
                        )

                    # Display execution results
                    if execution_result.get("executed"):
                        # Successful execution
                        exec_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
                        exec_table.add_column("Field", style="cyan")
                        exec_table.add_column("Value", style="white")

                        exec_table.add_row("Action", f"[bold green]{execution_result['side']}[/bold green]")
                        exec_table.add_row("Quantity", f"{execution_result['qty']} shares")
                        exec_table.add_row("Price", f"${execution_result['price']:.2f}")
                        exec_table.add_row("Total Value", f"${execution_result['qty'] * execution_result['price']:,.2f}")

                        quote = execution_result.get("quote") or {}
                        bid = quote.get("bid_price")
                        ask = quote.get("ask_price")
                        if bid is not None and ask is not None:
                            exec_table.add_row("Bid / Ask", f"${bid:.2f} / ${ask:.2f}")
                            exec_table.add_row("Spread", f"${(ask - bid):.4f}")
                        elif bid is not None or ask is not None:
                            px = bid if bid is not None else ask
                            label = "Bid" if bid is not None else "Ask"
                            exec_table.add_row(label, f"${px:.2f}")
                        if quote.get("timestamp"):
                            exec_table.add_row("Quote Time", str(quote["timestamp"]))

                        if execution_result.get("requested_limit_price") is not None:
                            exec_table.add_row("Requested Limit", f"${float(execution_result['requested_limit_price']):.2f}")
                        
                        if execution_result.get('order'):
                            exec_table.add_row("Order ID", execution_result['order']['id'][:8] + "...")
                            exec_table.add_row("Status", execution_result['order']['status'])

                        console.print(Panel(
                            exec_table,
                            title="[bold green]✓ Trade Executed Successfully[/bold green]",
                            border_style="green",
                            padding=(1, 2)
                        ))

                        # Show updated portfolio
                        summary = executor.get_portfolio_summary()
                        portfolio_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
                        portfolio_table.add_column("Metric", style="cyan")
                        portfolio_table.add_column("Value", style="green")

                        portfolio_table.add_row("Portfolio Value", f"${summary['account_value']:,.2f}")
                        portfolio_table.add_row("Cash", f"${summary['cash']:,.2f}")
                        portfolio_table.add_row("Positions", str(summary['positions_count']))

                        console.print(Panel(
                            portfolio_table,
                            title="Updated Portfolio",
                            border_style="cyan",
                            padding=(1, 2)
                        ))
                        execution_portfolio_summary = summary

                        # Journal capture (non-critical)
                        try:
                            from tradingagents.agents.journal.core.store import JournalStore
                            from tradingagents.agents.journal.ingestion.hooks import capture_trade_thesis
                            
                            journal_store = JournalStore()
                            capture_trade_thesis(
                                store=journal_store,
                                final_state=final_state,
                                structured_decision=structured,
                                execution_result=execution_result,
                                trade_date=selections["analysis_date"],
                                executor=executor,
                            )
                        except Exception as e:
                            # Journal capture is non-critical — never break the main flow
                            if console:
                                console.print(f"[dim yellow]Journal capture note: {e}[/dim yellow]")

                    else:
                        # Trade not executed (HOLD or error)
                        message = execution_result.get('message', 'No action taken')
                        error = execution_result.get('error')
                        execution_error = error
                        try:
                            execution_portfolio_summary = executor.get_portfolio_summary()
                        except Exception:
                            execution_portfolio_summary = None

                        if error:
                            console.print(Panel(
                                f"[red]{error}[/red]",
                                title="[bold red]Execution Error[/bold red]",
                                border_style="red",
                                padding=(1, 2)
                            ))
                        else:
                            console.print(Panel(
                                f"[yellow]{message}[/yellow]",
                                title="[bold yellow]No Trade Executed[/bold yellow]",
                                border_style="yellow",
                                padding=(1, 2)
                            ))

                except Exception as e:
                    execution_error = str(e)
                    console.print(Panel(
                        f"[red]Execution failed: {str(e)}[/red]",
                        title="[bold red]Execution Error[/bold red]",
                        border_style="red",
                        padding=(1, 2)
                    ))

            # Always surface execution details in the main UI (Live output can hide console.print panels).
            exec_sel = selections.get("execution") or {}
            exec_enabled = bool(exec_sel.get("enabled", False))
            exec_lines = []
            exec_lines.append(f"- Enabled: `{exec_enabled}`")
            exec_lines.append(f"- Final decision: `{decision}`")

            if not exec_enabled:
                exec_lines.append("- Result: `skipped (execution disabled)`")
            elif not executor:
                exec_lines.append("- Result: `skipped (executor not configured)`")
            elif execution_result is None:
                exec_lines.append("- Result: `not attempted`")
                if execution_error:
                    exec_lines.append(f"- Error: `{execution_error}`")
            else:
                exec_lines.append(f"- Executed: `{bool(execution_result.get('executed'))}`")
                if execution_result.get("message"):
                    exec_lines.append(f"- Message: {execution_result.get('message')}")
                if execution_result.get("error"):
                    exec_lines.append(f"- Error: `{execution_result.get('error')}`")
                if execution_result.get("side"):
                    exec_lines.append(f"- Side: `{execution_result.get('side')}`")
                if execution_result.get("qty") is not None:
                    exec_lines.append(f"- Qty: `{execution_result.get('qty')}`")
                if execution_result.get("price") is not None:
                    exec_lines.append(f"- Price: `{execution_result.get('price')}`")
                if execution_result.get("requested_limit_price") is not None:
                    exec_lines.append(f"- Requested limit: `{execution_result.get('requested_limit_price')}`")
                if execution_result.get("decision_source"):
                    exec_lines.append(f"- Decision source: `{execution_result.get('decision_source')}`")
                if execution_result.get("decision_validation_ok") is not None:
                    exec_lines.append(f"- Decision validation ok: `{execution_result.get('decision_validation_ok')}`")
                if execution_result.get("decision_validation_error"):
                    exec_lines.append(f"- Decision validation error: `{execution_result.get('decision_validation_error')}`")
                if execution_result.get("decision_price_guard_error"):
                    exec_lines.append(f"- Decision price guard error: `{execution_result.get('decision_price_guard_error')}`")
                if execution_result.get("market_snapshot_reference_price") is not None:
                    exec_lines.append(
                        f"- Snapshot reference price: `{execution_result.get('market_snapshot_reference_price')}`"
                    )
                if execution_result.get("market_snapshot_source"):
                    exec_lines.append(
                        f"- Snapshot source: `{execution_result.get('market_snapshot_source')}`"
                    )

                quote = execution_result.get("quote") or {}
                if quote.get("bid_price") is not None or quote.get("ask_price") is not None:
                    exec_lines.append("")
                    exec_lines.append("**Quote**")
                    if quote.get("bid_price") is not None:
                        exec_lines.append(f"- Bid: `{quote.get('bid_price')}`")
                    if quote.get("ask_price") is not None:
                        exec_lines.append(f"- Ask: `{quote.get('ask_price')}`")
                    if quote.get("timestamp"):
                        exec_lines.append(f"- Time: `{quote.get('timestamp')}`")

                order = execution_result.get("order") or {}
                if order:
                    exec_lines.append("")
                    exec_lines.append("**Order**")
                    for k in ("id", "status", "type", "submitted_at"):
                        if order.get(k) is not None:
                            exec_lines.append(f"- {k}: `{order.get(k)}`")

            if execution_portfolio_summary:
                exec_lines.append("")
                exec_lines.append("**Portfolio**")
                exec_lines.append(f"- Account value: `${execution_portfolio_summary.get('account_value', 0):,.2f}`")
                exec_lines.append(f"- Cash: `${execution_portfolio_summary.get('cash', 0):,.2f}`")
                exec_lines.append(f"- Positions: `{execution_portfolio_summary.get('positions_count', 0)}`")

            message_buffer.update_report_section("execution_report", "\n".join(exec_lines))
            update_display(layout)

        final_state = trace[-1]
        graph._attach_canonical_decision(final_state, expected_ticker=selections["ticker"])
        graph._enforce_decision_guard(
            final_state,
            expected_ticker=selections["ticker"],
            executor=executor,
        )
        decision = (
            (final_state.get("final_trade_decision_structured") or {}).get("action")
            or graph.process_signal(final_state["final_trade_decision"])
        )

        # Update all agent statuses to completed
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "Analysis", f"Completed analysis for {selections['analysis_date']}"
        )

        # Update final report sections
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        # Append canonical structured decision payload for traceability.
        try:
            structured_for_report = final_state.get("final_trade_decision_structured")
            if isinstance(structured_for_report, dict):
                decision_path = report_dir / "final_trade_decision.md"
                if decision_path.exists():
                    with open(decision_path, "a", encoding="utf-8") as f:
                        f.write("\n\n## Canonical Decision JSON\n\n```json\n")
                        f.write(
                            json.dumps(
                                structured_for_report,
                                indent=2,
                                ensure_ascii=False,
                                default=str,
                            )
                        )
                        f.write("\n```\n")
                        if str(structured_for_report.get("decision_version", "")).lower() == "v2":
                            execution_plan = structured_for_report.get("execution_plan") or []
                            default_action = structured_for_report.get("default_action")
                            f.write("\n## Conditional Plan Summary\n\n")
                            f.write(f"- Plan mode: `{structured_for_report.get('plan_mode')}`\n")
                            f.write(
                                f"- Execution intent: `{structured_for_report.get('execution_intent')}`\n"
                            )
                            if structured_for_report.get("override_reason"):
                                f.write(
                                    f"- Override reason: `{structured_for_report.get('override_reason')}`\n"
                                )
                            f.write(f"- Branches: `{len(execution_plan)}`\n")
                            f.write(
                                f"- Immediate branch: `{structured_for_report.get('immediate_branch_id') or 'none'}`\n"
                            )
                            if isinstance(default_action, str):
                                f.write(f"- Default action branch: `{default_action}`\n")
                            elif isinstance(default_action, dict):
                                f.write(
                                    f"- Default action template: `{default_action.get('action', 'HOLD')}`\n"
                                )
                            for branch in execution_plan[:5]:
                                if not isinstance(branch, dict):
                                    continue
                                cond = branch.get("conditions") or {}
                                f.write(
                                    f"- `{branch.get('branch_id')}` -> `{((branch.get('action_template') or {}).get('action') or 'HOLD')}`"
                                )
                                notes = []
                                price = cond.get("price") or {}
                                if price.get("close_above") is not None:
                                    notes.append(f"close>{price.get('close_above')}")
                                if price.get("close_below") is not None:
                                    notes.append(f"close<{price.get('close_below')}")
                                volume = cond.get("volume") or {}
                                if volume.get("volume_ratio_min") is not None:
                                    notes.append(f"vol>={volume.get('volume_ratio_min')}x")
                                if (cond.get("event_conditions") or []):
                                    notes.append("event_confirm")
                                if notes:
                                    f.write(f" ({', '.join(notes)})")
                                f.write("\n")
        except Exception:
            pass

        # Persist a single consolidated report + final state snapshot for later review.
        final_report_path = report_dir / "final_report.md"
        if message_buffer.final_report:
            with open(final_report_path, "w", encoding="utf-8") as f:
                f.write(_strip_thinking_blocks(message_buffer.final_report))

        # Persist a separate execution report (includes "no action" / disabled).
        try:
            run_finished_at = datetime.datetime.now()
            elapsed_s = (run_finished_at - run_started_at).total_seconds()

            llm_metrics = (final_state or {}).get("llm_metrics", {}) if final_state else {}
            tool_calls_count = int(
                llm_metrics.get("tool_calls_issued_total", len(message_buffer.tool_calls))
            )
            llm_calls_exact = int(
                llm_metrics.get("llm_api_calls_total", 0)
            )
            reports_count = sum(
                1 for content in message_buffer.report_sections.values() if content is not None
            )

            exec_sel = selections.get("execution") or {}
            exec_enabled = bool(exec_sel.get("enabled", False))

            lines = []
            lines.append("# Execution Report")
            lines.append("")
            lines.append(f"- Ticker: `{selections['ticker']}`")
            lines.append(f"- Analysis date: `{selections['analysis_date']}`")
            lines.append(f"- Final decision: `{decision}`")
            lines.append(f"- Execution enabled: `{exec_enabled}`")
            lines.append("")

            if executor:
                lines.append("## Alpaca Executor")
                lines.append("")
                if getattr(executor, "trading_base_url", None):
                    lines.append(f"- Trading URL: `{executor.trading_base_url}`")
                if getattr(executor, "data_base_url", None):
                    lines.append(f"- Data URL: `{executor.data_base_url}`")
                lines.append(f"- Paper trading: `{exec_sel.get('paper', True)}`")
                lines.append(f"- Position size pct: `{exec_sel.get('position_size_pct', 0.10)}`")
                lines.append("")

                lines.append("## Execution Result")
                lines.append("")
                if execution_result is None:
                    lines.append("- Result: `not attempted`")
                    if execution_error:
                        lines.append(f"- Error: `{execution_error}`")
                else:
                    lines.append(f"- Executed: `{bool(execution_result.get('executed'))}`")
                    if execution_result.get("message"):
                        lines.append(f"- Message: {execution_result.get('message')}")
                    if execution_result.get("error"):
                        lines.append(f"- Error: `{execution_result.get('error')}`")
                    if execution_result.get("side"):
                        lines.append(f"- Side: `{execution_result.get('side')}`")
                    if execution_result.get("qty") is not None:
                        lines.append(f"- Qty: `{execution_result.get('qty')}`")
                    if execution_result.get("price") is not None:
                        try:
                            lines.append(f"- Price: `${float(execution_result.get('price')):.4f}`")
                        except Exception:
                            lines.append(f"- Price: `{execution_result.get('price')}`")
                    if execution_result.get("requested_limit_price") is not None:
                        try:
                            lines.append(f"- Requested limit: `${float(execution_result.get('requested_limit_price')):.4f}`")
                        except Exception:
                            lines.append(f"- Requested limit: `{execution_result.get('requested_limit_price')}`")
                    spec = execution_result.get("order_spec") or {}
                    if isinstance(spec, dict) and spec.get("order_type"):
                        lines.append(f"- Requested order type: `{spec.get('order_type')}`")
                    if execution_result.get("decision_source"):
                        lines.append(f"- Decision source: `{execution_result.get('decision_source')}`")
                    if execution_result.get("decision_version") is not None:
                        lines.append(f"- Decision version: `{execution_result.get('decision_version')}`")
                    if execution_result.get("decision_validation_ok") is not None:
                        lines.append(f"- Decision validation ok: `{execution_result.get('decision_validation_ok')}`")
                    if execution_result.get("decision_validation_error"):
                        lines.append(f"- Decision validation error: `{execution_result.get('decision_validation_error')}`")
                    if execution_result.get("decision_price_guard_error"):
                        lines.append(f"- Decision price guard error: `{execution_result.get('decision_price_guard_error')}`")
                    if execution_result.get("market_snapshot_reference_price") is not None:
                        lines.append(f"- Snapshot reference price: `{execution_result.get('market_snapshot_reference_price')}`")
                    if execution_result.get("market_snapshot_source"):
                        lines.append(f"- Snapshot source: `{execution_result.get('market_snapshot_source')}`")

                    quote = execution_result.get("quote") or {}
                    if quote:
                        lines.append("")
                        lines.append("### Quote")
                        lines.append("")
                        for k in ("bid_price", "ask_price", "bid_size", "ask_size", "timestamp"):
                            if k in quote and quote.get(k) is not None:
                                lines.append(f"- {k}: `{quote.get(k)}`")

                    order = execution_result.get("order") or {}
                    if order:
                        lines.append("")
                        lines.append("### Order")
                        lines.append("")
                        for k in ("id", "symbol", "qty", "side", "type", "status", "submitted_at"):
                            if k in order and order.get(k) is not None:
                                lines.append(f"- {k}: `{order.get(k)}`")

                if execution_portfolio_summary:
                    lines.append("")
                    lines.append("## Portfolio Summary")
                    lines.append("")
                    lines.append(f"- Account value: `${execution_portfolio_summary.get('account_value', 0):,.2f}`")
                    lines.append(f"- Cash: `${execution_portfolio_summary.get('cash', 0):,.2f}`")
                    lines.append(f"- Buying power: `${execution_portfolio_summary.get('buying_power', 0):,.2f}`")
                    lines.append(f"- Positions: `{execution_portfolio_summary.get('positions_count', 0)}`")

            else:
                lines.append("## Alpaca Executor")
                lines.append("")
                lines.append("- Status: `not configured`")
                if exec_enabled:
                    lines.append("- Note: execution was enabled but executor setup failed (missing creds/deps or initialization error).")
                lines.append("")

            lines.append("## Analysis Stats")
            lines.append("")
            lines.append(f"- Duration (s): `{elapsed_s:.2f}`")
            lines.append(f"- Stream chunks: `{len(trace)}`")
            lines.append(f"- Tool calls: `{tool_calls_count}`")
            lines.append(f"- LLM calls (API): `{llm_calls_exact}`")
            by_model = llm_metrics.get("llm_api_calls_by_model") or {}
            if by_model:
                lines.append(f"- LLM calls by model: `{by_model}`")
            lines.append(f"- Reports generated: `{reports_count}`")
            guard = (final_state or {}).get("decision_guard", {}) if final_state else {}
            snapshot = (final_state or {}).get("market_snapshot", {}) if final_state else {}
            if guard:
                lines.append(f"- Decision guard validation ok: `{guard.get('validation_ok')}`")
                if guard.get("mode_selected_by") is not None:
                    lines.append(f"- Decision mode selected by: `{guard.get('mode_selected_by')}`")
                if guard.get("trader_selected_execution_intent") is not None:
                    lines.append(
                        f"- Trader selected execution intent: `{guard.get('trader_selected_execution_intent')}`"
                    )
                if guard.get("final_execution_intent") is not None:
                    lines.append(f"- Final execution intent: `{guard.get('final_execution_intent')}`")
                if guard.get("mode_overridden") is not None:
                    lines.append(f"- Mode overridden: `{guard.get('mode_overridden')}`")
                if guard.get("override_reason"):
                    lines.append(f"- Override reason: `{guard.get('override_reason')}`")
                if guard.get("violations"):
                    lines.append(f"- Decision guard violations: `{guard.get('violations')}`")
                if guard.get("abort_reason"):
                    lines.append(f"- Decision guard abort reason: `{guard.get('abort_reason')}`")
            if snapshot:
                lines.append(f"- Market snapshot source: `{snapshot.get('source')}`")
                lines.append(f"- Market snapshot ref: `{snapshot.get('reference_price')}`")
            lines.append("")

            execution_report_path = report_dir / "execution_report.md"
            with open(execution_report_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            message_buffer.add_message("System", f"Warning: failed to write execution_report.md: {e}")

        final_state_path = results_dir / "final_state.json"
        try:
            import json

            with open(final_state_path, "w", encoding="utf-8") as f:
                json.dump(final_state, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            message_buffer.add_message("System", f"Warning: failed to write final_state.json: {e}")

        # Display the complete final report
        display_complete_report(final_state)

        update_display(layout)

    exec_sel = selections.get("execution") or {}
    exec_enabled = bool(exec_sel.get("enabled", False))

    return {
        "ticker": selections.get("ticker"),
        "analysis_date": selections.get("analysis_date"),
        "decision": decision,
        "results_dir": str(results_dir),
        "final_report_path": str(final_report_path) if message_buffer and message_buffer.final_report else None,
        "execution_enabled": exec_enabled,
        "executed": (bool(execution_result.get("executed")) if isinstance(execution_result, dict) else None),
        "execution_error": execution_error,
    }


def run_analysis():
    # First get all user selections
    selections = get_user_selections()

    # Portfolio analysis mode: skip single-ticker Live UI and run portfolio pipeline instead.
    if selections.get("analysis_mode") == "portfolio":
        from cli.portfolio_analysis_utils import (
            init_portfolio_context,
            run_portfolio_analysis_from_selections,
        )

        init_portfolio_context(console=console, setup_executor=setup_executor)
        run_portfolio_analysis_from_selections(selections)
        return

    # Discovery mode: run AI stock discovery (fresh) or resume from saved list
    if selections.get("analysis_mode") == "discovery":
        from cli.discovery_utils import (
            init_discovery_context,
            run_discovery_flow,
            run_discovery_resume_flow,
        )

        init_discovery_context(
            console_ref=console,
            setup_executor_ref=setup_executor,
            message_buffer_ref=message_buffer,
            create_layout_ref=create_layout,
            update_display_ref=update_display,
            display_complete_report_ref=display_complete_report,
        )
        if selections.get("discovery_mode_variant") == "resume":
            run_discovery_resume_flow(selections)
        else:
            run_discovery_flow(selections)
        return

    # Single ticker mode now supports multiple tickers (sequentially).
    tickers = selections.get("tickers") or (
        [selections["ticker"]] if selections.get("ticker") else []
    )
    if not tickers:
        raise ValueError("No tickers provided for single-ticker analysis mode.")

    summaries: list[dict] = []
    for idx, ticker in enumerate(tickers):
        per_sel = {**selections, "analysis_mode": "single", "ticker": ticker}
        try:
            summaries.append(
                run_single_ticker_analysis(
                    per_sel, batch_idx=idx, batch_total=len(tickers)
                )
            )
        except Exception as e:
            summaries.append(
                {
                    "ticker": ticker,
                    "analysis_date": selections.get("analysis_date"),
                    "decision": "ERROR",
                    "results_dir": None,
                    "final_report_path": None,
                    "execution_enabled": bool(
                        (selections.get("execution") or {}).get("enabled", False)
                    ),
                    "executed": None,
                    "execution_error": str(e),
                }
            )
            if console is not None:
                console.print(
                    Panel(
                        f"[red]Error analyzing {ticker}:[/red] {e}",
                        title="Ticker Failed",
                        border_style="red",
                        padding=(1, 2),
                    )
                )

        # Sleep between tickers to mitigate provider rate limits / burst caps.
        if idx < len(tickers) - 1:
            if console is not None:
                console.print("[dim]Waiting 10 seconds before the next ticker...[/dim]")
            time.sleep(10)

    # Batch summary table
    if console is not None and len(summaries) > 1:
        table = Table(title="Batch Summary", box=box.SIMPLE_HEAD, expand=True)
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Ticker", style="cyan", width=10)
        table.add_column("Decision", style="white", width=10)
        table.add_column("Executed", style="white", width=10)
        table.add_column("Report", style="dim")
        table.add_column("Error", style="red")

        for i, s in enumerate(summaries):
            executed = s.get("executed")
            executed_str = "-" if executed is None else ("yes" if executed else "no")
            report = s.get("final_report_path") or "-"
            err = s.get("execution_error") or ""
            table.add_row(
                str(i + 1),
                str(s.get("ticker") or "?"),
                str(s.get("decision") or "-"),
                executed_str,
                report,
                err,
            )

        console.print()
        console.print(table)
        console.print()
