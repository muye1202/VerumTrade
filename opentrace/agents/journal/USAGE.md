# Journal Agent — User Guide

The journal agent automates post-analysis trade monitoring. You run the analysis pipeline, import the decision reports, start the daemon, and the agent monitors your positions — executing orders when event conditions trigger.

---

## Quick Start

```bash
# 1. Run the normal analysis pipeline (produces reports under results/stocks/)
python -m cli.main

# 2. Import today's decision reports into the journal
python -m cli.main journal import-scheduled --date 2026-02-22

# 3. Start the monitoring daemon (Ctrl+C to stop)
python -m cli.main journal daemon
```

That's it. The daemon wakes every 15 minutes during market hours, evaluates each thesis's trigger conditions against live prices, and fires alerts or executes orders when conditions are met.

---

## How It Works

```
Analysis Pipeline                Journal Agent
═══════════════                  ═════════════
                                 
run analysis ──→ reports/stocks/ ──→ import-scheduled
  (produces v2                       │
  decision JSON)                     ▼
                              TradeThesis in SQLite
                                     │
                                     ▼
                              daemon (every 15 min)
                                     │
                               ┌─────┴─────┐
                               │  Per thesis: │
                               │  1. Get price │
                               │  2. Eval plan │
                               │  3. Check     │
                               │     events    │
                               │  4. Decide    │
                               │  5. Execute   │
                               └─────┬─────┘
                                     │
                              alerts / orders
```

### What's a v2 Decision?

The analysis pipeline produces a `final_trade_decision.md` containing a structured JSON decision block:

```
BEGIN_DECISION_JSON
{
  "decision_version": "v2",
  "ticker": "NVDA",
  "action": "BUY",
  "execution_intent": "WAIT_FOR_TRIGGER",
  "plan_mode": "CONDITIONAL",
  "execution_plan": [
    {
      "branch_id": "profit_take",
      "conditions": {
        "price": { "close_above": 950.0 },
        "schedule": { "session_constraint": "MARKET_HOURS" }
      },
      "event_conditions": [
        { "event_key": "consecutive_closes_above_950" }
      ],
      "action_template": {
        "action": "SELL", "order_type": "LIMIT", "time_in_force": "DAY",
        "limit_price": 950.0, "quantity": 50,
        "stop_loss": 780.0, "take_profit": 950.0
      }
    },
    {
      "branch_id": "time_exit",
      "event_conditions": [
        { "event_key": "time_stop_ten_days" }
      ],
      "action_template": {
        "action": "SELL", "order_type": "MARKET", "time_in_force": "DAY",
        "quantity": 50, "stop_loss": 780.0, "take_profit": 950.0
      }
    }
  ]
}
END_DECISION_JSON
```

Each branch defines **conditions** (price targets, volume, schedule) and **event conditions** (consecutive closes, time stops) that must all be satisfied before the branch's `action_template` fires.

---

## Step-by-Step Workflow

### 1. Configure Environment

Add these to your `.env` file:

```bash
# ── Required: Alpaca (market data + execution) ──────────────
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # use paper for testing!

# ── Optional: Journal database location ─────────────────────
JOURNAL_DB_PATH=./journal/trade_journal.db        # default

# ── Optional: Tier 2 LLM evaluation ─────────────────────────
# If set, the daemon uses an LLM for ambiguous near-trigger situations.
# If not set, only rule-based evaluation runs (zero LLM cost).
JOURNAL_LLM_PROVIDER=openai                       # or anthropic, deepseek, etc.
JOURNAL_LLM_MODEL=gpt-4o-mini

# ── Optional: Execution policy ──────────────────────────────
JOURNAL_EXECUTION_ENABLED=false        # set to true for live execution
JOURNAL_EXECUTION_DRY_RUN=true         # logs decisions but does not execute
JOURNAL_EXECUTION_MIN_CONFIDENCE=70.0  # minimum confidence score to act
JOURNAL_EXECUTION_COOLDOWN_MINUTES=30  # prevent duplicate actions
JOURNAL_EXECUTION_MAX_ACTIONS_PER_DAY=5
JOURNAL_EXECUTION_ALLOW_PROFIT_TAKE=true
JOURNAL_EXECUTION_ALLOW_DEFENSIVE_EXIT=true
```

> [!CAUTION]
> **Start with `JOURNAL_EXECUTION_ENABLED=false` and `JOURNAL_EXECUTION_DRY_RUN=true`.**
> The daemon will monitor and log decisions without placing real orders.
> Only flip these after you've reviewed daemon output and trust the triggers.

### 2. Run Analysis

Run the standard analysis pipeline. This produces v2 decision reports under `results/stocks/<date>/<ticker>/reports/final_trade_decision.md`.

```bash
python -m cli.main
# Follow the prompts: select ticker(s), date, etc.
```

### 3. Import Reports

Import the decisions into the journal database:

```bash
# Preview first (dry run)
python -m cli.main journal import-scheduled --date 2026-02-22 --dry-run --verbose

# Import for real
python -m cli.main journal import-scheduled --date 2026-02-22 --verbose
```

This:
- Parses each `final_trade_decision.md` for the `BEGIN_DECISION_JSON` block
- Validates the v2 schema (ticker, action, execution plan, order types)
- Creates a `TradeThesis` in SQLite for each valid `WAIT_FOR_TRIGGER` decision
- Compiles event conditions (e.g. `"consecutive_closes_above_150"`) into deterministic checker rules via pattern matching

