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


# Injected from cli/main.py
console = None
setup_executor = None


def init_discovery_context(
    *,
    console_ref,
    setup_executor_ref,
):
    """Inject dependencies from main CLI."""
    global console, setup_executor
    console = console_ref
    setup_executor = setup_executor_ref


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

    # Run deep analysis
    console.print()
    console.print(f"[cyan]Running deep analysis on: {', '.join(selected_tickers)}[/cyan]")

    # Use BatchAnalyzer through the discovery graph
    with console.status("[bold cyan]Running deep analysis...[/bold cyan]", spinner="dots"):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.batch_analysis import BatchAnalyzer

        # Setup executor if requested
        executor = None
        exec_settings = selections.get("execution", {})
        if exec_settings.get("enabled"):
            results_dir = Path(config["results_dir"]) / "discovery" / trade_date
            results_dir.mkdir(parents=True, exist_ok=True)
            executor = setup_executor(exec_settings, log_dir=results_dir)

        # Create full analysis graph
        graph = TradingAgentsGraph(
            selected_analysts=["market", "news", "fundamentals"],
            config=config,
            debug=False,
        )

        batch_analyzer = BatchAnalyzer(
            graph=graph,
            executor=executor,
        )

        analysis_results = batch_analyzer.analyze_candidates(
            tickers=selected_tickers,
            trade_date=trade_date,
            max_positions=len(selected_tickers),
        )

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
