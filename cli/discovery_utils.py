# cli/discovery_utils.py
"""
CLI utilities for stock discovery mode.
Handles the discovery flow, display, and integration with deep analysis.
"""

import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns
from rich.markdown import Markdown
from rich import box
from rich.rule import Rule
from rich.align import Align

from verumtrade.graph.stock_discovery import StockDiscoveryGraph, DiscoveryResult
from verumtrade.default_config import DEFAULT_CONFIG
from verumtrade.execution import fetch_portfolio_symbols
from verumtrade.execution.execution_kwargs import executor_kwargs_from_structured
from cli.discovery_report_logger import (
    load_tickers_from_discovery_report,
    write_deep_analysis_report,
    write_discovery_report,
)
from cli.discovery_stage_logger import DiscoveryStageProgressLogger


# Injected from cli/main.py
console = None
setup_executor = None
message_buffer = None
create_layout = None
update_display = None
display_complete_report = None


def _normalize_console_unsafe_text(text: str) -> str:
    """Normalize characters that can fail on cp1252 terminals."""
    return str(text).replace("\u2011", "-")


def init_discovery_context(
    *,
    console_ref,
    setup_executor_ref,
    message_buffer_ref=None,
    create_layout_ref=None,
    update_display_ref=None,
    display_complete_report_ref=None,
):
    """Inject dependencies from main CLI."""
    global console, setup_executor, message_buffer, create_layout, update_display, display_complete_report
    console = console_ref
    setup_executor = setup_executor_ref
    message_buffer = message_buffer_ref
    create_layout = create_layout_ref
    update_display = update_display_ref
    display_complete_report = display_complete_report_ref


def display_discovery_header():
    """Display the stock discovery mode header."""
    header = """
╔═══════════════════════════════════════════════════════════════╗
║                   🔍 STOCK DISCOVERY MODE                     ║
║                                                               ║
║  Let AI discover promising stocks for you using:              ║
║  • Web search for market trends                               ║
║  • Sector performance analysis                                ║
║  • Technical breakout screening                               ║
║  • News catalyst detection                                    ║
╚═══════════════════════════════════════════════════════════════╝
"""
    console.print(Panel(
        header,
        border_style="cyan",
        padding=(0, 1),
    ))


