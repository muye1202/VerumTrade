"""
Update journal trade dates from Alpaca order history.

This script fetches historical filled orders from Alpaca and updates
the trade_date field in the journal database to reflect actual purchase dates.
"""
import sys
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# Ensure we can import from parent directories
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip("'\"")

from tradingagents.execution import AlpacaExecutor
from tradingagents.agents.journal.core.models import TradeThesis

def fetch_filled_orders(executor: AlpacaExecutor, limit: int = 500) -> List[Any]:
    """Fetch all filled orders from Alpaca."""
    try:
        # Try using GetOrdersRequest
        try:
            from alpaca.trading.requests import GetOrdersRequest
            request = GetOrdersRequest(status='closed', limit=limit)
            orders = executor.trading_client.get_orders(request)
        except Exception:
            # Fallback to kwargs-based API
            orders = executor.trading_client.get_orders(status='closed', limit=limit)
        
        # Filter to only filled orders
        filled_orders = []
        for order in orders:
            status = getattr(order, 'status', None)
            status_str = status.value if hasattr(status, 'value') else str(status or '')
            if status_str.upper() == 'FILLED':
                filled_orders.append(order)
        
        return filled_orders
    except Exception as e:
        print(f"Error fetching orders: {e}")
        return []

def match_thesis_to_order(
    thesis: TradeThesis, 
    orders: List[Any], 
    tolerance_pct: float = 0.01
) -> Optional[Any]:
    """
    Match a thesis to an Alpaca order by ticker and entry price.
    
    Returns the most recent matching order, or None if no match found.
    """
    matching_orders = []
    
    for order in orders:
        # Check ticker match
        if order.symbol.upper() != thesis.ticker.upper():
            continue
        
        # Check action match (BUY for long, SELL for short)
        side = getattr(order, 'side', None)
        side_str = side.value if hasattr(side, 'value') else str(side or '')
        if side_str.upper() != thesis.action.upper():
            continue
        
        # Check price match (within tolerance)
        filled_price = float(getattr(order, 'filled_avg_price', 0) or 0)
        if filled_price <= 0:
            continue
        
        if thesis.entry_price and thesis.entry_price > 0:
            price_diff_pct = abs(filled_price - thesis.entry_price) / thesis.entry_price
            if price_diff_pct <= tolerance_pct:
                matching_orders.append(order)
    
    if not matching_orders:
        return None
    
    # Return the most recent order
    return max(matching_orders, key=lambda o: getattr(o, 'filled_at', datetime.min))

def update_trade_dates(db_path: Path, dry_run: bool = False) -> None:
    """Update trade dates in the journal database."""
    print("Initializing Alpaca executor...")
    try:
        executor = AlpacaExecutor(paper=True)
        print("✓ Alpaca executor initialized\n")
    except Exception as e:
        print(f"Error initializing Alpaca: {e}")
        return
    
    print("Fetching filled orders from Alpaca...")
    orders = fetch_filled_orders(executor, limit=500)
    print(f"✓ Found {len(orders)} filled orders\n")
    
    if not orders:
        print("No orders to process.")
        return
    
    # Connect to database
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Get active theses
    cursor.execute("""
        SELECT id, ticker, action, entry_price, trade_date
        FROM trade_theses
        WHERE status = 'active'
    """)
    
    theses_data = cursor.fetchall()
    print(f"Found {len(theses_data)} active theses in journal\n")
    
    updated_count = 0
    skipped_count = 0
    
    for thesis_id, ticker, action, entry_price, current_trade_date in theses_data:
        # Create minimal TradeThesis object for matching
        thesis = TradeThesis(
            id=thesis_id,
            ticker=ticker,
            action=action,
            entry_price=entry_price,
            trade_date=current_trade_date
        )
        
        # Find matching order
        matching_order = match_thesis_to_order(thesis, orders, tolerance_pct=0.01)
        
        if matching_order:
            filled_at = getattr(matching_order, 'filled_at', None)
            if filled_at:
                new_trade_date = filled_at.strftime("%Y-%m-%d")
                filled_price = float(getattr(matching_order, 'filled_avg_price', 0))
                
                if new_trade_date != current_trade_date:
                    print(f"✓ {ticker}:")
                    print(f"    Current trade_date: {current_trade_date}")
                    print(f"    Matched order: {matching_order.id} filled at ${filled_price:.2f} on {new_trade_date}")
                    print(f"    → Updating to: {new_trade_date}")
                    
                    if not dry_run:
                        cursor.execute(
                            "UPDATE trade_theses SET trade_date = ? WHERE id = ?",
                            (new_trade_date, thesis_id)
                        )
                    updated_count += 1
                else:
                    print(f"○ {ticker}: Already has correct date ({current_trade_date})")
                    skipped_count += 1
            else:
                print(f"⚠ {ticker}: Matched order but no filled_at timestamp")
                skipped_count += 1
        else:
            print(f"✗ {ticker}: No matching order found (entry_price: ${entry_price})")
            skipped_count += 1
        
        print()
    
    if not dry_run:
        conn.commit()
        print(f"\n{'='*60}")
        print(f"✅ Updated {updated_count} position(s)")
        print(f"○  Skipped {skipped_count} position(s)")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"DRY RUN - Would update {updated_count} position(s)")
        print(f"DRY RUN - Would skip {skipped_count} position(s)")
        print(f"{'='*60}")
    
    conn.close()

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Update journal trade dates from Alpaca order history"
    )
    parser.add_argument(
        "--db",
        type=str,
        default="./journal_cli/journal/trade_journal.db",
        help="Path to journal database (default: ./journal_cli/journal/trade_journal.db)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes"
    )
    
    args = parser.parse_args()
    
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / args.db
    
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        return
    
    if args.dry_run:
        print("=== DRY RUN MODE - No changes will be made ===\n")
    
    update_trade_dates(db_path, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
