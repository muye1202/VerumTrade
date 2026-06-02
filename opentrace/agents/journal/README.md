# Trade Journal Agent

The journal tracks every trade lifecycle â€” **thesis â†’ monitoring â†’ outcome â†’ reflection** â€” and feeds structured lessons back into future trading decisions.

> **New here?** See [USAGE.md](USAGE.md) for the step-by-step guide on importing reports, starting the daemon, and enabling automated execution.

## Directory Structure

```
opentrace/agents/journal/
â”‚
â”œâ”€â”€ __init__.py          â† Public API re-exports (backward-compatible)
â”‚
â”œâ”€â”€ core/                â† Data models & SQLite persistence
â”‚   â”œâ”€â”€ models.py        â† Dataclasses: TradeThesis, PositionSnapshot, JournalAlert, TradeOutcome, TradeLesson, enums
â”‚   â””â”€â”€ store.py         â† JournalStore â€” single-writer/multi-reader SQLite (WAL mode)
â”‚
â”œâ”€â”€ ingestion/           â† Getting trades into the journal
â”‚   â”œâ”€â”€ thesis_extractor.py  â† Parses agent pipeline final_state into a structured TradeThesis
â”‚   â”œâ”€â”€ hooks.py             â† capture_trade_thesis() â€” glue between execution flow and journal
â”‚   â””â”€â”€ report_import.py     â† Imports v2 scheduled-order decisions from results/stocks/
â”‚
â”œâ”€â”€ monitoring/          â† Live position surveillance
â”‚   â”œâ”€â”€ monitor.py       â† PositionMonitor â€” compares live positions against thesis parameters each tick
â”‚   â”œâ”€â”€ scheduler.py     â† JournalScheduler â€” APScheduler daemon, wakes monitor periodically
â”‚   â””â”€â”€ outcome.py       â† OutcomeRecorder â€” computes P&L and thesis-accuracy on position close
â”‚
â”œâ”€â”€ evaluation/          â† Tiered decision plan evaluation
â”‚   â”œâ”€â”€ decision_plan_evaluator.py  â† Tier 0: stateless rule-based branch matcher (every tick)
â”‚   â”œâ”€â”€ condition_tracker.py        â† Tier 1: stateful cross-tick condition evaluation (SQLite-backed)
â”‚   â”œâ”€â”€ thesis_state_machine.py     â† Lifecycle phases: PENDING â†’ WATCHING â†’ NEAR_TRIGGER â†’ TRIGGERED â†’ ACTIVE â†’ CLOSED
â”‚   â”œâ”€â”€ llm_evaluator.py            â† Tier 2: LLM evaluation for ambiguous near-trigger situations
â”‚   â”œâ”€â”€ event_compiler.py           â† LLM-powered translation of event_conditions â†’ deterministic CheckerSpecs
â”‚   â”œâ”€â”€ smart_evaluator.py          â† SmartPlanEvaluator â€” orchestrates the full Tier 0+1+2 pipeline
â”‚   â””â”€â”€ news_event_inference.py     â† Rule-based event flag inference from thesis text
â”‚
â”œâ”€â”€ execution/           â† Action decision & guardrails
â”‚   â”œâ”€â”€ execution_advisor.py  â† JournalExecutionAdvisor â€” rules-first engine for action decisions
â”‚   â””â”€â”€ execution_policy.py   â† JournalExecutionPolicy â€” hard guardrails (dry-run, cooldown, daily limits)
â”‚
â”œâ”€â”€ portfolio/           â† Brokerage sync
â”‚   â””â”€â”€ portfolio_sync.py    â† sync_missing_positions() â€” creates theses from live Alpaca positions (create-only)
â”‚
â”œâ”€â”€ learning/            â† Post-trade reflection & memory
â”‚   â”œâ”€â”€ reflection_agent.py  â† LLM-powered trade post-mortem â†’ structured TradeLesson
â”‚   â””â”€â”€ lesson_memory.py     â† ChromaDB-backed vector store for semantic lesson retrieval
â”‚
â””â”€â”€ tests/               â† Pytest suite
    â”œâ”€â”€ test_execution_policy.py
    â”œâ”€â”€ test_monitor_alert_gating.py
    â”œâ”€â”€ test_monitor_quote_quality.py
    â”œâ”€â”€ test_portfolio_sync.py
    â””â”€â”€ test_scheduler_portfolio_pull.py
```

## Public API

All key symbols are re-exported from the top-level package for backward compatibility:

```python
from opentrace.agents.journal import (
    # Core
    JournalStore,
    TradeThesis, PositionSnapshot, JournalAlert, TradeOutcome, TradeLesson,
    AlertType, ThesisStatus, ActionDecisionType, ActionReasonCode,
    JournalActionDecision, JournalActionExecution,
    # Ingestion
    ThesisExtractor, import_scheduled_reports,
    # Monitoring
    PositionMonitor, OutcomeRecorder, JournalScheduler,
    # Evaluation
    infer_event_flags, event_inference_enabled,
    # Execution
    JournalExecutionAdvisor, ActionContext, JournalExecutionPolicy, PolicyResult,
    # Portfolio sync
    sync_missing_positions,
    # Learning
    ReflectionAgent, create_reflection_callback, LessonMemory,
)
```

## Data Flow

```
Agent pipeline output
        â”‚
        â–¼ ingestion/hooks.py, ingestion/thesis_extractor.py
   TradeThesis â”€â”€â†’ core/store.py (SQLite)
        â”‚
        â–¼ monitoring/scheduler.py (timed daemon)
   monitoring/monitor.py
        â”œâ”€â”€ evaluation/* (Tier 0 â†’ 1 â†’ 2)
        â”œâ”€â”€ execution/execution_advisor.py  â†’ ActionDecision
        â”‚        â””â”€â”€ execution/execution_policy.py (guardrails)
        â””â”€â”€ core/store.py (alerts, snapshots)
        â”‚
        â–¼ position closed
   monitoring/outcome.py â†’ TradeOutcome
        â”‚
        â–¼
   learning/reflection_agent.py â†’ TradeLesson
        â”‚
        â–¼
   learning/lesson_memory.py (ChromaDB)
```

## Running Tests

```bash
python -m pytest opentrace/agents/journal/tests/ -v
```
