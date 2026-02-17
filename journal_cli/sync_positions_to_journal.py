"""
Sync existing Alpaca positions into the journal database.

This script captures theses for positions that were opened before the journal
system was activated.
"""

import sys
import os
from datetime import datetime
from pathlib import Path

# Add project root to Python path so we can import tradingagents
sys.path.insert(0, str(Path(__file__).parent.parent))

# Try to load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # If python-dotenv not installed, try to load .env manually
    # Look for .env in project root (two levels up from this script)
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
    exit(1)

from tradingagents.execution import AlpacaExecutor
from tradingagents.agents.journal import JournalStore
from tradingagents.agents.journal.models import TradeThesis, ThesisStatus

def sync_positions():
    """Sync existing Alpaca positions to journal as theses."""
    executor = AlpacaExecutor(paper=True)
    store = JournalStore("./journal/trade_journal.db")
    
    print("Fetching live positions from Alpaca...")
    # Use the trading_client directly to get all positions
    try:
        positions_raw = executor.trading_client.get_all_positions()
        # Convert Alpaca position objects to dicts
        positions = []
        for p in positions_raw:
            positions.append({
                "symbol": str(p.symbol),
                "qty": str(p.qty),
                "avg_entry_price": str(p.avg_entry_price),
                "current_price": str(getattr(p, "current_price", p.avg_entry_price)),
            })
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return

    if not positions:
        print("No live positions found in Alpaca")
        return

    print(f"\nFound {len(positions)} positions:")
    for p in positions:
        print(f"  - {p['symbol']}: {p['qty']} shares @ ${float(p['avg_entry_price']):.2f}")

    print("\nChecking which need to be added to journal...")
    synced_count = 0

    for position in positions:
        ticker = position["symbol"]

        # Check if already tracked
        existing = store.get_theses_by_ticker(ticker)
        active_existing = [t for t in existing if t.status == ThesisStatus.ACTIVE.value]

        if active_existing:
            print(f"✓ {ticker} already tracked in journal (thesis {active_existing[0].id})")
            continue

        # Create a basic thesis for this position
        entry_price = float(position["avg_entry_price"])
        current_price = float(position.get("current_price", entry_price))
        qty = int(float(position["qty"]))

        # Simple stop loss and target (adjust as needed)
        stop_loss = entry_price * 0.95  # 5% stop
        target_1 = entry_price * 1.10   # 10% target

        thesis = TradeThesis(
            ticker=ticker,
            trade_date=datetime.now().strftime("%Y-%m-%d"),
            action="BUY" if qty > 0 else "SELL",
            conviction=70.0,  # Default conviction
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            risk_reward_ratio=(target_1 - entry_price) / (entry_price - stop_loss),
            time_horizon_label="swing",
            holding_days_planned=10,
            catalyst="Position opened before journal activation",
            regime="unknown",
            key_risks="Manually synced - no original analysis",
            quantity=abs(qty),
            market_analyst_summary="[Synced from existing position]",
            final_decision_text=f"Synced existing {ticker} position",
            status=ThesisStatus.ACTIVE.value,
        )

        store.save_thesis(thesis)
        print(f"✓ Created thesis for {ticker} (ID: {thesis.id})")
        synced_count += 1

    print(f"\n✅ Synced {synced_count} position(s) to journal")
    print(f"📊 Total active theses: {len(store.get_active_theses())}")
    print("\nThe daemon will now monitor these positions!")

if __name__ == "__main__":
    sync_positions()
