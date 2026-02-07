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

from tradingagents.graph.stock_discovery import StockDiscoveryGraph, DiscoveryResult
from tradingagents.default_config import DEFAULT_CONFIG
from cli.discovery_report_logger import (
    write_deep_analysis_report,
    write_discovery_report,
)


# Injected from cli/main.py
console = None
setup_executor = None
message_buffer = None
create_layout = None
update_display = None
display_complete_report = None


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
            Markdown(result.report),
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
    from tradingagents.graph.trading_graph import TradingAgentsGraph
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
    graph = TradingAgentsGraph(
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
                structured = graph.extract_structured_decision(
                    final_state.get("final_trade_decision", "")
                )
                decision = structured.get("action") or graph.process_signal(
                    final_state.get("final_trade_decision", "")
                )

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

    # Get trade date
    trade_date = selections.get("analysis_date") or datetime.datetime.now().strftime("%Y-%m-%d")

    console.print(f"[cyan]Starting discovery for date: {trade_date}[/cyan]")
    console.print(f"[dim]Using LLM: {config['deep_think_llm']} ({config['llm_provider']})[/dim]")
    console.print()

    # Run discovery
    with console.status("[bold cyan]Running stock discovery...[/bold cyan]", spinner="dots"):
        discovery_graph = StockDiscoveryGraph(
            config=config,
            debug=False,
        )
        result = discovery_graph.run_discovery(trade_date=trade_date)

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
