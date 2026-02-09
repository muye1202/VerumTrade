# Trade Journal Agent — Integration Guide

## File Placement

Drop these into your existing repo:

```
tradingagents/journal/          ← NEW package (all 8 files)
    __init__.py
    models.py                   # Data models (TradeThesis, PositionSnapshot, JournalAlert, TradeOutcome)
    store.py                    # SQLite persistence layer
    thesis_extractor.py         # Parses agent analysis state → structured thesis
    monitor.py                  # Checks positions against thesis parameters
    outcome.py                  # Computes P&L and thesis accuracy on position close
    scheduler.py                # Daemon that wakes the monitor on a timer
    hooks.py                    # Glue: auto-captures theses when trades execute

cli/journal_cli.py              ← NEW CLI subcommand
```

## Wiring Into Existing Code

### 1. Register the CLI subcommand (`cli/main.py`)

```python
# Near the top imports:
from cli.journal_cli import journal_app

# After your existing app setup:
app.add_typer(journal_app, name="journal")
```

Now you can run:
```bash
python -m cli.main journal status
python -m cli.main journal theses --active
python -m cli.main journal alerts
python -m cli.main journal check-now
python -m cli.main journal daemon --interval 15
python -m cli.main journal outcomes
python -m cli.main journal performance
python -m cli.main journal inspect <thesis_id>
```

### 2. Auto-capture theses on trade execution (`cli/analysis_utils.py`)

In `run_single_ticker_analysis()`, after the execution block, add:

```python
# After: execution_result = executor.execute_signal(...)
# Add journal capture:
try:
    from tradingagents.journal.store import JournalStore
    from tradingagents.journal.hooks import capture_trade_thesis

    journal_store = JournalStore()  # Uses default ./journal/trade_journal.db
    capture_trade_thesis(
        store=journal_store,
        final_state=final_state,
        structured_decision=structured,
        execution_result=execution_result,
        trade_date=selections["analysis_date"],
    )
except Exception as e:
    # Journal capture is non-critical — never break the main flow
    logger.warning(f"Journal capture failed: {e}")
```

### 3. Same for portfolio analysis (`cli/portfolio_analysis_utils.py`)

In the per-ticker analysis loop, after execution:

```python
try:
    from tradingagents.journal.hooks import capture_trade_thesis
    from tradingagents.journal.store import JournalStore

    journal_store = JournalStore()
    capture_trade_thesis(
        store=journal_store,
        final_state=final_state,
        structured_decision=structured,
        execution_result=execution_result,
        trade_date=analysis_date,
    )
except Exception:
    pass  # Non-critical
```

### 4. Start the monitoring daemon

Run alongside your trading system:

```bash
# Terminal 1: your normal analysis
python -m cli.main analyze

# Terminal 2: journal daemon (monitors positions against theses)
python -m cli.main journal daemon --interval 15
```

Or as a standalone process:
```bash
python -m tradingagents.journal.scheduler --interval 15
```

### 5. Start the scheduler as a background thread (programmatic)

```python
from tradingagents.journal.store import JournalStore
from tradingagents.journal.scheduler import JournalScheduler
from tradingagents.execution import AlpacaExecutor

store = JournalStore()
executor = AlpacaExecutor(paper=True)

scheduler = JournalScheduler(
    store=store,
    executor=executor,
    market_interval_minutes=15,
    on_tick_complete=lambda s: print(f"Tick: {s['alerts_fired']} alerts"),
)
scheduler.start_background()  # Non-blocking, runs in daemon thread
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Existing Pipeline                             │
│                                                                 │
│  Analysts → Debate → Trader → Risk Judge → AlpacaExecutor       │
│                                                │                │
│                                                ▼                │
│                                    ┌───────────────────┐        │
│                                    │   hooks.py        │        │
│                                    │ capture_trade_    │        │
│                                    │ thesis()          │        │
│                                    └────────┬──────────┘        │
└─────────────────────────────────────────────┼───────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Trade Journal System                         │
│                                                                 │
│  ┌──────────────────┐     ┌──────────────────────┐              │
│  │ ThesisExtractor   │────▶│    JournalStore       │             │
│  │ (parse agents →   │     │    (SQLite DB)        │             │
│  │  structured thesis)│     │                      │             │
│  └──────────────────┘     │  trade_theses         │             │
│                           │  position_snapshots    │             │
│                           │  journal_alerts        │             │
│                           │  trade_outcomes        │             │
│                           └──────────┬─────────────┘             │
│                                      │                          │
│  ┌──────────────────┐               │                          │
│  │ JournalScheduler  │◀──────────────┘                          │
│  │  (daemon/thread)  │                                          │
│  │  market-aware     │     ┌──────────────────────┐             │
│  │  interval timer   │────▶│  PositionMonitor      │             │
│  │                   │     │  • fetch brokerage    │             │
│  │  15m (market)     │     │  • fetch prices       │             │
│  │  30m (ext hours)  │     │  • check stop/target  │             │
│  │  2h  (overnight)  │     │  • check time-stop    │             │
│  └──────────────────┘     │  • detect gaps        │             │
│                           │  • detect closures    │             │
│                           └──────────┬─────────────┘             │
│                                      │                          │
│                           ┌──────────▼─────────────┐             │
│                           │  OutcomeRecorder        │             │
│                           │  • P&L + slippage      │             │
│                           │  • MAE / MFE           │             │
│                           │  • alpha vs SPY        │             │
│                           │  • R-multiple          │             │
│                           │  • thesis accuracy     │             │
│                           └──────────┬─────────────┘             │
│                                      │                          │
│                                      ▼                          │
│                           ┌────────────────────────┐             │
│                           │  🔓 LEARNING LOOP HOOK  │             │
│                           │  TradeOutcome.          │             │
│                           │  reflection_notes       │             │
│                           │                        │             │
│                           │  → reflect_and_remember│             │
│                           │    (future integration)│             │
│                           └────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

## Learning Loop Hook (Open for Discussion)

The `TradeOutcome` model has a `reflection_notes` field left as `None`. The idea for closing the loop:

```python
# Future: after OutcomeRecorder produces an outcome
outcome = outcome_recorder.record_outcome(thesis_id)

# A ReflectionAgent would:
# 1. Consume the structured outcome (P&L, R-multiple, thesis accuracy flags)
# 2. Format it as returns_losses for the existing reflect_and_remember()
# 3. Call graph.reflect_and_remember(returns_losses) to update ChromaDB memories
# 4. Save reflection_notes back to the outcome

# The question: should the reflection be LLM-driven (generates natural language
# lessons like "RSI divergence signals worked well in uptrend regime") or purely
# structured (just feeds the numbers into vector memory)?
```

## Zero External Dependencies

The journal system uses only:
- `sqlite3` (stdlib)
- `threading` (stdlib)
- `zoneinfo` (stdlib, Python 3.9+)
- `yfinance` (already in the project — used for price checks, gracefully degrades if unavailable)
- `alpaca-py` (already in the project — used for brokerage positions, optional)

No new pip installs required.
