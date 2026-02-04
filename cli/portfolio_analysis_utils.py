import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from tradingagents.graph.portfolio_analyzer import PortfolioAnalyzer
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from cli.utils import (
    select_analysts,
    select_deep_thinking_agent,
    select_llm_provider,
    select_research_depth,
    select_shallow_thinking_agent,
)

# Injected from `cli/main.py` via `init_portfolio_context`
console: Console | None = None
setup_executor = None


def init_portfolio_context(*, console: Console, setup_executor):
    globals()["console"] = console
    globals()["setup_executor"] = setup_executor


def analyze_portfolio(execute_trades: bool, 
                      min_conviction: float, 
                      analysis_date: Optional[str], 
                      n_stocks: Optional[int] = None):

    """
    Analyze your entire portfolio and get rebalancing recommendations.

    This command will:
    1. Fetch all current positions from your brokerage
    2. **Triage**: Use AI to select N most analysis-worthy stocks (if n_stocks set)
    3. Analyze selected positions using the agent framework
    4. Calculate portfolio-level metrics
    5. Generate BUY/SELL/HOLD recommendations
    6. (Optional) Execute high-conviction trades
    7. Provide strategic insights and future recommendations
    """
    console.print("\n")
    console.print(
        Panel(
            "[bold green]Portfolio Analysis Mode[/bold green]\n"
            "[dim]Analyzing your entire portfolio with AI agents[/dim]",
            border_style="green",
            padding=(1, 2),
        )
    )

    selections = _get_portfolio_analysis_selections(execute_trades, analysis_date, n_stocks)
    selections["execution"] = {
        "enabled": bool(execute_trades),
        "provider": "alpaca",
        "paper": True,
        "position_size_pct": 0.10,
    }
    selections["min_conviction"] = float(min_conviction)

    run_portfolio_analysis_from_selections(selections)


