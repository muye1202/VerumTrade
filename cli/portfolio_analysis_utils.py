import datetime
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
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


# ==============================================================================
# PortfolioMessageBuffer - Tracks progress for Live GUI
# ==============================================================================

class PortfolioMessageBuffer:
    """
    Tracks portfolio analysis progress for the Live terminal GUI.
    Similar to MessageBuffer in main.py but adapted for multi-stock portfolio analysis.
    """

    # Agent names matching the single-ticker analysis
    AGENT_NAMES = [
        "Catalyst Analyst",
        "Market Analyst",
        "Social Analyst",
        "News Analyst",
        "Fundamentals Analyst",
        "Bull Researcher",
        "Bear Researcher",
        "Research Manager",
        "Trader",
        "Risky Analyst",
        "Neutral Analyst",
        "Safe Analyst",
        "Portfolio Manager",
    ]

    def __init__(self, max_length: int = 100):
        self.messages: deque = deque(maxlen=max_length)
        self.tool_calls: deque = deque(maxlen=max_length)

        # Portfolio-level tracking
        self.triage_status = "pending"  # pending | in_progress | completed
        self.triage_result: Optional[Dict] = None

        # Stock-level tracking
        self.stocks_to_analyze: List[str] = []
        self.current_stock: Optional[str] = None
        self.current_stock_idx: int = 0
        self.total_stocks: int = 0
        self.stock_statuses: Dict[str, str] = {}  # ticker -> pending | in_progress | completed | error
        self.stock_decisions: Dict[str, str] = {}  # ticker -> BUY | SELL | HOLD

        # Current stock's agent statuses (reset for each stock)
        self.agent_status: Dict[str, str] = {name: "pending" for name in self.AGENT_NAMES}
        self.current_agent: Optional[str] = None

        # Reports
        self.current_report: Optional[str] = None
        self.report_sections: Dict[str, Optional[str]] = {
            "catalyst_report": None,
            "market_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
        }

    def add_message(self, message_type: str, content: Any):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name: str, args: Any):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent: str, status: str):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def reset_agent_statuses(self):
        """Reset all agent statuses to pending (called when starting a new stock)."""
        for agent in self.AGENT_NAMES:
            self.agent_status[agent] = "pending"
        self.current_agent = None
        # Clear report sections for new stock
        for section in self.report_sections:
            self.report_sections[section] = None
        self.current_report = None

    def update_report_section(self, section_name: str, content: str):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        """Update current_report with the latest non-None section."""
        section_titles = {
            "market_report": "Market Analysis",
            "catalyst_report": "Catalyst / Event-Risk Analysis",
            "sentiment_report": "Social Sentiment",
            "news_report": "News Analysis",
            "fundamentals_report": "Fundamentals Analysis",
            "investment_plan": "Research Team Decision",
            "trader_investment_plan": "Trading Team Plan",
            "final_trade_decision": "Portfolio Management Decision",
        }

        latest_section = None
        latest_content = None

        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content

        if latest_section and latest_content:
            title = section_titles.get(latest_section, latest_section)
            stock_prefix = f"[{self.current_stock}] " if self.current_stock else ""
            self.current_report = f"### {stock_prefix}{title}\n{latest_content}"


# ==============================================================================
# Portfolio Layout Functions
# ==============================================================================

