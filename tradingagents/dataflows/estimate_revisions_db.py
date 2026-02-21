import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class EstimateRevisionsDB:
    """
    Manages historical snapshots of consensus EPS and revenue estimates
    for calculating estimate revision momentum.
    """
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Default to a file in the same directory as this module
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "data_cache", "estimate_revisions.db")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS estimate_snapshots (
                    ticker TEXT,
                    trade_date TEXT,
                    eps_consensus REAL,
                    revenue_consensus REAL,
                    up_revisions INTEGER,
                    down_revisions INTEGER,
                    PRIMARY KEY (ticker, trade_date)
                )
            ''')
            # Index for faster historical lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_ticker_date 
                ON estimate_snapshots(ticker, trade_date)
            ''')
            conn.commit()

    def store_snapshot(self, ticker: str, trade_date: str, snapshot: Dict[str, Any]) -> None:
        """Store a single day's snapshot for a ticker."""
        eps = float(snapshot.get("eps_consensus", 0.0))
        rev = float(snapshot.get("revenue_consensus", 0.0))
        up_revs = int(snapshot.get("up_revisions", 0))
        down_revs = int(snapshot.get("down_revisions", 0))

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO estimate_snapshots 
                    (ticker, trade_date, eps_consensus, revenue_consensus, up_revisions, down_revisions)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (ticker, trade_date, eps, rev, up_revs, down_revs))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to store snapshot for {ticker} on {trade_date}: {e}")

    def get_snapshot(self, ticker: str, target_date: str) -> Optional[Dict[str, Any]]:
        """
        Get the snapshot for a ticker on or immediately before target_date.
        This allows for weekend/holiday safe lookups.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM estimate_snapshots 
                    WHERE ticker = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 1
                ''', (ticker, target_date))
                row = cursor.fetchone()
                
                if row:
                    return {
                        "trade_date": row["trade_date"],
                        "eps_consensus": row["eps_consensus"],
                        "revenue_consensus": row["revenue_consensus"],
                        "up_revisions": row["up_revisions"],
                        "down_revisions": row["down_revisions"]
                    }
        except Exception as e:
            logger.error(f"Failed to retrieve snapshot for {ticker} near {target_date}: {e}")
            
        return None

    def get_snapshot_30d_ago(self, ticker: str, current_date: str) -> Optional[Dict[str, Any]]:
        """Convenience method to get the snapshot from approximately 30 days ago."""
        dt = datetime.strptime(current_date, "%Y-%m-%d")
        target_dt = dt - timedelta(days=30)
        target_str = target_dt.strftime("%Y-%m-%d")
        
        # We look for a record <= target_str. If missing, we might want to look a bit forward?
        # Actually, looking <= target_str makes sense, it finds the most recent record *before* 30 days ago.
        # But if the DB is new, it might return a very old record if one exists, or none.
        # Let's add a lower bound so we don't return a 2-year-old record.
        lower_bound_dt = target_dt - timedelta(days=15) # look up to 45 days ago
        lower_bound_str = lower_bound_dt.strftime("%Y-%m-%d")
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM estimate_snapshots 
                    WHERE ticker = ? AND trade_date <= ? AND trade_date >= ?
                    ORDER BY trade_date DESC LIMIT 1
                ''', (ticker, current_date, lower_bound_str)) # getting closest past record within 45 days
                # Wait, we want the record closest to 30 days ago.
                # Let's get all in last 45 days, and pick closest to 30 days ago.
                cursor.execute('''
                    SELECT * FROM estimate_snapshots 
                    WHERE ticker = ? AND trade_date < ?
                    ORDER BY trade_date ASC
                ''', (ticker, current_date))
                rows = cursor.fetchall()
                if not rows:
                    return None
                    
                # Find the row closest to target_date
                best_row = None
                min_diff = float("inf")
                
                for row in rows:
                    row_dt = datetime.strptime(row["trade_date"], "%Y-%m-%d")
                    diff = abs((row_dt - target_dt).days)
                    if diff < min_diff and diff <= 15: # Must be within 15 days of the 30-day target
                        min_diff = diff
                        best_row = row
                        
                if best_row:
                    return {
                        "trade_date": best_row["trade_date"],
                        "eps_consensus": best_row["eps_consensus"],
                        "revenue_consensus": best_row["revenue_consensus"],
                        "up_revisions": best_row["up_revisions"],
                        "down_revisions": best_row["down_revisions"]
                    }
        except Exception as e:
            logger.error(f"Failed to retrieve 30d snapshot for {ticker}: {e}")
            
        return None
