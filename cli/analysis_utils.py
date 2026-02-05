import datetime
import json
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

def update_research_team_status(status):
    """Update status for all research team members and trader."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager", "Trader"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)

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

def run_analysis():
    run_started_at = datetime.datetime.now()
    # First get all user selections
    selections = get_user_selections()

    # Portfolio analysis mode: skip single-ticker Live UI and run portfolio pipeline instead.
    if selections.get("analysis_mode") == "portfolio":
        from cli.portfolio_analysis_utils import init_portfolio_context, run_portfolio_analysis_from_selections

        init_portfolio_context(console=console, setup_executor=setup_executor)
        run_portfolio_analysis_from_selections(selections)
        return

    # Discovery mode: run AI stock discovery
    if selections.get("analysis_mode") == "discovery":
        from cli.discovery_utils import init_discovery_context, run_discovery_flow

        init_discovery_context(console_ref=console, setup_executor_ref=setup_executor)
        run_discovery_flow(selections)
        return

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
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
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
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
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(content)
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

        # Initialize state and get graph args
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"],
            selections["analysis_date"],
            portfolio_context=portfolio_ctx,
        )
        args = graph.propagator.get_graph_args()

        # Stream the analysis
        trace = []
        final_state = None
        execution_result = None
        execution_error: Optional[str] = None
        execution_portfolio_summary: Optional[dict] = None
        seen_messages = 0
        for chunk in graph.graph.stream(init_agent_state, **args):
            messages = chunk.get("messages") or []
            # Always process each streamed chunk. Some providers/steps mutate state without
            # appending messages, and we still want the UI to update.
            new_messages = messages[seen_messages:] if seen_messages <= len(messages) else []
            seen_messages = len(messages)
            if True:

                def _msg_type_and_content(msg):
                    # LangGraph can emit tuples, message objects, or dicts depending on provider/runtime.
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
                    # Returns list of (name, args) pairs.
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
                                # Best-effort: parse JSON args if possible, otherwise log raw string.
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

                for msg in new_messages:
                    msg_type, content = _msg_type_and_content(msg)
                    message_buffer.add_message(msg_type, content)

                    for tool_name, tool_args in _extract_tool_calls(msg):
                        message_buffer.add_tool_call(tool_name, tool_args)

                # Update reports and agent status based on chunk content
                # Analyst Team Reports
                if "market_report" in chunk and chunk["market_report"]:
                    message_buffer.update_report_section(
                        "market_report", chunk["market_report"]
                    )
                    message_buffer.update_agent_status("Market Analyst", "completed")
                    # Set next analyst to in_progress
                    if "social" in selections["analysts"]:
                        message_buffer.update_agent_status(
                            "Social Analyst", "in_progress"
                        )

                if "sentiment_report" in chunk and chunk["sentiment_report"]:
                    message_buffer.update_report_section(
                        "sentiment_report", chunk["sentiment_report"]
                    )
                    message_buffer.update_agent_status("Social Analyst", "completed")
                    # Set next analyst to in_progress
                    if "news" in selections["analysts"]:
                        message_buffer.update_agent_status(
                            "News Analyst", "in_progress"
                        )

                if "news_report" in chunk and chunk["news_report"]:
                    message_buffer.update_report_section(
                        "news_report", chunk["news_report"]
                    )
                    message_buffer.update_agent_status("News Analyst", "completed")
                    # Set next analyst to in_progress
                    if "fundamentals" in selections["analysts"]:
                        message_buffer.update_agent_status(
                            "Fundamentals Analyst", "in_progress"
                        )

                if "fundamentals_report" in chunk and chunk["fundamentals_report"]:
                    message_buffer.update_report_section(
                        "fundamentals_report", chunk["fundamentals_report"]
                    )
                    message_buffer.update_agent_status(
                        "Fundamentals Analyst", "completed"
                    )
                    # Set all research team members to in_progress
                    update_research_team_status("in_progress")

                # Research Team - Handle Investment Debate State
                if (
                    "investment_debate_state" in chunk
                    and chunk["investment_debate_state"]
                ):
                    debate_state = chunk["investment_debate_state"]

                    # Update Bull Researcher status and report
                    if "bull_history" in debate_state and debate_state["bull_history"]:
                        # Keep all research team members in progress
                        update_research_team_status("in_progress")
                        # Extract latest bull response
                        bull_responses = debate_state["bull_history"].split("\n")
                        latest_bull = bull_responses[-1] if bull_responses else ""
                        if latest_bull:
                            message_buffer.add_message("Reasoning", latest_bull)
                            # Update research report with bull's latest analysis
                            message_buffer.update_report_section(
                                "investment_plan",
                                f"### Bull Researcher Analysis\n{latest_bull}",
                            )

                    # Update Bear Researcher status and report
                    if "bear_history" in debate_state and debate_state["bear_history"]:
                        # Keep all research team members in progress
                        update_research_team_status("in_progress")
                        # Extract latest bear response
                        bear_responses = debate_state["bear_history"].split("\n")
                        latest_bear = bear_responses[-1] if bear_responses else ""
                        if latest_bear:
                            message_buffer.add_message("Reasoning", latest_bear)
                            # Update research report with bear's latest analysis
                            message_buffer.update_report_section(
                                "investment_plan",
                                f"{message_buffer.report_sections['investment_plan']}\n\n### Bear Researcher Analysis\n{latest_bear}",
                            )

                    # Update Research Manager status and final decision
                    if (
                        "judge_decision" in debate_state
                        and debate_state["judge_decision"]
                    ):
                        # Keep all research team members in progress until final decision
                        update_research_team_status("in_progress")
                        message_buffer.add_message(
                            "Reasoning",
                            f"Research Manager: {debate_state['judge_decision']}",
                        )
                        # Update research report with final decision
                        message_buffer.update_report_section(
                            "investment_plan",
                            f"{message_buffer.report_sections['investment_plan']}\n\n### Research Manager Decision\n{debate_state['judge_decision']}",
                        )
                        # Mark all research team members as completed
                        update_research_team_status("completed")
                        # Set first risk analyst to in_progress
                        message_buffer.update_agent_status(
                            "Risky Analyst", "in_progress"
                        )

                # Trading Team
                if (
                    "trader_investment_plan" in chunk
                    and chunk["trader_investment_plan"]
                ):
                    message_buffer.update_report_section(
                        "trader_investment_plan", chunk["trader_investment_plan"]
                    )
                    # Set first risk analyst to in_progress
                    message_buffer.update_agent_status("Risky Analyst", "in_progress")

                # Risk Management Team - Handle Risk Debate State
                if "risk_debate_state" in chunk and chunk["risk_debate_state"]:
                    risk_state = chunk["risk_debate_state"]

                    # Update Risky Analyst status and report
                    if (
                        "current_risky_response" in risk_state
                        and risk_state["current_risky_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Risky Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            f"Risky Analyst: {risk_state['current_risky_response']}",
                        )
                        # Update risk report with risky analyst's latest analysis only
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            f"### Risky Analyst Analysis\n{risk_state['current_risky_response']}",
                        )

                    # Update Safe Analyst status and report
                    if (
                        "current_safe_response" in risk_state
                        and risk_state["current_safe_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Safe Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            f"Safe Analyst: {risk_state['current_safe_response']}",
                        )
                        # Update risk report with safe analyst's latest analysis only
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            f"### Safe Analyst Analysis\n{risk_state['current_safe_response']}",
                        )

                    # Update Neutral Analyst status and report
                    if (
                        "current_neutral_response" in risk_state
                        and risk_state["current_neutral_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Neutral Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            f"Neutral Analyst: {risk_state['current_neutral_response']}",
                        )
                        # Update risk report with neutral analyst's latest analysis only
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            f"### Neutral Analyst Analysis\n{risk_state['current_neutral_response']}",
                        )

                    # Update Portfolio Manager status and final decision
                    if "judge_decision" in risk_state and risk_state["judge_decision"]:
                        message_buffer.update_agent_status(
                            "Portfolio Manager", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            f"Portfolio Manager: {risk_state['judge_decision']}",
                        )
                        # Update risk report with final decision only
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            f"### Portfolio Manager Decision\n{risk_state['judge_decision']}",
                        )
                        # Mark risk analysts as completed
                        message_buffer.update_agent_status("Risky Analyst", "completed")
                        message_buffer.update_agent_status("Safe Analyst", "completed")
                        message_buffer.update_agent_status(
                            "Neutral Analyst", "completed"
                        )
                        message_buffer.update_agent_status(
                            "Portfolio Manager", "completed"
                        )

                # Update the display
                update_display(layout)

            trace.append(chunk)
            final_state = chunk

        # Get final state and decision
        if final_state:
            structured = graph.extract_structured_decision(
                final_state["final_trade_decision"]
            )
            decision = structured.get("action") or graph.process_signal(
                final_state["final_trade_decision"]
            )

            # Execute trade if executor is configured
            if executor:
                try:
                    console.print("\n")
                    console.print(Rule("[bold yellow]Executing Trade[/bold yellow]"))
                    console.print()

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
        decision = graph.process_signal(final_state["final_trade_decision"])

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

        # Persist a single consolidated report + final state snapshot for later review.
        final_report_path = report_dir / "final_report.md"
        if message_buffer.final_report:
            with open(final_report_path, "w", encoding="utf-8") as f:
                f.write(message_buffer.final_report)

        # Persist a separate execution report (includes "no action" / disabled).
        try:
            run_finished_at = datetime.datetime.now()
            elapsed_s = (run_finished_at - run_started_at).total_seconds()

            tool_calls_count = len(message_buffer.tool_calls)
            llm_calls_count = sum(
                1 for _, msg_type, _ in message_buffer.messages if msg_type == "Reasoning"
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
            lines.append(f"- LLM calls: `{llm_calls_count}`")
            lines.append(f"- Reports generated: `{reports_count}`")
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