def create_portfolio_layout() -> Layout:
    """Create the layout for portfolio analysis Live display."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3),
        Layout(name="analysis", ratio=5),
    )
    layout["upper"].split_row(
        Layout(name="stocks", ratio=2),
        Layout(name="agents", ratio=2),
        Layout(name="messages", ratio=3),
    )
    return layout


def update_portfolio_display(
    layout: Layout,
    buffer: PortfolioMessageBuffer,
    spinner_text: Optional[str] = None,
):
    """Update all panels in the portfolio analysis layout."""

    # ---- HEADER ----
    header_text = (
        "[bold green]Portfolio Analysis Mode[/bold green]\n"
        "[dim]Analyzing portfolio positions with AI agents[/dim]"
    )
    layout["header"].update(
        Panel(header_text, border_style="green", padding=(0, 2))
    )

    # ---- STOCKS PROGRESS TABLE ----
    stocks_table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    stocks_table.add_column("#", style="dim", width=3, justify="center")
    stocks_table.add_column("Ticker", style="cyan", width=8)
    stocks_table.add_column("Status", style="white", width=12, justify="center")
    stocks_table.add_column("Decision", style="white", width=8, justify="center")

    # Add triage row if applicable
    if buffer.triage_status != "pending" or buffer.triage_result:
        triage_status_display = {
            "pending": "[yellow]pending[/yellow]",
            "in_progress": Spinner("dots", text="[blue]running[/blue]", style="bold cyan"),
            "completed": "[green]done[/green]",
        }.get(buffer.triage_status, buffer.triage_status)

        stocks_table.add_row("T", "[bold]TRIAGE[/bold]", triage_status_display, "-")
        stocks_table.add_row("─" * 3, "─" * 8, "─" * 12, "─" * 8, style="dim")

    # Add stock rows
    for idx, ticker in enumerate(buffer.stocks_to_analyze):
        status = buffer.stock_statuses.get(ticker, "pending")
        decision = buffer.stock_decisions.get(ticker, "-")

        if status == "in_progress":
            status_display = Spinner("dots", text="[blue]analyzing[/blue]", style="bold cyan")
        elif status == "completed":
            status_display = "[green]done[/green]"
        elif status == "error":
            status_display = "[red]error[/red]"
        else:
            status_display = "[yellow]pending[/yellow]"

        decision_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(decision, "white")
        decision_display = f"[{decision_color}]{decision}[/{decision_color}]" if decision != "-" else "-"

        stocks_table.add_row(str(idx + 1), ticker, status_display, decision_display)

    layout["stocks"].update(
        Panel(stocks_table, title="Stock Progress", border_style="cyan", padding=(0, 1))
    )

    # ---- AGENT STATUS TABLE (for current stock) ----
    agents_table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    agents_table.add_column("Team", style="cyan", width=18)
    agents_table.add_column("Agent", style="green", width=18)
    agents_table.add_column("Status", style="yellow", width=12, justify="center")

    teams = {
        "Analyst": ["Market Analyst", "Social Analyst", "News Analyst", "Fundamentals Analyst"],
        "Research": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading": ["Trader"],
        "Risk Mgmt": ["Risky Analyst", "Neutral Analyst", "Safe Analyst"],
        "Portfolio": ["Portfolio Manager"],
    }

    for team, agents in teams.items():
        for i, agent in enumerate(agents):
            status = buffer.agent_status.get(agent, "pending")

            if status == "in_progress":
                status_cell = Spinner("dots", text="[blue]running[/blue]", style="bold cyan")
            elif status == "completed":
                status_cell = "[green]done[/green]"
            elif status == "error":
                status_cell = "[red]error[/red]"
            else:
                status_cell = "[yellow]pending[/yellow]"

            team_display = team if i == 0 else ""
            agents_table.add_row(team_display, agent, status_cell)

    current_stock_title = f"Agents [{buffer.current_stock}]" if buffer.current_stock else "Agents"
    layout["agents"].update(
        Panel(agents_table, title=current_stock_title, border_style="magenta", padding=(0, 1))
    )

    # ---- MESSAGES TABLE ----
    messages_table = Table(
        show_header=True,
        header_style="bold blue",
        box=box.MINIMAL,
        padding=(0, 1),
        expand=True,
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column("Content", style="white", no_wrap=False, ratio=1)

    # Combine and sort messages and tool calls
    all_messages = []

    for timestamp, tool_name, args in buffer.tool_calls:
        args_str = str(args)[:80] + "..." if len(str(args)) > 80 else str(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {args_str}"))

    for timestamp, msg_type, content in buffer.messages:
        content_str = _extract_content_string(content)
        if len(content_str) > 150:
            content_str = content_str[:147] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    all_messages.sort(key=lambda x: x[0])

    max_messages = 10
    for timestamp, msg_type, content in all_messages[-max_messages:]:
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(messages_table, title="Messages & Tools", border_style="blue", padding=(0, 1))
    )

    # ---- ANALYSIS PANEL ----
    if buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        waiting_text = "[italic]Waiting for analysis report...[/italic]"
        if buffer.triage_status == "in_progress":
            waiting_text = "[italic]Triage agent selecting stocks for analysis...[/italic]"
        layout["analysis"].update(
            Panel(waiting_text, title="Current Report", border_style="green", padding=(1, 2))
        )

    # ---- FOOTER ----
    tool_calls_count = len(buffer.tool_calls)
    llm_calls_count = sum(1 for _, msg_type, _ in buffer.messages if msg_type == "Reasoning")
    completed_stocks = sum(1 for s in buffer.stock_statuses.values() if s in ("completed", "error"))
    total_stocks = len(buffer.stocks_to_analyze)

    stats_text = (
        f"Stocks: {completed_stocks}/{total_stocks} | "
        f"Tool Calls: {tool_calls_count} | "
        f"LLM Calls: {llm_calls_count}"
    )
    if spinner_text:
        stats_text = f"{spinner_text} | {stats_text}"

    layout["footer"].update(Panel(stats_text, border_style="grey50"))


def _extract_content_string(content: Any) -> str:
    """Extract string content from various message formats."""
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            else:
                text_parts.append(str(item))
        return " ".join(text_parts)
    return str(content)


def analyze_portfolio(execute_trades: bool, 
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
    6. (Optional) Execute trades
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

    run_portfolio_analysis_from_selections(selections)


def run_portfolio_analysis_from_selections(selections: dict) -> None:
    """Run portfolio analysis using pre-collected selections (used by main CLI wizard)."""
    if console is None or setup_executor is None:
        raise RuntimeError("Portfolio context not initialized (call init_portfolio_context first).")

    # Create config
    config = DEFAULT_CONFIG.copy()
    requested_depth = int(selections["research_depth"])
    debate_cap = int(config.get("max_debate_rounds_cap", requested_depth))
    risk_cap = int(config.get("max_risk_rounds_cap", requested_depth))
    config["max_debate_rounds"] = min(requested_depth, debate_cap)
    config["max_risk_discuss_rounds"] = min(requested_depth, risk_cap)
    config["max_recur_limit"] = max(100, requested_depth * 120)
    if console is not None and (
        config["max_debate_rounds"] < requested_depth
        or config["max_risk_discuss_rounds"] < requested_depth
    ):
        console.print(
            "[yellow]Context guard: clamped research depth "
            f"(requested={requested_depth}, debate={config['max_debate_rounds']}, "
            f"risk={config['max_risk_discuss_rounds']}).[/yellow]"
        )
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

    analyzer = PortfolioAnalyzer(
        graph=graph,
        executor=executor,
        analysis_date=selections["analysis_date"],
        time_horizon=selections.get("time_horizon"),
    )

    execute_trades = bool(exec_sel.get("enabled", False))

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

    console.print("\n[yellow]Starting portfolio analysis with Live GUI...[/yellow]\n")

    # ------------------------------------------------------------------
    # Create Live GUI components
    # ------------------------------------------------------------------
    buffer = PortfolioMessageBuffer()
    layout = create_portfolio_layout()

    # Fetch portfolio first to populate stock list
    portfolio = executor.get_portfolio_summary()
    all_positions = portfolio.get("positions", [])

    if n_stocks is not None and n_stocks > 0:
        # Triage will select stocks - we'll update the list after triage
        buffer.stocks_to_analyze = [p["symbol"] for p in all_positions]
    else:
        buffer.stocks_to_analyze = [p["symbol"] for p in all_positions]

    for ticker in buffer.stocks_to_analyze:
        buffer.stock_statuses[ticker] = "pending"

    buffer.total_stocks = len(buffer.stocks_to_analyze)

    # ------------------------------------------------------------------
    # Define callbacks for Live GUI updates
    # ------------------------------------------------------------------
    live_context = {"live": None}  # Will hold the Live instance

    def on_triage_start():
        buffer.triage_status = "in_progress"
        buffer.add_message("System", "Starting portfolio triage...")
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_triage_complete(triage_result: Dict[str, Any]):
        buffer.triage_status = "completed"
        buffer.triage_result = triage_result

        # Update stocks to analyze based on triage result
        selected = triage_result.get("selected", [])
        selected_tickers = [s["ticker"].upper() for s in selected]

        # Update buffer with selected stocks
        buffer.stocks_to_analyze = selected_tickers
        buffer.stock_statuses = {t: "pending" for t in selected_tickers}
        buffer.total_stocks = len(selected_tickers)

        buffer.add_message("System", f"Triage complete: {len(selected_tickers)} stocks selected")

        # Show triage notes in current report
        notes = triage_result.get("research_notes", "")
        if notes:
            buffer.current_report = f"### Triage Results\n{notes}"

        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_stock_start(ticker: str, idx: int, total: int):
        buffer.current_stock = ticker
        buffer.current_stock_idx = idx
        buffer.stock_statuses[ticker] = "in_progress"
        buffer.reset_agent_statuses()

        # Set first analyst to in_progress based on selected analysts
        analyst_order = ["catalyst", "market", "social", "news", "fundamentals"]
        selected_analyst_values = [a.value for a in selections.get("analysts", [])]
        for analyst_type in analyst_order:
            if analyst_type in selected_analyst_values:
                buffer.update_agent_status(f"{analyst_type.capitalize()} Analyst", "in_progress")
                break

        buffer.add_message("System", f"Starting analysis for {ticker} ({idx + 1}/{total})")
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_stock_chunk(ticker: str, chunk: Dict[str, Any]):
        """Process streaming chunks to update agent statuses and reports."""
        # Update reports and agent status based on chunk content (same logic as single-ticker)
        _process_chunk_for_buffer(buffer, chunk, selections)
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_stock_complete(ticker: str, analysis: Dict[str, Any]):
        if "error" in analysis:
            buffer.stock_statuses[ticker] = "error"
            buffer.stock_decisions[ticker] = "HOLD"
            buffer.add_message("System", f"Error analyzing {ticker}: {analysis.get('error', 'Unknown error')}")
        else:
            buffer.stock_statuses[ticker] = "completed"
            buffer.stock_decisions[ticker] = analysis.get("decision", "HOLD")
            buffer.add_message(
                "System",
                f"Completed {ticker}: {analysis.get('decision', 'HOLD')}"
            )

        # Mark all agents as completed for this stock
        for agent in buffer.AGENT_NAMES:
            buffer.agent_status[agent] = "completed"

        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_execution_start():
        buffer.add_message("System", "Starting trade execution...")
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_execution_complete(results: List[Dict[str, Any]]):
        executed_count = sum(1 for r in results if r.get("execution_result", {}).get("executed"))
        buffer.add_message("System", f"Execution complete: {executed_count}/{len(results)} trades executed")
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    def on_stock_executed(ticker: str, exec_result: Dict[str, Any]):
        executed = exec_result.get("executed", False)
        status = "✓ executed" if executed else "✗ not executed"
        buffer.add_message("Execution", f"{ticker}: {status}")
        if live_context["live"]:
            update_portfolio_display(layout, buffer)

    # ------------------------------------------------------------------
    # Run analysis with Live GUI
    # ------------------------------------------------------------------
    with Live(layout, refresh_per_second=4, console=console) as live:
        live_context["live"] = live
        update_portfolio_display(layout, buffer)

        results = analyzer.analyze_portfolio(
            execute_trades=execute_trades,
            n_stocks=n_stocks,
            on_triage_start=on_triage_start,
            on_triage_complete=on_triage_complete,
            on_stock_start=on_stock_start,
            on_stock_chunk=on_stock_chunk,
            on_stock_complete=on_stock_complete,
            on_stock_executed=on_stock_executed,
            on_execution_start=on_execution_start,
            on_execution_complete=on_execution_complete,
        )

        # Final update
        buffer.add_message("System", "Portfolio analysis complete!")
        update_portfolio_display(layout, buffer)

    # Display final results (outside of Live context)
    _display_portfolio_analysis_results(results, execute_trades)
    _save_portfolio_analysis_results(results, results_dir)

    console.print(f"\n[green]Results saved to: {results_dir}[/green]\n")


def _process_chunk_for_buffer(
    buffer: PortfolioMessageBuffer,
    chunk: Dict[str, Any],
    selections: dict,
):
    """
    Process a streaming chunk to update buffer's agent statuses and reports.
    This mirrors the logic in analysis_utils.py for single-ticker analysis.
    """
    # Extract messages from chunk
    messages = chunk.get("messages") or []
    if messages:
        # Process the last message
        msg = messages[-1]
        msg_type, content = _msg_type_and_content(msg)
        buffer.add_message(msg_type, content)

        # Extract tool calls
        for tool_name, tool_args in _extract_tool_calls(msg):
            buffer.add_tool_call(tool_name, tool_args)

    # Update reports and agent status based on chunk content
    # Analyst Team Reports
    if "catalyst_report" in chunk and chunk["catalyst_report"]:
        buffer.update_report_section("catalyst_report", chunk["catalyst_report"])
        buffer.update_agent_status("Catalyst Analyst", "completed")
        if "market" in [a.value for a in selections.get("analysts", [])]:
            buffer.update_agent_status("Market Analyst", "in_progress")

    if "market_report" in chunk and chunk["market_report"]:
        buffer.update_report_section("market_report", chunk["market_report"])
        buffer.update_agent_status("Market Analyst", "completed")
        if "social" in [a.value for a in selections.get("analysts", [])]:
            buffer.update_agent_status("Social Analyst", "in_progress")

    if "sentiment_report" in chunk and chunk["sentiment_report"]:
        buffer.update_report_section("sentiment_report", chunk["sentiment_report"])
        buffer.update_agent_status("Social Analyst", "completed")
        if "news" in [a.value for a in selections.get("analysts", [])]:
            buffer.update_agent_status("News Analyst", "in_progress")

    if "news_report" in chunk and chunk["news_report"]:
        buffer.update_report_section("news_report", chunk["news_report"])
        buffer.update_agent_status("News Analyst", "completed")
        if "fundamentals" in [a.value for a in selections.get("analysts", [])]:
            buffer.update_agent_status("Fundamentals Analyst", "in_progress")

    if "fundamentals_report" in chunk and chunk["fundamentals_report"]:
        buffer.update_report_section("fundamentals_report", chunk["fundamentals_report"])
        buffer.update_agent_status("Fundamentals Analyst", "completed")
        # Research team starts
        buffer.update_agent_status("Bull Researcher", "in_progress")
        buffer.update_agent_status("Bear Researcher", "in_progress")
        buffer.update_agent_status("Research Manager", "in_progress")

    # Research Team - Investment Debate State
    if "investment_debate_state" in chunk and chunk["investment_debate_state"]:
        debate_state = chunk["investment_debate_state"]

        if "bull_history" in debate_state and debate_state["bull_history"]:
            buffer.update_agent_status("Bull Researcher", "in_progress")

        if "bear_history" in debate_state and debate_state["bear_history"]:
            buffer.update_agent_status("Bear Researcher", "in_progress")

        if "judge_decision" in debate_state and debate_state["judge_decision"]:
            buffer.update_report_section("investment_plan", debate_state["judge_decision"])
            buffer.update_agent_status("Bull Researcher", "completed")
            buffer.update_agent_status("Bear Researcher", "completed")
            buffer.update_agent_status("Research Manager", "completed")
            buffer.update_agent_status("Trader", "in_progress")

    # Trading Team
    if "trader_investment_plan" in chunk and chunk["trader_investment_plan"]:
        buffer.update_report_section("trader_investment_plan", chunk["trader_investment_plan"])
        buffer.update_agent_status("Trader", "completed")
        buffer.update_agent_status("Risky Analyst", "in_progress")

    # Risk Management Team
    if "risk_debate_state" in chunk and chunk["risk_debate_state"]:
        risk_state = chunk["risk_debate_state"]

        if "current_risky_response" in risk_state and risk_state["current_risky_response"]:
            buffer.update_agent_status("Risky Analyst", "in_progress")

        if "current_safe_response" in risk_state and risk_state["current_safe_response"]:
            buffer.update_agent_status("Safe Analyst", "in_progress")

        if "current_neutral_response" in risk_state and risk_state["current_neutral_response"]:
            buffer.update_agent_status("Neutral Analyst", "in_progress")

        if "judge_decision" in risk_state and risk_state["judge_decision"]:
            buffer.update_report_section("final_trade_decision", risk_state["judge_decision"])
            buffer.update_agent_status("Risky Analyst", "completed")
            buffer.update_agent_status("Safe Analyst", "completed")
            buffer.update_agent_status("Neutral Analyst", "completed")
            buffer.update_agent_status("Portfolio Manager", "completed")


def _msg_type_and_content(msg: Any) -> tuple:
    """Extract message type and content from various message formats."""
    if isinstance(msg, tuple) and len(msg) == 2:
        role, content = msg
        role = str(role).lower()
        if role in ("human", "user"):
            return "User", _extract_content_string(content)
        if role in ("ai", "assistant"):
            return "Reasoning", _extract_content_string(content)
        if role == "tool":
            return "ToolResult", _extract_content_string(content)
        return "System", _extract_content_string(content)

    if isinstance(msg, dict):
        role = str(msg.get("role", "")).lower()
        content = msg.get("content", msg)
        if role in ("assistant", "ai"):
            return "Reasoning", _extract_content_string(content)
        if role in ("user", "human"):
            return "User", _extract_content_string(content)
        if role == "tool":
            return "ToolResult", _extract_content_string(content)
        return "System", _extract_content_string(content)

    msg_kind = getattr(msg, "type", None)
    content = getattr(msg, "content", msg)
    if msg_kind == "ai":
        return "Reasoning", _extract_content_string(content)
    if msg_kind in ("human", "user"):
        return "User", _extract_content_string(content)
    if msg_kind == "tool":
        return "ToolResult", _extract_content_string(content)
    if msg_kind == "system":
        return "System", _extract_content_string(content)
    if hasattr(msg, "content"):
        return "Reasoning", _extract_content_string(content)
    return "System", str(msg)


def _extract_tool_calls(msg: Any) -> List[tuple]:
    """Extract tool calls from a message."""
    calls = []

    # LangChain-style tool_calls
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

    # OpenAI-compatible shape in additional_kwargs
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

    # Dict messages with tool_calls
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

