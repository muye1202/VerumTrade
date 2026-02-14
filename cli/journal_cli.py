"""
CLI commands for the Trade Journal system.

Register with the main CLI by importing and adding to the typer app:
    from cli.journal_cli import journal_app
    app.add_typer(journal_app, name="journal")

Then run with:
    python -m cli.main journal status
    python -m cli.main journal alerts
    python -m cli.main journal theses
    python -m cli.main journal check-now
    python -m cli.main journal daemon
    python -m cli.main journal outcomes
    python -m cli.main journal performance
"""

from __future__ import annotations

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tradingagents.agents.journal.store import JournalStore
from tradingagents.agents.journal.models import AlertType, ThesisStatus

console = Console()
journal_app = typer.Typer(
    name="journal",
    help="Trade Journal — track, monitor, and review your trades.",
    no_args_is_help=True,
)

# Default DB path (configurable via env var)
_DEFAULT_DB = os.getenv("JOURNAL_DB_PATH", "./journal/trade_journal.db")


def _get_store(db_path: Optional[str] = None) -> JournalStore:
    return JournalStore(db_path=db_path or _DEFAULT_DB)


def _get_executor():
    """Try to initialize an Alpaca executor for live data."""
    try:
        from tradingagents.execution import AlpacaExecutor

        api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            return AlpacaExecutor(paper=True)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------