### 4. Verify Import

```bash
# Check status
python -m cli.main journal status

# List active theses
python -m cli.main journal theses --active

# Inspect a specific thesis
python -m cli.main journal inspect <thesis_id>
```

### 5. Start the Daemon

```bash
# Default: check every 15 minutes during market hours
python -m cli.main journal daemon

# Custom interval
python -m cli.main journal daemon --interval 5
```

The daemon:
- Wakes every `--interval` minutes during market hours
- Gets live price/volume for each active thesis
- Evaluates the v2 decision plan (Tier 0: rule-based, Tier 1: cross-tick condition tracking, Tier 2: LLM if configured)
- Fires alerts when thresholds are breached
- Makes action decisions via the execution advisor
- Passes decisions through the execution policy guardrails
- Executes orders via Alpaca (if enabled and not dry-run)

You can also trigger a single tick manually:

```bash
python -m cli.main journal check-now
```

### 6. Monitor in Real-Time

While the daemon runs, you can open a second terminal to check state:

```bash
# See unacknowledged alerts
python -m cli.main journal alerts

# See action decisions the daemon has made
python -m cli.main journal decisions --ticker NVDA

# See actual executions
python -m cli.main journal executions --ticker NVDA

# Acknowledge an alert once you've seen it
python -m cli.main journal acknowledge-alert <alert_id>
```

### 7. Review Outcomes

Once positions close:

```bash
# See closed-trade outcomes with P&L
python -m cli.main journal outcomes

# Aggregate performance metrics
python -m cli.main journal performance
```

### 8. Learn from History

The reflection agent extracts lessons from completed trades:

```bash
# List extracted lessons
python -m cli.main journal lessons

# Semantic search across lessons
python -m cli.main journal query-lessons "what caused my biggest loss?"
```

---

## Environment Variable Reference

| Variable | Default | Purpose |
|---|---|---|
| `JOURNAL_DB_PATH` | `./journal/trade_journal.db` | SQLite database location |
| `JOURNAL_LLM_PROVIDER` | *(none)* | LLM provider for Tier 2 evaluation |
| `JOURNAL_LLM_MODEL` | *(none)* | LLM model for Tier 2 evaluation |
| `JOURNAL_BACKEND_URL` | *(none)* | Override LLM API base URL |
| `JOURNAL_EXECUTION_ENABLED` | `false` | Enable live execution |
| `JOURNAL_EXECUTION_DRY_RUN` | `true` | Log decisions without executing |
| `JOURNAL_EXECUTION_MIN_CONFIDENCE` | `70.0` | Minimum confidence to act |
| `JOURNAL_EXECUTION_COOLDOWN_MINUTES` | `30` | Cooldown between actions |
| `JOURNAL_EXECUTION_MAX_ACTIONS_PER_DAY` | `5` | Daily execution cap |
| `JOURNAL_EXECUTION_ALLOW_PROFIT_TAKE` | `true` | Allow profit-take orders |
| `JOURNAL_EXECUTION_ALLOW_DEFENSIVE_EXIT` | `true` | Allow defensive exits |

---

## CLI Command Reference

All commands are under `python -m cli.main journal <command>`:

| Command | Description |
|---|---|
| `status` | Dashboard: active theses, recent alerts, scheduler info |
| `theses` | List trade theses (`--active`, `--ticker`) |
| `inspect <id>` | Full detail for a thesis: snapshots, alerts, plan |
| `import-scheduled` | Import v2 decision reports from `results/stocks/` |
| `daemon` | Start the monitoring daemon (foreground) |
| `check-now` | Run a single monitoring tick immediately |
| `alerts` | List alerts (`--all` or `--unacked`) |
| `acknowledge-alert <id>` | Dismiss an alert |
| `decisions` | List action decisions (`--ticker`, `--thesis-id`) |
| `executions` | List executed actions (`--ticker`, `--decision-id`) |
| `outcomes` | List closed-trade P&L |
| `performance` | Aggregate performance metrics |
| `lessons` | List extracted trade lessons |
| `query-lessons <query>` | Semantic search across lessons |

---

## Event Conditions

The journal agent supports the following event patterns at import time. These are compiled from the `event_key` strings in the decision JSON:

| Pattern | Example `event_key` | What it checks |
|---|---|---|
| Consecutive closes | `consecutive_closes_above_150` | N consecutive daily closes above/below a price |
| Time stop | `time_stop_eight_days` | Trade has been open for N trading days |
| Declining volume | `declining_volume`, `low_volume` | Volume ratio below threshold |
| Elevated volume | `high_volume`, `volume_surge` | Volume ratio above threshold |

> [!TIP]
> Use word-form numbers in event keys for reliable parsing (e.g. `three_consecutive_closes_above_150` instead of `3_consecutive_closes_above_150`).

Unrecognized event keys are passed to the LLM for compilation (if configured via `JOURNAL_LLM_PROVIDER`). If no LLM is configured, unrecognized events require manual confirmation.

---

## Safety Checklist

Before enabling live execution:

- [ ] Run with `JOURNAL_EXECUTION_DRY_RUN=true` for at least a few market sessions
- [ ] Review `journal decisions` output — are the triggers sensible?
- [ ] Use Alpaca **paper trading** URL first (`https://paper-api.alpaca.markets`)
- [ ] Set `JOURNAL_EXECUTION_MAX_ACTIONS_PER_DAY` to a conservative limit
- [ ] Confirm `stop_loss` and `take_profit` in every decision report branch
