"""
Start the journal scheduler daemon with reflection enabled.
"""
import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Add project root to Python path so we can import tradingagents
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from tradingagents.agents.journal import (
    JournalStore,
    JournalScheduler,
    LessonMemory,
    create_reflection_callback,
    JournalExecutionAdvisor,
    JournalExecutionPolicy,
)
from tradingagents.execution import AlpacaExecutor

from rich.console import Console
from rich.table import Table
from rich import box

# Ensure journal directory exists
os.makedirs("journal", exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("journal/scheduler.log", mode="a"),
    ],
)

console = Console()

def display_snapshot_table(store: JournalStore):
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
        title=f"[bold]Active Positions — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]",
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
    table.add_column("→ Stop", justify="right", width=8)
    table.add_column("→ Tgt", justify="right", width=8)
    
    for thesis in active_theses:
        snapshot = store.get_latest_snapshot(thesis.id)
        
        if not snapshot:
            # No snapshot yet, show thesis only
            action_color = {"BUY": "green", "SELL": "red"}.get(thesis.action, "yellow")
            table.add_row(
                thesis.ticker,
                f"[{action_color}]{thesis.action}[/{action_color}]",
                f"${thesis.entry_price:.2f}" if thesis.entry_price else "—",
                "—", "—", "—", "—", "—", "—", "—", "—",
            )
            continue
        
        # Compute colors
        action_color = {"BUY": "green", "SELL": "red"}.get(thesis.action, "yellow")
        pl_pct = snapshot.unrealized_pl_pct or 0
        pl_color = "green" if pl_pct >= 0 else "red"
        
        mae = snapshot.max_adverse_excursion_pct
        mfe = snapshot.max_favorable_excursion_pct
        rel_strength = snapshot.relative_strength
        
        table.add_row(
            thesis.ticker,
            f"[{action_color}]{thesis.action}[/{action_color}]",
            f"${thesis.entry_price:.2f}" if thesis.entry_price else "—",
            f"${snapshot.current_price:.2f}" if snapshot.current_price else "—",
            f"[{pl_color}]{pl_pct:+.2f}%[/{pl_color}]",
            f"{mae:.2f}%" if mae is not None else "—",
            f"[green]{mfe:+.2f}%[/green]" if mfe is not None else "—",
            f"{rel_strength:+.2f}%" if rel_strength is not None else "—",
            str(snapshot.holding_days_elapsed) if snapshot.holding_days_elapsed else "—",
            f"{snapshot.distance_to_stop_pct:.1f}%" if snapshot.distance_to_stop_pct is not None else "—",
            f"{snapshot.distance_to_target1_pct:.1f}%" if snapshot.distance_to_target1_pct is not None else "—",
        )
    
    console.print(table)


def display_action_decisions_table(summary):
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
    table.add_column("Block Reasons", overflow="fold")

    for r in rows:
        gate = "[green]PASS[/green]" if r.get("gates_passed") else "[red]BLOCK[/red]"
        block_reasons = ", ".join(r.get("block_reasons") or [])
        exec_status = str(r.get("execution_status") or "-")
        table.add_row(
            str(r.get("ticker") or "-"),
            str(r.get("decision_type") or "-"),
            str(r.get("reason_code") or "-"),
            f"{float(r.get('confidence') or 0):.1f}",
            gate,
            exec_status,
            block_reasons or "-",
        )

    console.print(table)

def main():
    # Initialize components
    store = JournalStore(db_path="./journal/trade_journal.db")
    
    # Initialize Alpaca executor (optional, for live position monitoring)
    try:
        executor = AlpacaExecutor(paper=True)
        console.print("[green]✓ Alpaca executor initialized[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Running without Alpaca: {e}[/yellow]")
        executor = None
    
    # Initialize lesson memory (ChromaDB)
    memory = LessonMemory(persist_directory="./journal/lessons_chromadb")
    console.print(f"[green]✓ Lesson memory initialized ({memory.count()} lessons)[/green]")
    
    # Create reflection callback
    callback = create_reflection_callback(lesson_memory=memory)
    console.print("[green]✓ Reflection callback created[/green]")

    execution_advisor = JournalExecutionAdvisor.from_env()
    execution_policy = JournalExecutionPolicy.from_env()
    console.print(
        "[green]✓ Execution advisor/policy initialized[/green] "
        f"[dim](enabled={execution_policy.execution_enabled}, dry_run={execution_policy.dry_run})[/dim]"
    )
    
    # Custom tick callback that displays snapshot table
    def on_tick(summary):
        alerts = summary.get("alerts_fired", 0)
        outcomes = summary.get("outcomes_recorded", 0)
        session = summary.get("market_session", "?")
        
        console.print(f"\n[bold cyan]━━━ Tick at {summary.get('timestamp', '')[:19]} (session: {session}) ━━━[/bold cyan]")
        
        # Display snapshot table
        display_snapshot_table(store)
        display_action_decisions_table(summary)
        
        # Show summary
        if alerts > 0:
            console.print(f"[yellow]🚨 {alerts} alert(s) fired[/yellow]")
        if outcomes > 0:
            console.print(f"[green]✓ {outcomes} outcome(s) recorded & reflected[/green]")
        actions_evaluated = summary.get("actions_evaluated", 0)
        actions_recommended = summary.get("actions_recommended", 0)
        actions_executed = summary.get("actions_executed", 0)
        actions_blocked = summary.get("actions_blocked", 0)
        actions_failed = summary.get("actions_failed", 0)
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
    
    # Create scheduler with reflection enabled
    scheduler = JournalScheduler(
        store=store,
        executor=executor,
        lesson_memory=memory,
        execution_advisor=execution_advisor,
        execution_policy=execution_policy,
        market_interval_minutes=15,  # Check every 15 min during market hours
        on_outcome_recorded=callback,  # ← This enables automatic reflection
        on_tick_complete=on_tick,  # ← This displays the snapshot table
    )
    
    console.print("\n[bold green]🚀 Starting journal scheduler daemon...[/bold green]")
    console.print("   - Monitoring active positions")
    console.print("   - Evaluating rules-driven defensive/profit-take actions")
    console.print("   - Recording outcomes when positions close")
    console.print("   - Extracting lessons via LLM reflection")
    console.print("   - Storing lessons in ChromaDB\n")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    
    # Run forever (blocking)
    scheduler.run_forever()

if __name__ == "__main__":
    main()
