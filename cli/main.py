import os
from typing import Optional
import datetime
import json
import re
import typer
from tradingagents.utils.market_session import now_et
import questionary
from pathlib import Path
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from rich.panel import Panel
from rich.spinner import Spinner
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
from rich import box
from rich.align import Align

from tradingagents.graph.portfolio_analyzer import PortfolioAnalyzer
from tradingagents.execution.portfolio_context import fetch_portfolio_context
from tradingagents.graph.batch_analysis import BatchAnalyzer
from tradingagents.execution import AlpacaExecutor
from cli.utils import *
from cli.analysis_utils import init_analysis_context, run_analysis
from cli.portfolio_analysis_utils import init_portfolio_context, analyze_portfolio as _analyze_portfolio_impl
from cli.discovery_utils import init_discovery_context, run_discovery_flow
from cli.journal_cli import journal_app

console = Console()

app = typer.Typer(
    name="Boolean Trader",
    help="Boolean Trader CLI: Team of Agentic Traders",
    add_completion=True,  # Enable shell completion
)

# Register journal subcommand
app.add_typer(journal_app, name="journal")


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {
            # Analyst Team
            "Catalyst Analyst": "pending",
            "Market Analyst": "pending",
            "Social Analyst": "pending",
            "News Analyst": "pending",
            "Fundamentals Analyst": "pending",
            # Research Team
            "Bull Researcher": "pending",
            "Bear Researcher": "pending",
            "Research Manager": "pending",
            # Trading Team
            "Trader": "pending",
            # Risk Management Team
            "Risky Analyst": "pending",
            "Neutral Analyst": "pending",
            "Safe Analyst": "pending",
            # Portfolio Management Team
            "Portfolio Manager": "pending",
        }
        self.current_agent = None
        self.report_sections = {
            "market_report": None,
            "catalyst_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
            "execution_report": None,
        }

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "catalyst_report": "Catalyst / Event-Risk Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
                "execution_report": "Execution",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports
        if any(
            self.report_sections[section]
            for section in [
                "market_report",
                "catalyst_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
            ]
        ):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections["market_report"]:
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections["catalyst_report"]:
                report_parts.append(
                    f"### Catalyst / Event-Risk Analysis\n{self.report_sections['catalyst_report']}"
                )
            if self.report_sections["sentiment_report"]:
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections["news_report"]:
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections["fundamentals_report"]:
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections["investment_plan"]:
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections["trader_investment_plan"]:
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections["final_trade_decision"]:
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        # Execution
        if self.report_sections.get("execution_report"):
            report_parts.append("## Execution")
            report_parts.append(f"{self.report_sections['execution_report']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()

def select_execution_settings() -> dict:
    """Select optional execution settings shown on the main (pre-run) page."""
    choice = questionary.select(
        "Select [Execution Mode]:",
        choices=[
            questionary.Choice("Analysis only (no trade execution)", value="none"),
            questionary.Choice("Alpaca paper trading (execute BUY/SELL signals)", value="alpaca_paper"),
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

    if choice is None:
        console.print("\n[red]No execution mode selected. Exiting...[/red]")
        raise typer.Exit(code=1)

    if choice == "none":
        return {"enabled": False}

    return {
        "enabled": True,
        "provider": "alpaca",
        "paper": True,
        # Agent-driven sizing (QUANTITY or POSITION_SIZE_PCT) is preferred.
        # This config value is a fallback sizing percentage used only if the agent
        # does not provide a usable size.
        "position_size_pct": 0.10,
    }


def setup_executor(execution_settings: dict, log_dir: Optional[Path] = None) -> Optional[AlpacaExecutor]:
    """Setup optional trade executor based on pre-run selections."""
    if not execution_settings or not execution_settings.get("enabled"):
        return None

    if execution_settings.get("provider") != "alpaca":
        return None

    console.print("\n[yellow]Setting up Alpaca executor...[/yellow]")

    has_creds = (
        (os.getenv("APCA_API_KEY_ID") and os.getenv("APCA_API_SECRET_KEY"))
        or (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))
    )
    if not has_creds:
        console.print("[yellow]WARNING: Alpaca credentials not found; continuing without execution.[/yellow]")
        console.print("Set APCA_API_KEY_ID + APCA_API_SECRET_KEY (or ALPACA_API_KEY + ALPACA_SECRET_KEY).")
        return None

    try:
        executor = AlpacaExecutor(
            paper=bool(execution_settings.get("paper", True)),
            position_size_pct=float(execution_settings.get("position_size_pct", 0.10)),
            log_dir=str(log_dir) if log_dir else None,
        )

        summary = executor.get_portfolio_summary()
        if getattr(executor, "trading_base_url", None):
            console.print(f"[green]Alpaca Trading URL: {executor.trading_base_url}[/green]")
        if getattr(executor, "data_base_url", None):
            console.print(f"[green]Alpaca Data URL: {executor.data_base_url}[/green]")
        console.print(f"\n[green]Portfolio Value: ${summary['account_value']:,.2f}[/green]")
        console.print(f"[green]Cash Available: ${summary['cash']:,.2f}[/green]")
        console.print(f"[green]Open Positions: {summary['positions_count']}[/green]\n")

        return executor
    except Exception as e:
        console.print(f"[yellow]WARNING: Failed to set up Alpaca executor ({e}); continuing without execution.[/yellow]")
        return None


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def update_display(layout, spinner_text=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to Boolean Trader CLI[/bold green]\n"
            "[dim]© [muye1202](https://github.com/muye1202/Multi-LLM-Agent-Trader)[/dim]",
            title="Welcome to Boolean Trader",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team
    teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Risky Analyst", "Neutral Analyst", "Safe Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status[first_agent]
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status[agent]
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        # Truncate tool call args if too long
        if isinstance(args, str) and len(args) > 100:
            args = args[:97] + "..."
        all_messages.append((timestamp, "Tool", f"{tool_name}: {args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        # Convert content to string if it's not already
        content_str = content
        if isinstance(content, list):
            # Handle list of content blocks (Anthropic format)
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
            content_str = ' '.join(text_parts)
        elif isinstance(content, dict):
            if isinstance(content.get("text"), str):
                content_str = content["text"]
            elif isinstance(content.get("content"), str):
                content_str = content["content"]
            else:
                try:
                    content_str = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content_str = str(content)
        elif not isinstance(content_str, str):
            content_str = str(content)
            
        # Truncate message content if too long
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp
    all_messages.sort(key=lambda x: x[0])

    # Calculate how many messages we can show based on available space
    # Start with a reasonable number and adjust based on content length
    max_messages = 12  # Increased from 8 to better fill the space

    # Get the last N messages that will fit in the panel
    recent_messages = all_messages[-max_messages:]

    # Add messages to table
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    if spinner_text:
        messages_table.add_row("", "Spinner", spinner_text)

    # Add a footer to indicate if messages were truncated
    if len(all_messages) > max_messages:
        messages_table.footer = (
            f"[dim]Showing last {max_messages} of {len(all_messages)} messages[/dim]"
        )

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    tool_calls_count = len(message_buffer.tool_calls)
    llm_calls_count = sum(
        1 for _, msg_type, _ in message_buffer.messages if msg_type == "Reasoning"
    )
    reports_count = sum(
        1 for content in message_buffer.report_sections.values() if content is not None
    )

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(
        f"Tool Calls: {tool_calls_count} | LLM Calls: {llm_calls_count} | Generated Reports: {reports_count}"
    )

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open("./cli/static/welcome.txt", "r", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()  # Add a blank line after the welcome box

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    default_date = now_et().strftime("%Y-%m-%d")

    # Step 1: Analysis mode
    console.print(
        create_question_box(
            "Step 1: Analysis Mode",
            "Choose between single-ticker analysis, portfolio analysis, or AI stock discovery",
            "Single ticker",
        )
    )
    analysis_mode = questionary.select(
        "Select [Analysis Mode]:",
        choices=[
            questionary.Choice("Single ticker (one or more symbols)", value="single"),
            questionary.Choice("Portfolio (analyze all positions)", value="portfolio"),
            questionary.Choice("Stock Discovery (AI finds promising stocks)", value="discovery"),
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

    if analysis_mode is None:
        console.print("\n[red]No analysis mode selected. Exiting...[/red]")
        raise typer.Exit(code=1)

    selected_ticker = None
    analysis_date = default_date

    # Discovery mode: only needs LLM config, date is today
    if analysis_mode == "discovery":
        console.print(
            create_question_box(
                "Step 2: Analysis Date",
                "Enter the date for stock discovery (defaults to today)",
                default_date,
            )
        )
        analysis_date = get_analysis_date()

        # --- NEW: Fresh run vs Resume ---
        console.print(
            create_question_box(
                "Step 3: Discovery Source",
                "Run a fresh discovery pipeline, or resume deep analysis from a previously saved ticker list?",
                "Fresh run",
            )
        )
        discovery_source = questionary.select(
            "Select [Discovery Source]:",
            choices=[
                questionary.Choice(
                    "Fresh run — run the full discovery pipeline",
                    value="fresh",
                ),
                questionary.Choice(
                    "Resume — load ticker list from a previous discovery report and run deep analysis",
                    value="resume",
                ),
            ],
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=questionary.Style(
                [
                    ("selected", "fg:cyan noinherit"),
                    ("highlighted", "fg:cyan noinherit"),
                    ("pointer", "fg:cyan noinherit"),
                ]
            ),
        ).ask()
        if discovery_source is None:
            console.print("\n[red]No discovery source selected. Exiting...[/red]")
            raise typer.Exit(code=1)

        if discovery_source == "resume":
            # Resume path: skip track/catalyst steps, go straight to LLM config
            console.print(create_question_box("Step 4: LLM Provider", "Select which service to talk to"))
            selected_llm_provider, backend_url = select_llm_provider()

            console.print(create_question_box("Step 5: Deep Thinking Agent", "Select the model for deep analysis"))
            selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

            console.print(
                create_question_box(
                    "Step 6: Execution",
                    "Optionally execute BUY/SELL signals after deep analysis.",
                    "Analysis only",
                )
            )
            execution_settings = select_execution_settings()

            return {
                "analysis_mode": "discovery",
                "discovery_mode_variant": "resume",
                "ticker": None,
                "analysis_date": analysis_date,
                "discovery_track": None,
                "discovery_catalyst_mode": None,
                "analysts": [],
                "research_depth": 1,
                "llm_provider": selected_llm_provider.lower(),
                "backend_url": backend_url,
                "shallow_thinker": selected_deep_thinker,
                "deep_thinker": selected_deep_thinker,
                "execution": execution_settings,
                "n_stocks": None,
            }

        # Fresh run path (original flow)
        console.print(
            create_question_box(
                "Step 4: Discovery Track",
                "Choose the screener track for discovery",
                "enricher",
            )
        )
        discovery_track = questionary.select(
            "Select [Discovery Track]:",
            choices=[
                questionary.Choice(
                    "Enricher \u2014 Stage 1 multi-factor enrichment + Stage 2 scoring (swing trade, multi-day)",
                    value="enricher",
                ),
                questionary.Choice(
                    "Anomaly Scan \u2014 Short-term momentum anomaly scans (intraday/next-day setups)",
                    value="anomaly_scan",
                ),
                questionary.Choice(
                    "Dual-Track \u2014 Run both Enricher + Anomaly Scan; merge & score top 8\u201312 with convergence bonus",
                    value="dual_track",
                ),
            ],
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=questionary.Style(
                [
                    ("selected", "fg:cyan noinherit"),
                    ("highlighted", "fg:cyan noinherit"),
                    ("pointer", "fg:cyan noinherit"),
                ]
            ),
        ).ask()
        if discovery_track is None:
            console.print("\n[red]No discovery track selected. Exiting...[/red]")
            raise typer.Exit(code=1)

        console.print(
            create_question_box(
                "Step 5: Stage 0 Catalyst Filter Mode",
                "Select the Stage 0 catalyst filter mode used by discovery prefiltering",
                "daily_calendar",
            )
        )
        discovery_catalyst_mode = questionary.select(
            "Select [Stage 0 Catalyst Filter Mode]:",
            choices=[
                questionary.Choice("daily_calendar (recommended default)", value="daily_calendar"),
                questionary.Choice("per_ticker_calendar", value="per_ticker_calendar"),
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
        if discovery_catalyst_mode is None:
            console.print("\n[red]No catalyst filter mode selected. Exiting...[/red]")
            raise typer.Exit(code=1)

        # Skip to LLM provider selection for discovery mode
        console.print(create_question_box("Step 6: LLM Provider", "Select which service to talk to"))
        selected_llm_provider, backend_url = select_llm_provider()

        console.print(create_question_box("Step 7: Deep Thinking Agent", "Select the model for stock discovery"))
        selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

        # Optional execution settings
        console.print(
            create_question_box(
                "Step 8: Execution",
                "Optionally execute BUY/SELL signals after deep analysis. Sizing is agent-driven (QUANTITY or POSITION_SIZE_PCT).",
                "Analysis only",
            )
        )
        execution_settings = select_execution_settings()

        return {
            "analysis_mode": "discovery",
            "discovery_mode_variant": "fresh",
            "ticker": None,
            "analysis_date": analysis_date,
            "discovery_track": discovery_track,
            "discovery_catalyst_mode": discovery_catalyst_mode,
            "analysts": [],
            "research_depth": 1,
            "llm_provider": selected_llm_provider.lower(),
            "backend_url": backend_url,
            "shallow_thinker": selected_deep_thinker,  # Use same for simplicity
            "deep_thinker": selected_deep_thinker,
            "execution": execution_settings,
            "n_stocks": None,
        }

    # Single ticker path: ask for ticker + date
    if analysis_mode == "single":
        console.print(
            create_question_box(
                "Step 2: Ticker Symbols",
                "Enter ticker symbols to analyze (one at a time). Confirm each ticker, then choose Submit.",
                "SPY",
            )
        )
        selected_tickers = get_tickers()
        selected_ticker = selected_tickers[0]

        console.print(
            create_question_box(
                "Step 3: Analysis Date",
                "Enter the analysis date (YYYY-MM-DD)",
                default_date,
            )
        )
        analysis_date = get_analysis_date()

        console.print(
            create_question_box(
                "Step 4: Skip Completed Analysts",
                "Automatically skip analyst steps (Market, Social, News, Fundamentals) if their reports already exist "
                "for the selected date and ticker.",
                "Yes",
            )
        )
        skip_completed = questionary.select(
            "Skip Completed Analysts?",
            choices=[
                questionary.Choice("Yes — Skip analysts with existing reports", value=True),
                questionary.Choice("No  — Rerun all analysts", value=False),
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
        if skip_completed is None:
            console.print("\n[red]No skip selection. Exiting...[/red]")
            raise typer.Exit(code=1)
        skip_completed_analysts = skip_completed
    else:
        skip_completed_analysts = False

    # Holding period (single + portfolio)
    horizon_step = 5 if analysis_mode == "single" else 2
    console.print(
        create_question_box(
            f"Step {horizon_step}: Holding Period",
            "Select the target holding period for trade ideas (affects all agent prompts)",
            "1–2 months",
        )
    )
    selected_time_horizon = select_time_horizon()

    # Common params (single + portfolio)
    analysts_step = horizon_step + 1
    console.print(
        create_question_box(
            f"Step {analysts_step}: Analysts Team",
            "Select your LLM analyst agents for the analysis",
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    depth_step = analysts_step + 1
    console.print(create_question_box(f"Step {depth_step}: Research Depth", "Select your research depth level"))
    selected_research_depth = select_research_depth()

    provider_step = depth_step + 1
    console.print(create_question_box(f"Step {provider_step}: LLM Provider", "Select which service to talk to"))
    selected_llm_provider, backend_url = select_llm_provider()
    
    thinking_step = provider_step + 1
    console.print(create_question_box(f"Step {thinking_step}: Thinking Agents", "Select your thinking agents for analysis"))
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    exec_step = thinking_step + 1
    console.print(
        create_question_box(
            f"Step {exec_step}: Execution",
            "Optionally execute BUY/SELL signals (paper trading). Sizing is agent-driven (QUANTITY or POSITION_SIZE_PCT).",
            "Analysis only",
        )
    )
    execution_settings = select_execution_settings()

    # --- Portfolio triage: pick N stocks ---
    n_stocks = None
    if analysis_mode == "portfolio":
        triage_step = exec_step + 1
        console.print(
            create_question_box(
                f"Step {triage_step}: Portfolio Triage",
                "Let the deep-think AI pre-screen positions to select the "
                "N most analysis-worthy stocks.  Saves time & cost.",
                "3",
            )
        )
        triage_choice = questionary.select(
            "Enable portfolio triage?",
            choices=[
                questionary.Choice("Yes — AI picks the most important stocks", value="yes"),
                questionary.Choice("No  — analyze every position", value="no"),
            ],
            instruction="\\n- Use arrow keys to navigate\\n- Press Enter to select",
            style=questionary.Style(
                [
                    ("selected", "fg:yellow noinherit"),
                    ("highlighted", "fg:yellow noinherit"),
                    ("pointer", "fg:yellow noinherit"),
                ]
            ),
        ).ask()
        if triage_choice is None:
            console.print("\\n[red]No triage selection. Exiting...[/red]")
            raise typer.Exit(code=1)
        if triage_choice == "yes":
            n_input = questionary.text(
                "How many stocks should the AI select for deep analysis?",
                default="3",
                validate=lambda x: (x.strip().isdigit() and int(x.strip()) > 0)
                    or "Enter a positive integer.",
            ).ask()
            n_stocks = int(n_input) if n_input else None

    return {
        "analysis_mode": analysis_mode,
        "ticker": selected_ticker,
        # Multi-ticker single-stock analysis: loop over these sequentially.
        # For compatibility, `ticker` remains the first ticker.
        **({"tickers": selected_tickers} if analysis_mode == "single" else {}),
        "analysis_date": analysis_date,
        "time_horizon": selected_time_horizon,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "execution": execution_settings,
        "n_stocks": n_stocks,
        "skip_completed_analysts": skip_completed_analysts,
    }


def get_ticker():
    """Get ticker symbol from user input."""
    return typer.prompt("", default="SPY")


_TICKER_RE = re.compile(r"^[A-Z0-9.\-]+$")


def get_tickers() -> list[str]:
    """
    Collect one or more ticker symbols.

    Workflow:
    - Enter a ticker
    - Confirm it
    - Choose: Enter more ticker / Submit

    Returns an ordered list of unique tickers (uppercased).
    """
    tickers: list[str] = []

    def _validate_ticker_text(val: str) -> bool | str:
        s = (val or "").strip().upper()
        if not s:
            return "Please enter a ticker symbol."
        if not _TICKER_RE.match(s):
            return "Use only letters/numbers and optional . or - (e.g., BRK.B, RDS-A)."
        return True

    select_style = questionary.Style(
        [
            ("selected", "fg:yellow noinherit"),
            ("highlighted", "fg:yellow noinherit"),
            ("pointer", "fg:yellow noinherit"),
        ]
    )

    while True:
        entered = questionary.text(
            "",
            default=("SPY" if not tickers else ""),
            validate=_validate_ticker_text,
        ).ask()

        if entered is None:
            console.print("\n[red]Ticker entry cancelled. Exiting...[/red]")
            raise typer.Exit(code=1)

        ticker = entered.strip().upper()

        confirm = questionary.select(
            f"Confirm ticker '{ticker}'?",
            choices=[
                questionary.Choice("Confirm", value="confirm"),
                questionary.Choice("Re-enter", value="reenter"),
            ],
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=select_style,
        ).ask()

        if confirm is None:
            console.print("\n[red]Ticker confirmation cancelled. Exiting...[/red]")
            raise typer.Exit(code=1)

        if confirm == "reenter":
            continue

        if ticker in tickers:
            console.print(f"[yellow]Duplicate ticker ignored:[/yellow] {ticker}")
        else:
            tickers.append(ticker)

        next_action = questionary.select(
            "Next:",
            choices=[
                questionary.Choice("Enter more ticker", value="more"),
                questionary.Choice("Submit", value="submit"),
            ],
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=select_style,
        ).ask()

        if next_action is None:
            console.print("\n[red]No selection made. Exiting...[/red]")
            raise typer.Exit(code=1)

        if next_action == "submit":
            break

    if not tickers:
        console.print("\n[red]No tickers submitted. Exiting...[/red]")
        raise typer.Exit(code=1)

    return tickers


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=now_et().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > now_et().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def display_complete_report(final_state):
    """Display the complete analysis report with team-based panels."""
    console.print("\n[bold green]Complete Analysis Report[/bold green]\n")

    # I. Analyst Team Reports
    analyst_reports = []

    # Market Analyst Report
    if final_state.get("catalyst_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["catalyst_report"]),
                title="Catalyst / Event-Risk Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Market Analyst Report
    if final_state.get("market_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["market_report"]),
                title="Market Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Social Analyst Report
    if final_state.get("sentiment_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["sentiment_report"]),
                title="Social Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # News Analyst Report
    if final_state.get("news_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["news_report"]),
                title="News Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Fundamentals Analyst Report
    if final_state.get("fundamentals_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["fundamentals_report"]),
                title="Fundamentals Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    if analyst_reports:
        console.print(
            Panel(
                Columns(analyst_reports, equal=True, expand=True),
                title="I. Analyst Team Reports",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        research_reports = []
        debate_state = final_state["investment_debate_state"]

        # Bull Researcher Analysis
        if debate_state.get("bull_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bull_history"]),
                    title="Bull Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Bear Researcher Analysis
        if debate_state.get("bear_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bear_history"]),
                    title="Bear Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Research Manager Decision
        if debate_state.get("judge_decision"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["judge_decision"]),
                    title="Research Manager",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if research_reports:
            console.print(
                Panel(
                    Columns(research_reports, equal=True, expand=True),
                    title="II. Research Team Decision",
                    border_style="magenta",
                    padding=(1, 2),
                )
            )

    # III. Trading Team Reports
    if final_state.get("trader_investment_plan"):
        console.print(
            Panel(
                Panel(
                    Markdown(final_state["trader_investment_plan"]),
                    title="Trader",
                    border_style="blue",
                    padding=(1, 2),
                ),
                title="III. Trading Team Plan",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    # IV. Risk Management Team Reports
    if final_state.get("risk_debate_state"):
        risk_reports = []
        risk_state = final_state["risk_debate_state"]

        # Aggressive (Risky) Analyst Analysis
        if risk_state.get("risky_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["risky_history"]),
                    title="Aggressive Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Conservative (Safe) Analyst Analysis
        if risk_state.get("safe_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["safe_history"]),
                    title="Conservative Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Neutral Analyst Analysis
        if risk_state.get("neutral_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["neutral_history"]),
                    title="Neutral Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if risk_reports:
            console.print(
                Panel(
                    Columns(risk_reports, equal=True, expand=True),
                    title="IV. Risk Management Team Decision",
                    border_style="red",
                    padding=(1, 2),
                )
            )

        # V. Portfolio Manager Decision
        if risk_state.get("judge_decision"):
            console.print(
                Panel(
                    Panel(
                        Markdown(risk_state["judge_decision"]),
                        title="Portfolio Manager",
                        border_style="blue",
                        padding=(1, 2),
                    ),
                    title="V. Portfolio Manager Decision",
                    border_style="green",
                    padding=(1, 2),
                )
            )


@app.command()
def analyze():
    init_analysis_context(
        console=console,
        message_buffer=message_buffer,
        get_user_selections=get_user_selections,
        setup_executor=setup_executor,
        create_layout=create_layout,
        update_display=update_display,
        display_complete_report=display_complete_report,
    )
    run_analysis()


@app.command()
def analyze_portfolio(
    execute_trades: bool = typer.Option(
        False,
        "--execute/--no-execute",
        help="Execute recommended trades (default: analysis only)",
    ),
    analysis_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Analysis date (YYYY-MM-DD), defaults to today",
    ),
    n_stocks: Optional[int] = typer.Option(
        None,
        "--n-stocks",
        help="Triage: AI selects this many positions for deep analysis (default: all)",
    ),
):
    init_portfolio_context(console=console, setup_executor=setup_executor)
    _analyze_portfolio_impl(execute_trades, analysis_date, n_stocks=n_stocks)


if __name__ == "__main__":
    app()