@journal_app.command()
def status(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Show journal status: active theses, alerts, scheduler info."""
    store = _get_store(db_path)

    active = store.get_active_theses()
    unacked_alerts = store.get_alerts(unacknowledged_only=True, limit=100)
    perf = store.get_performance_summary()
    recent_decisions = store.get_action_decisions(limit=500)
    recent_execs = store.get_action_executions(limit=500)
    now = datetime.utcnow()
    decisions_24h = [
        d for d in recent_decisions
        if _ts_within_hours(d.created_at, now=now, hours=24)
    ]
    execs_24h = [
        e for e in recent_execs
        if _ts_within_hours(e.created_at, now=now, hours=24)
    ]
    blocked_24h = sum(1 for d in decisions_24h if not d.gates_passed)
    executed_24h = sum(1 for e in execs_24h if e.status in {"dry_run", "submitted"})
    failed_24h = sum(1 for e in execs_24h if e.status in {"failed", "rejected"})

    # Status panel
    status_lines = [
        f"[bold]Database:[/bold] {store.db_path}",
        f"[bold]Active theses:[/bold] {len(active)}",
        f"[bold]Unacknowledged alerts:[/bold] {len(unacked_alerts)}",
        f"[bold]Total closed trades:[/bold] {perf.get('total_trades', 0)}",
        f"[bold]Action decisions (24h):[/bold] {len(decisions_24h)}",
        f"[bold]Blocked decisions (24h):[/bold] {blocked_24h}",
        f"[bold]Executions (24h):[/bold] {executed_24h}",
        f"[bold]Execution failures (24h):[/bold] {failed_24h}",
    ]

    if active:
        tickers = ", ".join(t.ticker for t in active)
        status_lines.append(f"[bold]Monitored tickers:[/bold] {tickers}")

    console.print(
        Panel(
            "\n".join(status_lines),
            title="[bold green]Trade Journal Status[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )

    # Show recent alerts if any
    if unacked_alerts:
        console.print()
        _display_alerts(unacked_alerts[:5], title="Recent Unacknowledged Alerts")


@journal_app.command()
def theses(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    active_only: bool = typer.Option(False, "--active", help="Show only active theses"),
    ticker: Optional[str] = typer.Option(None, "--ticker", "-t", help="Filter by ticker"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """List trade theses in the journal."""
    store = _get_store(db_path)

    if ticker:
        all_theses = store.get_theses_by_ticker(ticker)
    elif active_only:
        all_theses = store.get_active_theses()
    else:
        all_theses = store.get_all_theses(limit=limit)

    if not all_theses:
        console.print("[dim]No theses found.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", width=12)
    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Action", width=6)
    table.add_column("Entry $", justify="right", width=10)
    table.add_column("Stop $", justify="right", width=10)
    table.add_column("Target $", justify="right", width=10)
    table.add_column("R:R", justify="right", width=6)
    table.add_column("Conv.", justify="right", width=6)
    table.add_column("Time Stop", width=12)
    table.add_column("Status", width=14)
    table.add_column("Date", width=12)

    for t in all_theses:
        action_color = {"BUY": "green", "SELL": "red"}.get(t.action, "yellow")
        status_color = {
            "active": "green",
            "stopped_out": "red",
            "target_reached": "cyan",
            "time_stopped": "yellow",
            "closed": "dim",
        }.get(t.status, "white")

        table.add_row(
            t.id,
            t.ticker,
            f"[{action_color}]{t.action}[/{action_color}]",
            f"${t.entry_price:.2f}" if t.entry_price else "—",
            f"${t.stop_loss:.2f}" if t.stop_loss else "—",
            f"${t.target_1:.2f}" if t.target_1 else "—",
            f"{t.risk_reward_ratio:.1f}" if t.risk_reward_ratio else "—",
            f"{t.conviction:.0f}" if t.conviction else "—",
            t.time_stop_date or "—",
            f"[{status_color}]{t.status}[/{status_color}]",
            t.trade_date,
        )

    console.print(
        Panel(table, title="[bold]Trade Theses[/bold]", border_style="blue")
    )


@journal_app.command()
def alerts(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    unacked_only: bool = typer.Option(True, "--all/--unacked", help="Show all or only unacknowledged"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """Show journal alerts."""
    store = _get_store(db_path)

    alert_list = store.get_alerts(
        unacknowledged_only=not unacked_only,  # --all flag inverts the default
        limit=limit,
    )

    if not alert_list:
        console.print("[dim]No alerts found.[/dim]")
        return

    _display_alerts(alert_list)


@journal_app.command(name="ack")
def acknowledge_alert(
    alert_id: str = typer.Argument(..., help="Alert ID to acknowledge"),
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Acknowledge (dismiss) a journal alert."""
    store = _get_store(db_path)
    store.acknowledge_alert(alert_id)
    console.print(f"[green]Alert {alert_id} acknowledged.[/green]")


@journal_app.command(name="check-now")
def check_now(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Run a single monitoring tick immediately."""
    from tradingagents.agents.journal.scheduler import JournalScheduler

    store = _get_store(db_path)
    executor = _get_executor()

    scheduler = JournalScheduler(store=store, executor=executor)
    console.print("[yellow]Running monitoring tick...[/yellow]")

    summary = scheduler.run_once()

    console.print(
        Panel(
            f"[bold]Theses checked:[/bold] {summary['theses_checked']}\n"
            f"[bold]Snapshots taken:[/bold] {summary['snapshots_taken']}\n"
            f"[bold]Alerts fired:[/bold] {summary['alerts_fired']}\n"
            f"[bold]Positions closed:[/bold] {summary['positions_closed']}\n"
            f"[bold]Outcomes recorded:[/bold] {summary.get('outcomes_recorded', 0)}\n"
            f"[bold]Actions evaluated:[/bold] {summary.get('actions_evaluated', 0)}\n"
            f"[bold]Actions recommended:[/bold] {summary.get('actions_recommended', 0)}\n"
            f"[bold]Actions executed:[/bold] {summary.get('actions_executed', 0)}\n"
            f"[bold]Actions blocked:[/bold] {summary.get('actions_blocked', 0)}\n"
            f"[bold]Actions failed:[/bold] {summary.get('actions_failed', 0)}\n"
            f"[bold]Duration:[/bold] {summary.get('duration_seconds', 0):.1f}s\n"
            f"[bold]Market session:[/bold] {summary.get('market_session', 'unknown')}\n"
            + (
                f"[bold red]Errors:[/bold red] {len(summary.get('errors', []))}"
                if summary.get("errors")
                else ""
            ),
            title="[bold green]Monitoring Tick Complete[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )

    # Show any new alerts
    if summary["alerts_fired"] > 0:
        new_alerts = store.get_alerts(unacknowledged_only=True, limit=10)
        if new_alerts:
            console.print()
            _display_alerts(new_alerts, title="New Alerts")


@journal_app.command()
def daemon(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    interval: int = typer.Option(15, "--interval", "-i", help="Market-hours interval (minutes)"),
):
    """Start the journal scheduler daemon (foreground, Ctrl+C to stop)."""
    from tradingagents.agents.journal.scheduler import JournalScheduler

    store = _get_store(db_path)
    executor = _get_executor()

    def on_tick(summary):
        ts = summary.get("timestamp", "")[:19]
        alerts = summary.get("alerts_fired", 0)
        checked = summary.get("theses_checked", 0)
        session = summary.get("market_session", "?")
        actions_exec = summary.get("actions_executed", 0)
        actions_blocked = summary.get("actions_blocked", 0)
        alert_icon = f" 🚨 {alerts} alert(s)" if alerts > 0 else ""
        action_suffix = f", actions={actions_exec}/{actions_blocked} (exec/blocked)"
        console.print(
            f"[dim]{ts}[/dim] tick: {checked} theses, "
            f"session={session}{alert_icon}{action_suffix}"
        )

    scheduler = JournalScheduler(
        store=store,
        executor=executor,
        market_interval_minutes=interval,
        on_tick_complete=on_tick,
    )

    console.print(
        Panel(
            f"[bold]Database:[/bold] {store.db_path}\n"
            f"[bold]Market interval:[/bold] {interval}m\n"
            f"[bold]Executor:[/bold] {'Alpaca' if executor else 'None (price-only mode)'}\n"
            f"\n[dim]Press Ctrl+C to stop[/dim]",
            title="[bold green]Journal Scheduler Daemon[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )

    scheduler.run_forever()


@journal_app.command()
def outcomes(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """List trade outcomes (closed trades with P&L)."""
    store = _get_store(db_path)
    outcome_list = store.get_all_outcomes(limit=limit)

    if not outcome_list:
        console.print("[dim]No outcomes recorded yet.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Entry $", justify="right", width=10)
    table.add_column("Exit $", justify="right", width=10)
    table.add_column("P&L %", justify="right", width=8)
    table.add_column("P&L $", justify="right", width=10)
    table.add_column("Days", justify="right", width=6)
    table.add_column("R-Multiple", justify="right", width=10)
    table.add_column("Alpha %", justify="right", width=8)
    table.add_column("MAE %", justify="right", width=8)
    table.add_column("Exit Reason", width=16)
    table.add_column("Closed", width=12)

    for o in outcome_list:
        pl_color = "green" if (o.realized_pl_pct or 0) >= 0 else "red"
        alpha_color = "green" if (o.alpha_pct or 0) >= 0 else "red"

        table.add_row(
            o.ticker,
            f"${o.entry_price:.2f}" if o.entry_price else "—",
            f"${o.exit_price:.2f}" if o.exit_price else "—",
            f"[{pl_color}]{o.realized_pl_pct:+.2f}%[/{pl_color}]" if o.realized_pl_pct is not None else "—",
            f"[{pl_color}]${o.realized_pl:+,.2f}[/{pl_color}]" if o.realized_pl is not None else "—",
            str(o.holding_days) if o.holding_days else "—",
            f"{o.risk_multiple:+.1f}R" if o.risk_multiple is not None else "—",
            f"[{alpha_color}]{o.alpha_pct:+.2f}%[/{alpha_color}]" if o.alpha_pct is not None else "—",
            f"{o.max_adverse_excursion_pct:.1f}%" if o.max_adverse_excursion_pct is not None else "—",
            o.exit_reason or "—",
            (o.closed_at or "")[:10],
        )

    console.print(
        Panel(table, title="[bold]Trade Outcomes[/bold]", border_style="blue")
    )


@journal_app.command()
def decisions(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    ticker: Optional[str] = typer.Option(None, "--ticker", "-t", help="Filter by ticker"),
    thesis_id: Optional[str] = typer.Option(None, "--thesis-id", help="Filter by thesis id"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
):
    """List journal action decisions."""
    store = _get_store(db_path)
    rows = store.get_action_decisions(thesis_id=thesis_id, ticker=ticker, limit=limit)
    if not rows:
        console.print("[dim]No action decisions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Time", width=19)
    table.add_column("Ticker", width=8, style="cyan")
    table.add_column("Decision", width=20)
    table.add_column("Reason", width=24)
    table.add_column("Conf", width=6, justify="right")
    table.add_column("Qty %", width=6, justify="right")
    table.add_column("Gate", width=8)
    table.add_column("Dry", width=5, justify="center")

    for d in rows:
        gate = "[green]pass[/green]" if d.gates_passed else "[red]block[/red]"
        table.add_row(
            (d.created_at or "")[:19],
            d.ticker,
            d.decision_type,
            d.reason_code,
            f"{d.confidence:.0f}",
            f"{(d.recommended_qty_pct or 0):.2f}" if d.recommended_qty_pct is not None else "—",
            gate,
            "Y" if d.dry_run else "N",
        )
    console.print(Panel(table, title="[bold]Journal Action Decisions[/bold]", border_style="cyan"))


@journal_app.command()
def executions(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    ticker: Optional[str] = typer.Option(None, "--ticker", "-t", help="Filter by ticker"),
    decision_id: Optional[str] = typer.Option(None, "--decision-id", help="Filter by decision id"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
):
    """List journal action executions."""
    store = _get_store(db_path)
    rows = store.get_action_executions(decision_id=decision_id, ticker=ticker, limit=limit)
    if not rows:
        console.print("[dim]No action executions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Time", width=19)
    table.add_column("Ticker", width=8, style="cyan")
    table.add_column("Decision ID", width=12)
    table.add_column("Signal", width=6)
    table.add_column("Qty", width=8, justify="right")
    table.add_column("Status", width=10)
    table.add_column("Order ID", width=14)
    table.add_column("Error", no_wrap=False)

    for e in rows:
        status_color = "green" if e.status in {"dry_run", "submitted"} else "red"
        table.add_row(
            (e.created_at or "")[:19],
            e.ticker,
            e.decision_id,
            e.submitted_signal,
            f"{e.submitted_qty:.2f}" if e.submitted_qty is not None else "—",
            f"[{status_color}]{e.status}[/{status_color}]",
            e.broker_order_id or "—",
            (e.error or "")[:80],
        )
    console.print(Panel(table, title="[bold]Journal Action Executions[/bold]", border_style="cyan"))


@journal_app.command()
def performance(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Show aggregate performance metrics across all closed trades."""
    store = _get_store(db_path)
    perf = store.get_performance_summary()

    if perf.get("total_trades", 0) == 0:
        console.print("[dim]No closed trades yet — performance metrics unavailable.[/dim]")
        return

    # Format metrics
    lines = [
        f"[bold]Total closed trades:[/bold] {perf['total_trades']}",
        f"[bold]Win rate:[/bold] {perf['win_rate']:.1%} ({perf['winners']}W / {perf['losers']}L)",
        "",
    ]

    avg_ret = perf.get("avg_return_pct")
    if avg_ret is not None:
        color = "green" if avg_ret >= 0 else "red"
        lines.append(f"[bold]Avg return per trade:[/bold] [{color}]{avg_ret:+.2f}%[/{color}]")

    total_pl = perf.get("total_pl")
    if total_pl is not None:
        color = "green" if total_pl >= 0 else "red"
        lines.append(f"[bold]Total P&L:[/bold] [{color}]${total_pl:+,.2f}[/{color}]")

    avg_alpha = perf.get("avg_alpha_pct")
    if avg_alpha is not None:
        color = "green" if avg_alpha >= 0 else "red"
        lines.append(f"[bold]Avg alpha vs SPY:[/bold] [{color}]{avg_alpha:+.2f}%[/{color}]")

    avg_days = perf.get("avg_holding_days")
    if avg_days is not None:
        lines.append(f"[bold]Avg holding days:[/bold] {avg_days:.1f}")

    lines.append("")

    best = perf.get("best_trade_pct")
    worst = perf.get("worst_trade_pct")
    if best is not None:
        lines.append(f"[bold]Best trade:[/bold] [green]{best:+.2f}%[/green]")
    if worst is not None:
        lines.append(f"[bold]Worst trade:[/bold] [red]{worst:+.2f}%[/red]")

    avg_rm = perf.get("avg_risk_multiple")
    if avg_rm is not None:
        lines.append(f"[bold]Avg R-multiple:[/bold] {avg_rm:+.2f}R")

    avg_capture = perf.get("avg_capture_ratio")
    if avg_capture is not None:
        lines.append(f"[bold]Avg capture ratio:[/bold] {avg_capture:.1%}")

    avg_mae = perf.get("avg_mae")
    avg_mfe = perf.get("avg_mfe")
    if avg_mae is not None:
        lines.append(f"[bold]Avg max adverse excursion:[/bold] {avg_mae:.2f}%")
    if avg_mfe is not None:
        lines.append(f"[bold]Avg max favorable excursion:[/bold] {avg_mfe:.2f}%")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold green]Performance Summary[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )


@journal_app.command(name="inspect")
def inspect_thesis(
    thesis_id: str = typer.Argument(..., help="Thesis ID to inspect"),
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Inspect a single thesis with full details, snapshots, and alerts."""
    store = _get_store(db_path)

    thesis = store.get_thesis(thesis_id)
    if not thesis:
        console.print(f"[red]Thesis {thesis_id} not found.[/red]")
        raise typer.Exit(code=1)

    # Thesis details
    detail_lines = [
        f"[bold]Ticker:[/bold] {thesis.ticker}",
        f"[bold]Action:[/bold] {thesis.action}",
        f"[bold]Status:[/bold] {thesis.status}",
        f"[bold]Trade date:[/bold] {thesis.trade_date}",
        f"[bold]Conviction:[/bold] {thesis.conviction}" if thesis.conviction else "",
        "",
        f"[bold]Entry price:[/bold] ${thesis.entry_price:.2f}" if thesis.entry_price else "",
        f"[bold]Stop loss:[/bold] ${thesis.stop_loss:.2f}" if thesis.stop_loss else "",
        f"[bold]Target 1:[/bold] ${thesis.target_1:.2f}" if thesis.target_1 else "",
        f"[bold]Target 2:[/bold] ${thesis.target_2:.2f}" if thesis.target_2 else "",
        f"[bold]R:R ratio:[/bold] {thesis.risk_reward_ratio:.2f}" if thesis.risk_reward_ratio else "",
        "",
        f"[bold]Time horizon:[/bold] {thesis.time_horizon_label}" if thesis.time_horizon_label else "",
        f"[bold]Time stop:[/bold] {thesis.time_stop_date}" if thesis.time_stop_date else "",
        f"[bold]Holding days planned:[/bold] {thesis.holding_days_planned}" if thesis.holding_days_planned else "",
        "",
        f"[bold]Regime:[/bold] {thesis.regime}" if thesis.regime else "",
        f"[bold]Catalyst:[/bold] {thesis.catalyst}" if thesis.catalyst else "",
        f"[bold]Invalidation:[/bold] {thesis.invalidation_trigger}" if thesis.invalidation_trigger else "",
        f"[bold]Key risks:[/bold] {thesis.key_risks}" if thesis.key_risks else "",
    ]
    detail_lines = [l for l in detail_lines if l or l == ""]  # Keep blank lines for spacing

    console.print(
        Panel(
            "\n".join(detail_lines),
            title=f"[bold]{thesis.ticker} — Thesis {thesis.id}[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Recent snapshots
    snapshots = store.get_snapshots(thesis_id, limit=10)
    if snapshots:
        snap_table = Table(
            show_header=True, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1),
        )
        snap_table.add_column("Time", width=20)
        snap_table.add_column("Price", justify="right", width=10)
        snap_table.add_column("P&L %", justify="right", width=8)
        snap_table.add_column("→ Stop", justify="right", width=8)
        snap_table.add_column("→ Tgt1", justify="right", width=8)
        snap_table.add_column("Days", justify="right", width=6)
        snap_table.add_column("vs SPY", justify="right", width=8)

        for s in snapshots:
            pl_color = "green" if (s.unrealized_pl_pct or 0) >= 0 else "red"
            snap_table.add_row(
                (s.timestamp or "")[:19],
                f"${s.current_price:.2f}" if s.current_price else "—",
                f"[{pl_color}]{s.unrealized_pl_pct:+.2f}%[/{pl_color}]" if s.unrealized_pl_pct is not None else "—",
                f"{s.distance_to_stop_pct:.1f}%" if s.distance_to_stop_pct is not None else "—",
                f"{s.distance_to_target1_pct:.1f}%" if s.distance_to_target1_pct is not None else "—",
                str(s.holding_days_elapsed) if s.holding_days_elapsed is not None else "—",
                f"{s.relative_strength:+.1f}%" if s.relative_strength is not None else "—",
            )

        console.print()
        console.print(
            Panel(snap_table, title="[bold]Recent Snapshots[/bold]", border_style="blue")
        )

    # Alerts for this thesis
    thesis_alerts = store.get_alerts(thesis_id=thesis_id, limit=10)
    if thesis_alerts:
        console.print()
        _display_alerts(thesis_alerts, title=f"Alerts for {thesis.ticker}")

    # Outcome if closed
    outcome = store.get_outcome(thesis_id)
    if outcome:
        console.print()
        pl_color = "green" if (outcome.realized_pl_pct or 0) >= 0 else "red"
        outcome_lines = [
            f"[bold]Exit price:[/bold] ${outcome.exit_price:.2f}" if outcome.exit_price else "",
            f"[bold]P&L:[/bold] [{pl_color}]{outcome.realized_pl_pct:+.2f}% (${outcome.realized_pl:+,.2f})[/{pl_color}]" if outcome.realized_pl_pct is not None else "",
            f"[bold]Holding days:[/bold] {outcome.holding_days}" if outcome.holding_days else "",
            f"[bold]R-multiple:[/bold] {outcome.risk_multiple:+.1f}R" if outcome.risk_multiple is not None else "",
            f"[bold]Alpha vs SPY:[/bold] {outcome.alpha_pct:+.2f}%" if outcome.alpha_pct is not None else "",
            f"[bold]Exit reason:[/bold] {outcome.exit_reason}" if outcome.exit_reason else "",
            f"[bold]Thesis correct:[/bold] {'✓' if outcome.thesis_correct else '✗'}" if outcome.thesis_correct is not None else "",
            f"[bold]MAE:[/bold] {outcome.max_adverse_excursion_pct:.2f}%" if outcome.max_adverse_excursion_pct is not None else "",
            f"[bold]MFE:[/bold] {outcome.max_favorable_excursion_pct:.2f}%" if outcome.max_favorable_excursion_pct is not None else "",
            f"[bold]Capture ratio:[/bold] {outcome.capture_ratio:.1%}" if outcome.capture_ratio is not None else "",
        ]
        outcome_lines = [l for l in outcome_lines if l]

        console.print(
            Panel(
                "\n".join(outcome_lines),
                title="[bold]Trade Outcome[/bold]",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Lessons for this thesis
    lessons = store.get_lessons_by_thesis(thesis_id)
    if lessons:
        console.print()
        lesson = lessons[0]  # Most recent
        lesson_lines = [
            f"[bold]Category:[/bold] {lesson.category}",
            f"[bold]Confidence:[/bold] {lesson.confidence:.0f}%",
            "",
            f"[bold]Lesson:[/bold] {lesson.lesson_text}",
        ]
        if lesson.what_worked:
            lesson_lines.append(f"[bold green]What worked:[/bold green] {'; '.join(lesson.what_worked)}")
        if lesson.what_failed:
            lesson_lines.append(f"[bold red]What failed:[/bold red] {'; '.join(lesson.what_failed)}")
        if lesson.most_accurate_agent:
            lesson_lines.append(f"[bold]Most accurate agent:[/bold] {lesson.most_accurate_agent}")
        if lesson.least_accurate_agent:
            lesson_lines.append(f"[bold]Least accurate agent:[/bold] {lesson.least_accurate_agent}")

        console.print(
            Panel(
                "\n".join(lesson_lines),
                title="[bold]Trade Lesson[/bold]",
                border_style="yellow",
                padding=(1, 2),
            )
        )


@journal_app.command()
def lessons(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """List trade lessons extracted from completed trades."""
    store = _get_store(db_path)

    if category:
        lesson_list = store.get_lessons_by_category(category, limit=limit)
    else:
        lesson_list = store.get_all_lessons(limit=limit)

    if not lesson_list:
        console.print("[dim]No lessons recorded yet. Lessons are generated when trades close and reflection runs.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold yellow",
        box=box.SIMPLE_HEAD,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Category", width=20)
    table.add_column("P&L %", justify="right", width=8)
    table.add_column("R-Mult", justify="right", width=8)
    table.add_column("Conf", justify="right", width=6)
    table.add_column("Lesson", no_wrap=False)
    table.add_column("Date", width=12)

    for lesson in lesson_list:
        pl_color = "green" if (lesson.realized_pl_pct or 0) >= 0 else "red"
        table.add_row(
            lesson.ticker,
            lesson.category,
            f"[{pl_color}]{lesson.realized_pl_pct:+.1f}%[/{pl_color}]" if lesson.realized_pl_pct is not None else "—",
            f"{lesson.risk_multiple:+.1f}R" if lesson.risk_multiple is not None else "—",
            f"{lesson.confidence:.0f}",
            lesson.lesson_text[:80] + ("..." if len(lesson.lesson_text) > 80 else ""),
            lesson.trade_date,
        )

    console.print(
        Panel(table, title="[bold]Trade Lessons[/bold]", border_style="yellow")
    )

    # Show category distribution
    categories = store.get_lesson_categories()
    if categories:
        cat_str = " | ".join(f"{cat}: {count}" for cat, count in list(categories.items())[:5])
        console.print(f"\n[dim]Categories: {cat_str}[/dim]")


@journal_app.command(name="lesson-memory")
def lesson_memory_stats(
    db_path: Optional[str] = typer.Option(None, "--db", help="Database path"),
):
    """Show lesson memory (ChromaDB) statistics."""
    try:
        from tradingagents.agents.journal.lesson_memory import LessonMemory
        memory = LessonMemory()
        stats = memory.get_stats()

        lines = [
            f"[bold]Total lessons in memory:[/bold] {stats['total_lessons']}",
            f"[bold]Persist directory:[/bold] {stats['persist_directory']}",
            "",
            f"[bold]Winning trades:[/bold] [green]{stats['winning_trades']}[/green]",
            f"[bold]Losing trades:[/bold] [red]{stats['losing_trades']}[/red]",
        ]

        if stats.get("categories"):
            lines.append("")
            lines.append("[bold]Categories:[/bold]")
            for cat, count in list(stats["categories"].items())[:10]:
                lines.append(f"  • {cat}: {count}")

        if stats.get("tickers"):
            lines.append("")
            lines.append("[bold]Tickers:[/bold]")
            for ticker, count in list(stats["tickers"].items())[:10]:
                lines.append(f"  • {ticker}: {count}")

        console.print(
            Panel(
                "\n".join(lines),
                title="[bold yellow]Lesson Memory (ChromaDB)[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
    except ImportError:
        console.print("[red]ChromaDB is not installed. Run: pip install chromadb[/red]")
    except Exception as e:
        console.print(f"[red]Error accessing lesson memory: {e}[/red]")


@journal_app.command(name="query-lessons")
def query_lessons(
    query: str = typer.Argument(..., help="Natural language query to search lessons"),
    n: int = typer.Option(5, "--n", "-n", help="Number of results"),
):
    """Search lessons using semantic similarity."""
    try:
        from tradingagents.agents.journal.lesson_memory import LessonMemory
        memory = LessonMemory()

        console.print(f"[dim]Searching for: '{query}'...[/dim]\n")
        results = memory.query_similar(query, n_results=n)

        if not results:
            console.print("[dim]No lessons found.[/dim]")
            return

        for i, result in enumerate(results, 1):
            meta = result.get("metadata", {})
            similarity = result.get("similarity", 0)
            pl_color = "green" if meta.get("realized_pl_pct", 0) >= 0 else "red"

            console.print(
                Panel(
                    f"[bold]Ticker:[/bold] {meta.get('ticker', '?')} | "
                    f"[bold]Category:[/bold] {meta.get('category', '?')} | "
                    f"[bold]P&L:[/bold] [{pl_color}]{meta.get('realized_pl_pct', 0):+.1f}%[/{pl_color}]\n\n"
                    f"{result.get('document', '')}",
                    title=f"[bold]#{i} (similarity: {similarity:.2%})[/bold]",
                    border_style="yellow",
                    padding=(1, 2),
                )
            )
    except ImportError:
        console.print("[red]ChromaDB is not installed. Run: pip install chromadb[/red]")
    except Exception as e:
        console.print(f"[red]Error querying lessons: {e}[/red]")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _display_alerts(alert_list, title="Alerts"):
    """Display a table of alerts."""
    table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.SIMPLE_HEAD,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Time", width=20)
    table.add_column("Ticker", style="cyan", width=8)
    table.add_column("Type", width=16)
    table.add_column("Severity", width=10)
    table.add_column("Message", no_wrap=False)
    table.add_column("Ack", width=4, justify="center")

    severity_colors = {
        "critical": "bold red",
        "warning": "yellow",
        "info": "dim",
    }

    for a in alert_list:
        sev_style = severity_colors.get(a.severity, "white")
        ack = "✓" if a.acknowledged else "—"

        table.add_row(
            (a.timestamp or "")[:19],
            a.ticker,
            a.alert_type,
            f"[{sev_style}]{a.severity}[/{sev_style}]",
            a.message[:120] + ("..." if len(a.message) > 120 else ""),
            ack,
        )

    console.print(
        Panel(table, title=f"[bold]{title}[/bold]", border_style="magenta")
    )


def _ts_within_hours(ts: Optional[str], *, now: datetime, hours: int) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return (now - dt).total_seconds() <= hours * 3600