def run_portfolio_analysis_from_selections(selections: dict) -> None:
    """Run portfolio analysis using pre-collected selections (used by main CLI wizard)."""
    if console is None or setup_executor is None:
        raise RuntimeError("Portfolio context not initialized (call init_portfolio_context first).")

    # Create config
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["max_recur_limit"] = max(100, selections["research_depth"] * 120)
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"]

    graph = TradingAgentsGraph(
        [analyst.value for analyst in selections["analysts"]],
        config=config,
        debug=False,
    )

    # Setup executor (always required to fetch portfolio, even if not executing trades)
    results_dir = Path(config["results_dir"]) / "portfolio_analysis" / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)

    exec_sel = selections.get("execution") or {}
    executor_settings = {
        "enabled": True,
        "provider": "alpaca",
        "paper": bool(exec_sel.get("paper", True)),
        "position_size_pct": float(exec_sel.get("position_size_pct", 0.10)),
    }
    executor = setup_executor(executor_settings, log_dir=results_dir / "execution_logs")
    if not executor:
        console.print("[red]Error: Could not initialize Alpaca executor[/red]")
        console.print("Portfolio analysis requires Alpaca credentials in `.env` to fetch positions.")
        raise typer.Exit(code=1)

    analyzer = PortfolioAnalyzer(graph=graph, executor=executor, analysis_date=selections["analysis_date"])

    execute_trades = bool(exec_sel.get("enabled", False))
    min_conviction = float(selections.get("min_conviction", 70.0))

    n_stocks = selections.get("n_stocks")  # None means "analyze all"
    # ------------------------------------------------------------------
    # Show triage configuration before running
    # ------------------------------------------------------------------
    if n_stocks is not None:
        console.print(
            Panel(
                f"[bold yellow]Triage enabled:[/bold yellow] AI will select the "
                f"top [bold]{n_stocks}[/bold] positions for full analysis.\n"
                f"[dim]Remaining positions will receive a lightweight HOLD recommendation.[/dim]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
    else:
        console.print("[dim]Triage disabled — all positions will be fully analyzed.[/dim]\n")

    console.print("\n[yellow]Running portfolio analysis...[/yellow]\n")
    with console.status("[bold green]Analyzing portfolio positions..."):
        results = analyzer.analyze_portfolio(execute_trades=execute_trades, 
                                             min_conviction=min_conviction,
                                             n_stocks=n_stocks)

    _display_portfolio_analysis_results(results, execute_trades)
    _save_portfolio_analysis_results(results, results_dir)

    console.print(f"\n[green]Results saved to: {results_dir}[/green]\n")


def _get_portfolio_analysis_selections(
    execute_trades: bool,
    analysis_date: Optional[str],
    n_stocks: Optional[int] = None,
) -> dict:
    """Get configuration for portfolio analysis."""
    import questionary

    # Use current date if not specified
    date = analysis_date or datetime.datetime.now().strftime("%Y-%m-%d")

    console.print(Panel(
        f"[bold]Analysis Date:[/bold] {date}\n"
        f"[bold]Execute Trades:[/bold] {execute_trades}",
        title="Configuration",
        border_style="blue"
    ))

    # ---- Step 1: Triage / N-stocks selection ----
    console.print(create_question_box(
        "Step 1: Portfolio Triage",
        "Should the AI pre-screen your portfolio to pick the top N stocks?\n"
        "This saves significant time and cost for large portfolios.",
    ))

    # Allow the caller to pre-set n_stocks (e.g. from the main wizard)
    if n_stocks is not None:
        selected_n_stocks = n_stocks
    else:
        triage_choice = questionary.select(
            "Enable portfolio triage?",
            choices=[
                questionary.Choice("Yes — let AI pick the most important stocks", value="yes"),
                questionary.Choice("No  — analyze every position (slow for large portfolios)", value="no"),
            ],
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=questionary.Style(
                [
                    ("selected", "fg:yellow noinherit"),
                    ("highlighted", "fg:yellow noinherit"),
                    ("pointer", "fg:yellow noinherit"),
                ]
            ),
        ).ask()

        if triage_choice == "yes":
            n_input = questionary.text(
                "How many stocks should the AI select for deep analysis?",
                default="3",
                validate=lambda x: (x.strip().isdigit() and int(x.strip()) > 0) or "Enter a positive integer.",
                style=questionary.Style(
                    [
                        ("text", "fg:green"),
                        ("highlighted", "noinherit"),
                    ]
                ),
            ).ask()
            selected_n_stocks = int(n_input) if n_input else None
        else:
            selected_n_stocks = None

    # ---- Step 2: Analysts ----
    console.print(create_question_box(
        "Step 2: Analysts Team", "Select your LLM analyst agents"
    ))
    selected_analysts = select_analysts()

    # ---- Step 3: Research depth ----
    console.print(create_question_box(
        "Step 3: Research Depth", "Select analysis depth"
    ))
    selected_research_depth = select_research_depth()

    # ---- Step 4: LLM provider ----
    console.print(create_question_box(
        "Step 4: LLM Provider", "Select which service to use"
    ))
    selected_llm_provider, backend_url = select_llm_provider()

    # ---- Step 5: Thinking agents ----
    console.print(create_question_box(
        "Step 5: Thinking Agents", "Select your AI models"
    ))
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    return {
        "analysis_date": date,
        "n_stocks": selected_n_stocks,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
    }


def _display_portfolio_analysis_results(results: Dict[str, Any], executed: bool):

    # ---- Triage Summary ----
    triage = results.get("triage")
    if triage:
        selected = triage.get("selected", [])
        skipped = triage.get("skipped", [])
        notes = triage.get("research_notes", "")

        triage_table = Table(
            show_header=True, header_style="bold yellow", box=box.SIMPLE_HEAD,
            padding=(0, 1), expand=True,
        )
        triage_table.add_column("#", style="dim", width=3, justify="center")
        triage_table.add_column("Ticker", style="cyan", width=8)
        triage_table.add_column("Status", style="green", width=12, justify="center")
        triage_table.add_column("Rationale", style="white", no_wrap=False)

        for s in selected:
            triage_table.add_row(
                str(s.get("priority", "")),
                s["ticker"],
                "[bold green]SELECTED[/bold green]",
                s.get("rationale", ""),
            )
        for s in skipped:
            triage_table.add_row(
                "-",
                s.get("ticker", "?"),
                "[dim]skipped[/dim]",
                s.get("rationale", ""),
            )

        console.print(Panel(
            triage_table,
            title="[bold yellow]Portfolio Triage[/bold yellow]",
            subtitle=f"[dim]{notes[:200]}[/dim]" if notes else None,
            border_style="yellow",
            padding=(1, 2),
        ))

    # ---- Portfolio Summary ----
    summary = results.get("portfolio_summary", {})
    metrics = results.get("portfolio_metrics", {})
    
    summary_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="white")
    
    summary_table.add_row("Total Value", f"${summary.get('account_value', 0):,.2f}")
    summary_table.add_row("Cash", f"${summary.get('cash', 0):,.2f}")
    summary_table.add_row("Positions", str(summary.get('positions_count', 0)))
    summary_table.add_row("Max Position %", f"{metrics.get('max_position_pct', 0)}%")
    summary_table.add_row("Avg Conviction", f"{metrics.get('avg_conviction', 0)}/100")
    summary_table.add_row("Health Status", metrics.get('portfolio_health', 'Unknown'))
    
    console.print(Panel(
        summary_table,
        title="[bold]Portfolio Overview[/bold]",
        border_style="cyan"
    ))
    
    # Recommendations
    recommendations = results.get("recommendations", [])
    if recommendations:
        rec_table = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 1))
        rec_table.add_column("Priority", style="yellow", justify="center", width=8)
        rec_table.add_column("Ticker", style="cyan", width=8)
        rec_table.add_column("Action", style="green", justify="center", width=8)
        rec_table.add_column("Conviction", style="magenta", justify="center", width=10)
        rec_table.add_column("Position %", justify="center", width=10)
        rec_table.add_column("Summary", style="white", no_wrap=False)
        
        for i, rec in enumerate(recommendations, 1):
            action_color = {
                "BUY": "green",
                "SELL": "red",
                "HOLD": "yellow"
            }.get(rec['action'], "white")

            # Dim triaged-out rows so the user sees they weren't deeply analyzed
            ticker_style = "[dim]" if rec.get("triaged_out") else ""
            ticker_end = "[/dim]" if rec.get("triaged_out") else ""

            rec_table.add_row(
                str(i),
                f"{ticker_style}{rec['ticker']}{ticker_end}",
                f"[{action_color}]{rec['action']}[/{action_color}]",
                f"{rec['conviction']:.0f}/100",
                f"{rec['current_position_pct']:.1f}%",
                (rec.get("decision_summary") or rec.get("rationale") or rec.get("suggested_action") or ""),
            )
        
        console.print(Panel(
            rec_table,
            title="[bold]Recommendations[/bold]",
            border_style="yellow"
        ))
    
    # Strategic Insights
    insights = results.get("strategic_insights", {})
    if insights:
        insights_md = "### Portfolio Assessment\n"
        insights_md += insights.get('portfolio_assessment', '') + "\n\n"
        
        insights_md += "### Key Risks\n"
        for risk in insights.get('key_risks', []):
            insights_md += f"- {risk}\n"
        insights_md += "\n"
        
        insights_md += "### Opportunities\n"
        for opp in insights.get('opportunities', []):
            insights_md += f"- {opp}\n"
        insights_md += "\n"
        
        insights_md += "### Future Actions\n"
        for action in insights.get('future_actions', [])[:5]:
            insights_md += f"- {action}\n"
        
        console.print(Panel(
            Markdown(insights_md),
            title="[bold]Strategic Insights[/bold]",
            border_style="green"
        ))

    # Execution Results (if trades were executed)
    if executed:
        execution_results = results.get("execution_results", [])
        if execution_results:
            exec_table = Table(box=box.SIMPLE_HEAD, show_header=True)
            exec_table.add_column("Ticker", style="cyan")
            exec_table.add_column("Action", style="white")
            exec_table.add_column("Status", style="green")
            exec_table.add_column("Details", style="white")
            
            for result in execution_results:
                status = "✓ Success" if result.get('execution_result', {}).get('executed') else "✗ Failed"
                status_color = "green" if "Success" in status else "red"
                
                details = result.get('execution_result', {}).get('message', 'No details')
                if result.get('error'):
                    details = result['error']
                
                exec_table.add_row(
                    result['ticker'],
                    result['action'],
                    f"[{status_color}]{status}[/{status_color}]",
                    details
                )
            
            console.print(Panel(
                exec_table,
                title="[bold]Execution Results[/bold]",
                border_style="blue"
            ))