def display_discovery_result(result: DiscoveryResult):
    """Display the discovery results in rich panels."""
    if not result.success:
        console.print(Panel(
            f"[red]Discovery failed: {result.error}[/red]",
            title="Error",
            border_style="red",
        ))
        return

    # Discovery summary panel
    summary_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    summary_table.add_column("Field", style="cyan")
    summary_table.add_column("Value", style="white")

    summary_table.add_row("Date", result.trade_date)
    summary_table.add_row("Stocks Found", str(len(result.tickers)))
    summary_table.add_row("Tickers", ", ".join(result.tickers) if result.tickers else "None")
    summary_table.add_row("Tool Iterations", str(result.iterations))

    console.print(Panel(
        summary_table,
        title="[bold green]✓ Discovery Complete[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))

    # Full recommendation report
    if result.report:
        console.print()
        console.print(Panel(
            Markdown(_normalize_console_unsafe_text(result.report)),
            title="Recommendation Report",
            border_style="blue",
            padding=(1, 2),
        ))


def display_recommendations_table(tickers: list, action_prompt: bool = True):
    """Display recommended tickers in a table with optional action prompt."""
    if not tickers:
        console.print("[yellow]No stocks were recommended.[/yellow]")
        return

    table = Table(
        title="Recommended Stocks",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Ticker", style="cyan bold")
    table.add_column("Action", style="green")

    for i, ticker in enumerate(tickers, 1):
        table.add_row(str(i), ticker, "Ready for analysis")

    console.print()
    console.print(Align.center(table))


def _run_discovery_deep_analysis(
    selected_tickers,
    trade_date,
    time_horizon,
    config,
    selections,
):
    """
    Run streaming deep analysis on each discovered ticker, showing the same
    Live progress display used in single-ticker analysis mode.
    """
    import time
    import logging
    from rich.live import Live
    from verumtrade.graph.verumtrade_graph import VerumtradeGraph
    from functools import wraps
    from verumtrade.utils.report_sanitization import strip_thinking_blocks as _strip_thinking_blocks
    from cli.analysis_utils import (
        process_analysis_stream,
        _reset_message_buffer,
    )

    logger = logging.getLogger("DiscoveryDeepAnalysis")
    selected_analysts = ["market", "news", "fundamentals"]

    # Setup executor if requested
    executor = None
    exec_settings = selections.get("execution", {})
    if exec_settings.get("enabled"):
        results_dir = Path(config["results_dir"]) / "discovery" / trade_date
        results_dir.mkdir(parents=True, exist_ok=True)
        executor = setup_executor(exec_settings, log_dir=results_dir)

    # Create the graph once (reused across tickers)
    graph = VerumtradeGraph(
        selected_analysts=selected_analysts,
        config=config,
        debug=False,
    )

    results = []
    total = len(selected_tickers)

    for idx, ticker in enumerate(selected_tickers):
        console.print()
        console.print(Rule(f"[bold green]Analyzing {idx + 1}/{total}: {ticker}[/bold green]"))
        console.print()

        _reset_message_buffer()

        # Set initial agent statuses
        first_analyst = f"{selected_analysts[0].capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")

        message_buffer.add_message("System", f"Discovery deep analysis: {ticker}")
        message_buffer.add_message("System", f"Analysis date: {trade_date}")

        init_agent_state = graph.propagator.create_initial_state(
            ticker,
            trade_date,
            time_horizon=time_horizon,
        )
        args = graph.propagator.get_graph_args()

        layout = create_layout()

        # --- Inject log/report writers for this ticker ---
        # Store original methods if not already saved to prevent stacking
        if not hasattr(message_buffer, "_orig_add_message"):
            message_buffer._orig_add_message = message_buffer.add_message
        if not hasattr(message_buffer, "_orig_add_tool_call"):
            message_buffer._orig_add_tool_call = message_buffer.add_tool_call
        if not hasattr(message_buffer, "_orig_update_report_section"):
            message_buffer._orig_update_report_section = message_buffer.update_report_section

        # Restore originals before wrapping to prevent recursive nesting
        message_buffer.add_message = message_buffer._orig_add_message
        message_buffer.add_tool_call = message_buffer._orig_add_tool_call
        message_buffer.update_report_section = message_buffer._orig_update_report_section

        # Directories
        results_dir = Path(config["results_dir"]) / "stocks" / trade_date / ticker
        results_dir.mkdir(parents=True, exist_ok=True)
        report_dir = results_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        log_file = results_dir / "message_tool.log"
        log_file.touch(exist_ok=True)

        def save_message_decorator(obj, func_name):
            func = getattr(obj, func_name)
            @wraps(func)
            def wrapper(*args, **kwargs):
                func(*args, **kwargs)
                if not obj.messages:
                    return
                timestamp, message_type, content = obj.messages[-1]
                content = str(content).replace("\n", " ")
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"{timestamp} [{message_type}] {content}\n")
            return wrapper
        
        def save_tool_call_decorator(obj, func_name):
            func = getattr(obj, func_name)
            @wraps(func)
            def wrapper(*args, **kwargs):
                func(*args, **kwargs)
                if not obj.tool_calls:
                    return
                timestamp, tool_name, kwargs_args = obj.tool_calls[-1]
                if isinstance(kwargs_args, dict):
                    args_str = ", ".join(f"{k}={v}" for k, v in kwargs_args.items())
                else:
                    args_str = str(kwargs_args)
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
        # --- End log/report injection ---

        try:
            with Live(layout, refresh_per_second=4):
                update_display(layout)
                trace, final_state = process_analysis_stream(
                    graph, init_agent_state, args,
                    message_buffer, update_display, layout,
                    selected_analysts,
                )

                # Mark all agents completed
                for agent in message_buffer.agent_status:
                    message_buffer.update_agent_status(agent, "completed")
                update_display(layout)

            if final_state:
                graph._attach_canonical_decision(final_state, expected_ticker=ticker)
                if executor:
                    graph._enforce_decision_guard(
                        final_state,
                        expected_ticker=ticker,
                        executor=executor,
                    )
                structured = final_state.get("final_trade_decision_structured")
                decision = (structured or {}).get("action") or graph.process_signal(
                    final_state.get("final_trade_decision", "")
                )
                execution_result = None

                if executor:
                    if not isinstance(structured, dict):
                        execution_result = {
                            "ticker": ticker,
                            "signal": decision,
                            "trade_date": trade_date,
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
                            "ticker": ticker,
                            "signal": decision,
                            "trade_date": trade_date,
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
                    elif decision in {"BUY", "SELL"}:
                        execution_result = executor.execute_signal(
                            ticker=ticker,
                            signal=decision,
                            analysis_state=final_state,
                            trade_date=trade_date,
                            agent_quantity=structured.get("quantity"),
                            agent_limit_price=structured.get("limit_price"),
                            **executor_kwargs_from_structured(structured),
                        )
                    else:
                        execution_result = {
                            "ticker": ticker,
                            "signal": decision,
                            "trade_date": trade_date,
                            "executed": False,
                            "message": "Discovery mode executes BUY/SELL only",
                        }

                conviction_score = _calculate_conviction(final_state, decision)

                results.append({
                    "ticker": ticker,
                    "decision": decision,
                    "conviction_score": conviction_score,
                    "final_state": final_state,
                    "market_report": final_state.get("market_report", ""),
                    "fundamentals_report": final_state.get("fundamentals_report", ""),
                    "news_report": final_state.get("news_report", ""),
                    "final_decision": final_state.get("final_trade_decision", ""),
                    "execution_result": execution_result,
                })

                display_complete_report(final_state)
            else:
                logger.error(f"No final state for {ticker}")

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}")
            console.print(Panel(
                f"[red]Error analyzing {ticker}:[/red] {e}",
                title="Analysis Failed",
                border_style="red",
                padding=(1, 2),
            ))

        # Brief pause between tickers
        if idx < total - 1:
            console.print("[dim]Waiting before next ticker...[/dim]")
            time.sleep(5)

    # Rank by conviction score
    results.sort(key=lambda x: x["conviction_score"], reverse=True)

    # Filter to BUY signals only
    buy_signals = [r for r in results if r["decision"] == "BUY"]

    logger.info(
        f"Discovery analysis complete. {len(buy_signals)} BUY signals out of "
        f"{len(results)} analyzed"
    )

    return buy_signals if buy_signals else results


def _calculate_conviction(final_state, decision):
    """
    Calculate a conviction score (0-100) based on analysis quality.
    Mirrors BatchAnalyzer._calculate_conviction logic.
    """
    score = 0.0

    if decision == "BUY":
        score = 60
    elif decision == "SELL":
        score = 40
    else:
        score = 50

    final_text = final_state.get("final_trade_decision", "").lower()

    if any(word in final_text for word in ["strong", "compelling", "excellent", "outstanding"]):
        score += 10
    if any(word in final_text for word in ["high confidence", "strongly recommend", "clear opportunity"]):
        score += 10

    if any(word in final_text for word in ["uncertain", "mixed", "unclear", "cautious"]):
        score -= 10
    if any(word in final_text for word in ["weak", "concerning", "risky"]):
        score -= 10

    if "investment_debate_state" in final_state:
        debate = final_state["investment_debate_state"]
        judge_decision = debate.get("judge_decision", "").lower()

        if decision == "BUY" and "bull" in judge_decision and "strong" in judge_decision:
            score += 5
        elif decision == "SELL" and "bear" in judge_decision and "strong" in judge_decision:
            score += 5

    return max(0, min(100, score))


def run_discovery_flow(selections: Dict[str, Any]):
    """
    Main discovery flow handler.

    Args:
        selections: User selections from the main CLI
    """
    import questionary

    display_discovery_header()
    console.print()

    # Build config from selections
    config = DEFAULT_CONFIG.copy()
    config["deep_think_llm"] = selections.get("deep_thinker", "gpt-4o")
    config["quick_think_llm"] = selections.get("shallow_thinker", "gpt-4o-mini")
    config["llm_provider"] = selections.get("llm_provider", "openai").lower()
    config["backend_url"] = selections.get("backend_url")
    catalyst_mode = selections.get("discovery_catalyst_mode", "daily_calendar")
    config.setdefault("numeric_filter", {})
    config["numeric_filter"].setdefault("catalyst_prefilter", {})
    config["numeric_filter"]["catalyst_prefilter"]["mode"] = catalyst_mode

    # Get trade date
    trade_date = selections.get("analysis_date") or datetime.datetime.now().strftime("%Y-%m-%d")

    console.print(f"[cyan]Starting discovery for date: {trade_date}[/cyan]")
    console.print(f"[dim]Using LLM: {config['deep_think_llm']} ({config['llm_provider']})[/dim]")
    console.print(f"[dim]Stage 0 catalyst filter mode: {catalyst_mode}[/dim]")
    excluded_tickers = []
    try:
        excluded_tickers = sorted(
            {
                str(t).strip().upper()
                for t in fetch_portfolio_symbols()
                if str(t).strip()
            }
        )
        if excluded_tickers:
            console.print(
                f"[dim]Skipping existing portfolio positions in discovery: {', '.join(excluded_tickers)}[/dim]"
            )
    except Exception as e:
        console.print(
            f"[yellow]Warning: could not fetch current portfolio positions for discovery exclusion ({e})[/yellow]"
        )
    console.print()

    # Run discovery with stage progress logging (Stage 0 + Stage 1).
    discovery_track = selections.get("discovery_track", "enricher")
    console.print(f"[dim]Discovery track: {discovery_track}[/dim]")
    stage_logger = DiscoveryStageProgressLogger(console=console)
    config["discovery_progress_callback"] = stage_logger.callback
    stage_logger.start()
    try:
        discovery_graph = StockDiscoveryGraph(
            config=config,
            debug=False,
        )
        result = discovery_graph.run_discovery(
            trade_date=trade_date,
            exclude_tickers=excluded_tickers,
            discovery_track=discovery_track,
        )
    finally:
        stage_logger.stop()
        config.pop("discovery_progress_callback", None)


    # Persist discovery report regardless of success/failure.
    try:
        discovery_report_path = write_discovery_report(
            results_root=config["results_dir"],
            trade_date=trade_date,
            result=result,
            llm_provider=config["llm_provider"],
            deep_think_model=config["deep_think_llm"],
        )
        console.print(f"[dim]Discovery report saved: {discovery_report_path}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: failed to write discovery report ({e})[/yellow]")

    # Display results
    display_discovery_result(result)

    if not result.success or not result.tickers:
        console.print("[yellow]No recommendations to analyze. Exiting discovery mode.[/yellow]")
        return

    # Ask if user wants deep analysis
    console.print()
    console.print(Rule("[bold]Next Steps[/bold]"))
    console.print()

    deep_analysis = questionary.confirm(
        "Run deep analysis on these recommendations?",
        default=True,
    ).ask()

    if not deep_analysis:
        console.print("[green]Discovery complete. You can manually analyze these tickers.[/green]")
        return

    # Select which tickers to analyze
    selected_tickers = questionary.checkbox(
        "Select tickers for deep analysis:",
        choices=[questionary.Choice(t, checked=True) for t in result.tickers],
    ).ask()

    if not selected_tickers:
        console.print("[yellow]No tickers selected for analysis.[/yellow]")
        return

    # Select holding period / time horizon for the deep analysis run
    from cli.utils import select_time_horizon

    console.print()
    time_horizon = select_time_horizon()

    # Run deep analysis
    console.print()
    console.print(f"[cyan]Running deep analysis on: {', '.join(selected_tickers)}[/cyan]")

    analysis_results = _run_discovery_deep_analysis(
        selected_tickers=selected_tickers,
        trade_date=trade_date,
        time_horizon=time_horizon,
        config=config,
        selections=selections,
    )

    # Persist deep analysis report regardless of result count.
    try:
        deep_report_path = write_deep_analysis_report(
            results_root=config["results_dir"],
            trade_date=trade_date,
            selected_tickers=selected_tickers,
            analysis_results=analysis_results,
            time_horizon=time_horizon,
        )
        console.print(f"[dim]Deep analysis report saved: {deep_report_path}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: failed to write deep analysis report ({e})[/yellow]")

    # Display analysis results
    display_analysis_results(analysis_results)


def run_discovery_resume_flow(selections: Dict[str, Any]):
    """
    Resume the discovery deep-analysis phase from a previously saved ticker list.

    Loads ``results/discovery/<date>/reports/stock_discovery_report.md``,
    extracts the discovered tickers, and runs the same deep-analysis pipeline
    used by the normal discovery flow — skipping the expensive discovery stage.

    Args:
        selections: User selections from the main CLI (same shape as for
                    ``run_discovery_flow``).
    """
    import questionary

    console.print()
    console.print(
        Panel(
            "[bold cyan]📂 RESUME FROM SAVED TICKER LIST[/bold cyan]\n"
            "[dim]Loading previously discovered tickers for deep analysis...[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Build config from selections (same as run_discovery_flow)
    config = DEFAULT_CONFIG.copy()
    config["deep_think_llm"] = selections.get("deep_thinker", "gpt-4o")
    config["quick_think_llm"] = selections.get("shallow_thinker", "gpt-4o-mini")
    config["llm_provider"] = selections.get("llm_provider", "openai").lower()
    config["backend_url"] = selections.get("backend_url")

    trade_date = selections.get("analysis_date") or datetime.datetime.now().strftime("%Y-%m-%d")

    # Load saved tickers
    try:
        tickers, report_path = load_tickers_from_discovery_report(
            results_root=config["results_dir"],
            trade_date=trade_date,
        )
    except FileNotFoundError as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Report Not Found",
                border_style="red",
                padding=(1, 2),
            )
        )
        return
    except ValueError as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="Parse Error",
                border_style="red",
                padding=(1, 2),
            )
        )
        return

    console.print(f"[dim]Loaded from: {report_path}[/dim]")
    console.print(f"[green]Found {len(tickers)} ticker(s): {', '.join(tickers)}[/green]")
    console.print()

    # Let user confirm / deselect tickers
    display_recommendations_table(tickers, action_prompt=False)
    console.print()

    selected_tickers = questionary.checkbox(
        "Select tickers for deep analysis:",
        choices=[questionary.Choice(t, checked=True) for t in tickers],
    ).ask()

    if not selected_tickers:
        console.print("[yellow]No tickers selected. Exiting.[/yellow]")
        return

    # Time horizon
    from cli.utils import select_time_horizon
    console.print()
    time_horizon = select_time_horizon()

    # Run deep analysis (identical path to normal discovery flow)
    console.print()
    console.print(f"[cyan]Running deep analysis on: {', '.join(selected_tickers)}[/cyan]")

    analysis_results = _run_discovery_deep_analysis(
        selected_tickers=selected_tickers,
        trade_date=trade_date,
        time_horizon=time_horizon,
        config=config,
        selections=selections,
    )

    # Persist deep analysis report
    try:
        deep_report_path = write_deep_analysis_report(
            results_root=config["results_dir"],
            trade_date=trade_date,
            selected_tickers=selected_tickers,
            analysis_results=analysis_results,
            time_horizon=time_horizon,
        )
        console.print(f"[dim]Deep analysis report saved: {deep_report_path}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: failed to write deep analysis report ({e})[/yellow]")

    # Display results
    display_analysis_results(analysis_results)


def display_analysis_results(results: list):
    """Display deep analysis results."""
    if not results:
        console.print("[yellow]No analysis results available.[/yellow]")
        return

    console.print()
    console.print(Rule("[bold green]Deep Analysis Results[/bold green]"))
    console.print()

    for result in results:
        ticker = result.get("ticker", "?")
        decision = result.get("decision", "UNKNOWN")
        conviction = result.get("conviction_score", 0)

        # Color code by decision
        decision_color = {
            "BUY": "green",
            "SELL": "red", 
            "HOLD": "yellow",
        }.get(decision, "white")

        # Summary panel
        summary = f"""
**Decision:** [{decision_color}]{decision}[/{decision_color}]
**Conviction Score:** {conviction:.1f}/100
"""

        # Add key reports if available
        if result.get("market_report"):
            summary += f"\n**Market Analysis:** {result['market_report'][:200]}..."

        if result.get("final_decision"):
            summary += f"\n\n**Final Analysis:**\n{result['final_decision'][:500]}..."

        console.print(Panel(
            Markdown(summary),
            title=f"[bold]{ticker}[/bold]",
            border_style=decision_color,
            padding=(1, 2),
        ))
        console.print()
