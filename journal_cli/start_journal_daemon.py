"""
Start the journal scheduler daemon with reflection enabled.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

# Resolve script and project paths.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
JOURNAL_DIR = SCRIPT_DIR / "journal"
EXECUTION_LOG_DIR = SCRIPT_DIR / "execution_logs"

# Add project root to Python path so we can import opentrace.
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from opentrace.agents.journal import (  # noqa: E402
    JournalExecutionAdvisor,
    JournalExecutionPolicy,
    JournalScheduler,
    JournalStore,
    LessonMemory,
    create_reflection_callback,
)
from opentrace.execution import AlpacaExecutor  # noqa: E402

# Ensure runtime directories exist under journal_cli/.
os.makedirs(JOURNAL_DIR, exist_ok=True)
os.makedirs(EXECUTION_LOG_DIR, exist_ok=True)

# Setup logging.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(JOURNAL_DIR / "scheduler.log", mode="a"),
    ],
)

console = Console()


def display_snapshot_table(store: JournalStore) -> None:
    """Display current snapshots for all active positions."""
    active_theses = store.get_active_theses()

    if not active_theses:
        console.print("[dim]No active positions to monitor[/dim]\n")
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.ROUNDED,
        padding=(0, 1),
        title=f"[bold]Active Positions - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]",
    )

    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Action", width=6)
    table.add_column("Entry $", justify="right", width=9)
    table.add_column("Current $", justify="right", width=9)
    table.add_column("P&L %", justify="right", width=9)
    table.add_column("MAE %", justify="right", width=8)
    table.add_column("MFE %", justify="right", width=8)
    table.add_column("vs SPY", justify="right", width=8)
    table.add_column("Days", justify="right", width=5)
    table.add_column("-> Stop", justify="right", width=8)
    table.add_column("-> Tgt", justify="right", width=8)

    for thesis in active_theses:
        snapshot = store.get_latest_snapshot(thesis.id)

        if not snapshot:
            action_color = {"BUY": "green", "SELL": "red"}.get(thesis.action, "yellow")
            table.add_row(
                thesis.ticker,
                f"[{action_color}]{thesis.action}[/{action_color}]",
                f"${thesis.entry_price:.2f}" if thesis.entry_price else "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
            )
            continue

        action_color = {"BUY": "green", "SELL": "red"}.get(thesis.action, "yellow")
        pl_pct = snapshot.unrealized_pl_pct or 0
        pl_color = "green" if pl_pct >= 0 else "red"

        mae = snapshot.max_adverse_excursion_pct
        mfe = snapshot.max_favorable_excursion_pct
        rel_strength = snapshot.relative_strength

        table.add_row(
            thesis.ticker,
            f"[{action_color}]{thesis.action}[/{action_color}]",
            f"${thesis.entry_price:.2f}" if thesis.entry_price else "-",
            f"${snapshot.current_price:.2f}" if snapshot.current_price else "-",
            f"[{pl_color}]{pl_pct:+.2f}%[/{pl_color}]",
            f"{mae:.2f}%" if mae is not None else "-",
            f"[green]{mfe:+.2f}%[/green]" if mfe is not None else "-",
            f"{rel_strength:+.2f}%" if rel_strength is not None else "-",
            str(snapshot.holding_days_elapsed) if snapshot.holding_days_elapsed is not None else "-",
            f"{snapshot.distance_to_stop_pct:.1f}%" if snapshot.distance_to_stop_pct is not None else "-",
            f"{snapshot.distance_to_target1_pct:.1f}%" if snapshot.distance_to_target1_pct is not None else "-",
        )

    console.print(table)


def display_action_decisions_table(summary: dict) -> None:
    """Display actionable decision rows for this tick."""
    rows = summary.get("action_decision_rows") or []
    if not rows:
        return

    table = Table(
        show_header=True,
        header_style="bold yellow",
        box=box.ROUNDED,
        padding=(0, 1),
        title="[bold]Action Decisions (Actionable)[/bold]",
    )
    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Decision", width=20)
    table.add_column("Reason", width=24)
    table.add_column("Conf", justify="right", width=6)
    table.add_column("Gate", width=8)
    table.add_column("Exec", width=10)
    table.add_column("Branch", width=16)
    table.add_column("Event Src", width=12)
    table.add_column("Block Reasons", overflow="fold")

    for row in rows:
        gate = "[green]PASS[/green]" if row.get("gates_passed") else "[red]BLOCK[/red]"
        block_reasons = ", ".join(row.get("block_reasons") or [])
        exec_status = str(row.get("execution_status") or "-")
        branch = str(row.get("matched_branch_id") or "-")
        event_src = str(row.get("event_confirmation_source") or "-")
        table.add_row(
            str(row.get("ticker") or "-"),
            str(row.get("decision_type") or "-"),
            str(row.get("reason_code") or "-"),
            f"{float(row.get('confidence') or 0):.1f}",
            gate,
            exec_status,
            branch,
            event_src,
            block_reasons or "-",
        )

    console.print(table)


def main() -> None:
    store = JournalStore(db_path=str(JOURNAL_DIR / "trade_journal.db"))

    try:
        executor = AlpacaExecutor(paper=True, log_dir=str(EXECUTION_LOG_DIR))
        console.print("[green]Alpaca executor initialized[/green]")
    except Exception as e:
        console.print(f"[yellow]Running without Alpaca: {e}[/yellow]")
        executor = None

    memory = LessonMemory(persist_directory=str(JOURNAL_DIR / "lessons_chromadb"))
    console.print(f"[green]Lesson memory initialized ({memory.count()} lessons)[/green]")

    callback = create_reflection_callback(lesson_memory=memory)
    console.print("[green]Reflection callback created[/green]")

    execution_advisor = JournalExecutionAdvisor.from_env()
    execution_policy = JournalExecutionPolicy.from_env()
    console.print(
        "[green]Execution advisor/policy initialized[/green] "
        f"[dim](enabled={execution_policy.execution_enabled}, dry_run={execution_policy.dry_run})[/dim]"
    )

    def on_tick(summary: dict) -> None:
        alerts = summary.get("alerts_fired", 0)
        outcomes = summary.get("outcomes_recorded", 0)
        session = summary.get("market_session", "?")

        console.print(
            f"\n[bold cyan]--- Tick at {summary.get('timestamp', '')[:19]} "
            f"(session: {session}) ---[/bold cyan]"
        )

        display_snapshot_table(store)
        display_action_decisions_table(summary)

        if alerts > 0:
            console.print(f"[yellow]{alerts} alert(s) fired[/yellow]")
        if outcomes > 0:
            console.print(f"[green]{outcomes} outcome(s) recorded and reflected[/green]")

        actions_evaluated = summary.get("actions_evaluated", 0)
        actions_recommended = summary.get("actions_recommended", 0)
        actions_executed = summary.get("actions_executed", 0)
        actions_blocked = summary.get("actions_blocked", 0)
        actions_failed = summary.get("actions_failed", 0)

        pull_created = summary.get("portfolio_pull_created", 0)
        pull_created_tickers = summary.get("portfolio_pull_created_tickers", []) or []
        if pull_created > 0:
            console.print(
                "[green]Auto portfolio pull:[/green] "
                f"created={pull_created} ({', '.join(str(t) for t in pull_created_tickers)})"
            )

        if actions_evaluated > 0:
            console.print(
                "[cyan]Action pipeline:[/cyan] "
                f"evaluated={actions_evaluated}, "
                f"recommended={actions_recommended}, "
                f"executed={actions_executed}, "
                f"blocked={actions_blocked}, "
                f"failed={actions_failed}"
            )

        console.print()

    # â”€â”€ Optional Tier 2 LLM eval â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Set JOURNAL_LLM_PROVIDER + JOURNAL_LLM_MODEL in .env to enable.
    # Example:
    #   JOURNAL_LLM_PROVIDER=openai
    #   JOURNAL_LLM_MODEL=gpt-4o-mini
    _journal_provider = os.getenv("JOURNAL_LLM_PROVIDER", "").strip()
    _journal_model    = os.getenv("JOURNAL_LLM_MODEL", "").strip()
    _journal_url      = os.getenv("JOURNAL_BACKEND_URL", "").strip()
    _llm_config: dict | None = None
    if _journal_provider or _journal_model:
        from opentrace.default_config import DEFAULT_CONFIG
        _llm_config = dict(DEFAULT_CONFIG)
        if _journal_provider:
            _llm_config["llm_provider"] = _journal_provider
        if _journal_model:
            _llm_config["quick_think_llm"] = _journal_model
        if _journal_url:
            _llm_config["backend_url"] = _journal_url
        console.print(
            f"[dim]Journal LLM (Tier 2): provider={_llm_config['llm_provider']}, "
            f"model={_llm_config['quick_think_llm']}[/dim]"
        )
    else:
        console.print("[dim]Journal LLM (Tier 2): disabled (set JOURNAL_LLM_PROVIDER + JOURNAL_LLM_MODEL to enable)[/dim]")

    scheduler = JournalScheduler(
        store=store,
        executor=executor,
        lesson_memory=memory,
        execution_advisor=execution_advisor,
        execution_policy=execution_policy,
        llm_config=_llm_config,
        market_interval_minutes=15,
        on_outcome_recorded=callback,
        on_tick_complete=on_tick,
    )

    console.print("\n[bold green]Starting journal scheduler daemon...[/bold green]")
    console.print("   - Monitoring active positions")
    console.print("   - Evaluating rules-driven defensive/profit-take actions")
    console.print("   - Recording outcomes when positions close")
    console.print("   - Extracting lessons via LLM reflection")
    console.print("   - Storing lessons in ChromaDB")
    console.print(f"   - Journal data dir: {JOURNAL_DIR}")
    console.print(f"   - Execution logs dir: {EXECUTION_LOG_DIR}\n")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    scheduler.run_forever()


if __name__ == "__main__":
    main()
