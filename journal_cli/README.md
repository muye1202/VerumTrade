# Journal Scripts

This directory contains utility scripts for managing the trading journal daemon.
Runtime artifacts (DB, scheduler logs, lessons, execution logs) are stored under `journal_cli/`.

## Scripts

### `start_journal_daemon.py`
Start the journal scheduler daemon with reflection enabled. This monitors active positions, auto-pulls missing live brokerage positions into journal theses on each tick, and records outcomes.

**Usage:**
```bash
python scripts/journal/start_journal_daemon.py
```

### `sync_positions_to_journal.py`
Bootstrap/backfill utility to sync existing Alpaca positions into the journal database. Creates theses for positions opened before the journal system was activated, and can be used for recovery if the daemon was not running.

**Usage:**
```bash
python scripts/journal/sync_positions_to_journal.py
```

### `update_trade_dates.py`
Update trade dates in the journal database based on Alpaca order history. Fixes inaccurate trade dates for synced positions.

**Usage:**
```bash
# Dry run (show what would be updated)
python scripts/journal/update_trade_dates.py --dry-run

# Actually update the database
python scripts/journal/update_trade_dates.py
```

### `check_journal.py`
Simple utility to inspect the journal database contents.

**Usage:**
```bash
python scripts/journal/check_journal.py
```

### `import_scheduled_reports.py`
Import canonical v2 scheduled-order reports from `results/stocks/{date}` into the journal database.

**Usage:**
```bash
# Dry run preview
python journal_cli/import_scheduled_reports.py --date 2026-02-20 --dry-run --verbose

# Apply import to journal DB
python journal_cli/import_scheduled_reports.py --date 2026-02-20

# JSON output for automation
python journal_cli/import_scheduled_reports.py --date 2026-02-20 --json
```

## Requirements

All scripts require:
- Alpaca API credentials in `.env` file
- Journal database at `./journal_cli/journal/trade_journal.db`
- Python packages: `opentrace`, `alpaca-py`, `python-dotenv`