def _save_portfolio_analysis_results(results: Dict[str, Any], results_dir: Path):
    """Save portfolio analysis results to files."""
    import json
    
    # Save full results as JSON
    with open(results_dir / "portfolio_analysis.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    # Save markdown report
    with open(results_dir / "portfolio_report.md", "w") as f:
        f.write("# Portfolio Analysis Report\n\n")
        f.write(f"**Date:** {results['analysis_date']}\n\n")

        # Triage section
        triage = results.get("triage")
        if triage:
            f.write("## Portfolio Triage\n\n")
            selected = triage.get("selected", [])
            skipped = triage.get("skipped", [])
            f.write(f"**Selected for deep analysis:** {len(selected)} positions\n")
            f.write(f"**Skipped:** {len(skipped)} positions\n\n")
            if selected:
                f.write("| Priority | Ticker | Rationale |\n")
                f.write("|----------|--------|-----------|\n")
                for s in selected:
                    f.write(f"| {s.get('priority', '-')} | {s['ticker']} | {s.get('rationale', '')} |\n")
                f.write("\n")
            if triage.get("research_notes"):
                f.write(f"**Research Notes:** {triage['research_notes']}\n\n")

        # Summary
        summary = results.get('portfolio_summary', {})
        f.write("## Portfolio Summary\n\n")
        f.write(f"- **Total Value:** ${summary.get('account_value', 0):,.2f}\n")
        f.write(f"- **Cash:** ${summary.get('cash', 0):,.2f}\n")
        f.write(f"- **Positions:** {summary.get('positions_count', 0)}\n\n")
        
        # Recommendations
        f.write("## Recommendations\n\n")
        for rec in results.get('recommendations', []):
            triaged_tag = " *(triaged out)*" if rec.get("triaged_out") else ""
            f.write(f"### {rec['ticker']} - {rec['action']}{triaged_tag}\n")

            if rec.get("decision_summary"):
                f.write(f"- **Summary:** {rec['decision_summary']}\n")
            f.write(f"- **Conviction:** {rec['conviction']}/100\n")
            f.write(f"- **Current Position:** {rec['current_position_pct']:.1f}%\n")
            f.write(f"- **Suggested Action:** {rec['suggested_action']}\n")
            f.write(f"- **Rationale:** {rec['rationale']}\n\n")
        
        # Insights
        insights = results.get('strategic_insights', {})
        f.write("## Strategic Insights\n\n")
        f.write(f"**Assessment:** {insights.get('portfolio_assessment', '')}\n\n")
        
        f.write("**Key Risks:**\n")
        for risk in insights.get('key_risks', []):
            f.write(f"- {risk}\n")
        
        f.write("\n**Future Actions:**\n")
        for action in insights.get('future_actions', []):
            f.write(f"- {action}\n")


# Add helper function for question box (if not already present)
def create_question_box(title, prompt, default=None):
    box_content = f"[bold]{title}[/bold]\n"
    box_content += f"[dim]{prompt}[/dim]"
    if default:
        box_content += f"\n[dim]Default: {default}[/dim]"
    return Panel(box_content, border_style="blue", padding=(1, 2))
