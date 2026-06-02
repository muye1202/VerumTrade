# Trade Journal Agent

The journal tracks every trade lifecycle — **thesis → monitoring → outcome → reflection** — and feeds structured lessons back into future trading decisions.

> **New here?** See [USAGE.md](USAGE.md) for the step-by-step guide on importing reports, starting the daemon, and enabling automated execution.

## Directory Structure

```
opentrace/agents/journal/
│
├── __init__.py          ← Public API re-exports (backward-compatible)
│
├── core/                ← Data models & SQLite persistence
│   ├── models.py        ← Dataclasses: TradeThesis, PositionSnapshot, JournalAlert, TradeOutcome, TradeLesson, enums
│   └── store.py         ← JournalStore — single-writer/multi-reader SQLite (WAL mode)
│
├── ingestion/           ← Getting trades into the journal
│   ├── thesis_extractor.py  ← Parses agent pipeline final_state into a structured TradeThesis
│   ├── hooks.py             ← capture_trade_thesis() — glue between execution flow and journal
│   └── report_import.py     ← Imports v2 scheduled-order decisions from results/stocks/
│
├── monitoring/          ← Live position surveillance
│   ├── monitor.py       ← PositionMonitor — compares live positions against thesis parameters each tick
│   ├── scheduler.py     ← JournalScheduler — APScheduler daemon, wakes monitor periodically
│   └── outcome.py       ← OutcomeRecorder — computes P&L and thesis-accuracy on position close
│
├── evaluation/          ← Tiered decision plan evaluation
│   ├── decision_plan_evaluator.py  ← Tier 0: stateless rule-based branch matcher (every tick)
│   ├── condition_tracker.py        ← Tier 1: stateful cross-tick condition evaluation (SQLite-backed)
│   ├── thesis_state_machine.py     ← Lifecycle phases: PENDING → WATCHING → NEAR_TRIGGER → TRIGGERED → ACTIVE → CLOSED
│   ├── llm_evaluator.py            ← Tier 2: LLM evaluation for ambiguous near-trigger situations
│   ├── event_compiler.py           ← LLM-powered translation of event_conditions → deterministic CheckerSpecs
│   ├── smart_evaluator.py          ← SmartPlanEvaluator — orchestrates the full Tier 0+1+2 pipeline
│   └── news_event_inference.py     ← Rule-based event flag inference from thesis text
│
├── execution/           ← Action decision & guardrails
│   ├── execution_advisor.py  ← JournalExecutionAdvisor — rules-first engine for action decisions
│   └── execution_policy.py   ← JournalExecutionPolicy — hard guardrails (dry-run, cooldown, daily limits)
│
├── portfolio/           ← Brokerage sync
│   └── portfolio_sync.py    ← sync_missing_positions() — creates theses from live Alpaca positions (create-only)
│
├── learning/            ← Post-trade reflection & memory
│   ├── reflection_agent.py  ← LLM-powered trade post-mortem → structured TradeLesson
│   └── lesson_memory.py     ← ChromaDB-backed vector store for semantic lesson retrieval
│
└── tests/               ← Pytest suite
    ├── test_execution_policy.py
    ├── test_monitor_alert_gating.py
    ├── test_monitor_quote_quality.py
    ├── test_portfolio_sync.py
    └── test_scheduler_portfolio_pull.py
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
        │
        ▼ ingestion/hooks.py, ingestion/thesis_extractor.py
   TradeThesis ──→ core/store.py (SQLite)
        │
        ▼ monitoring/scheduler.py (timed daemon)
   monitoring/monitor.py
        ├── evaluation/* (Tier 0 → 1 → 2)
        ├── execution/execution_advisor.py  → ActionDecision
        │        └── execution/execution_policy.py (guardrails)
        └── core/store.py (alerts, snapshots)
        │
        ▼ position closed
   monitoring/outcome.py → TradeOutcome
        │
        ▼
   learning/reflection_agent.py → TradeLesson
        │
        ▼
   learning/lesson_memory.py (ChromaDB)
```

## Running Tests

```bash
python -m pytest opentrace/agents/journal/tests/ -v
```
