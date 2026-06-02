"""
Sync existing Alpaca positions into the journal database.

This script captures theses for positions that were opened before the journal
system was activated.
"""

import sys
import os
from pathlib import Path

# Add project root to Python path so we can import opentrace
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
JOURNAL_DIR = SCRIPT_DIR / "journal"
EXECUTION_LOG_DIR = SCRIPT_DIR / "execution_logs"

sys.path.insert(0, str(PROJECT_ROOT))

# Try to load .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # If python-dotenv not installed, try to load .env manually
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip("'\"")

# Check for credentials
if not os.getenv("APCA_API_KEY_ID") and not os.getenv("ALPACA_API_KEY"):
    print("ERROR: Alpaca API credentials not found in environment variables.")
    print("Please set either:")
    print("  - APCA_API_KEY_ID and APCA_API_SECRET_KEY")
    print("  - OR ALPACA_API_KEY and ALPACA_SECRET_KEY")
    print("\nChecked .env file location:", Path(__file__).parent.parent.parent / ".env")
    raise SystemExit(1)

from opentrace.execution import AlpacaExecutor
from opentrace.agents.journal import JournalStore
from opentrace.agents.journal.portfolio.portfolio_sync import sync_missing_positions


def sync_positions() -> None:
    """Sync existing Alpaca positions to journal as theses."""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    os.makedirs(EXECUTION_LOG_DIR, exist_ok=True)

    executor = AlpacaExecutor(paper=True, log_dir=str(EXECUTION_LOG_DIR))
    store = JournalStore(str(JOURNAL_DIR / "trade_journal.db"))

    print("Fetching live positions from Alpaca...")
    result = sync_missing_positions(store=store, executor=executor)

    seen = int(result.get("positions_seen", 0) or 0)
    created = int(result.get("created", 0) or 0)
    skipped = int(result.get("skipped_existing", 0) or 0)
    created_tickers = list(result.get("created_tickers") or [])
    errors = list(result.get("errors") or [])

    print(f"Found {seen} live position(s)")
    print(f"Created {created} new thesis/theses")
    print(f"Skipped {skipped} already-tracked ticker(s)")
    if created_tickers:
        print("Created tickers:", ", ".join(created_tickers))

    if errors:
        print("\nErrors during sync:")
        for err in errors:
            print(f"  - {err}")

    print(f"\nTotal active theses: {len(store.get_active_theses())}")
    print("The daemon will now monitor these positions.")


if __name__ == "__main__":
    sync_positions()
