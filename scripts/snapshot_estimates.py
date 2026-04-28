import argparse
import sys
import logging
from datetime import datetime
import yfinance as yf

from tradingagents.dataflows.estimate_revisions_db import EstimateRevisionsDB
from tradingagents.agents.discovery.intelligence.pipeline_models import DEFAULT_SCREENING_UNIVERSE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SnapshotEstimates")

def fetch_estimate_snapshot(ticker: str) -> dict:
    ticker_obj = yf.Ticker(ticker)
    
    eps_consensus = 0.0
    revenue_consensus = 0.0
    up_revisions = 0
    down_revisions = 0
    
    # Process earnings estimate for EPS consensus and Revisions
    earnings_estimate = getattr(ticker_obj, "earnings_estimate", None)
    if hasattr(earnings_estimate, "empty") and not earnings_estimate.empty:
        candidate_rows = []
        try:
            for idx, row in earnings_estimate.iterrows():
                if "0q" in str(idx).lower() or "current" in str(idx).lower():
                    candidate_rows.append(row.to_dict())
        except Exception:
            pass
        if not candidate_rows:
            try:
                candidate_rows = [earnings_estimate.iloc[0].to_dict()]
            except Exception:
                pass
        
        for row_map in candidate_rows:
            # Revisions
            for k, v in row_map.items():
                if "up" in str(k).lower():
                    try: up_revisions = int(v)
                    except: pass
                if "down" in str(k).lower():
                    try: down_revisions = int(v)
                    except: pass
                if str(k).lower() in ["avg", "avgestimate", "estimate", "epsestimate"]:
                    try: eps_consensus = float(v)
                    except: pass
                    
            if eps_consensus != 0.0:
                break

    # Process revenue estimate
    rev_estimate = getattr(ticker_obj, "revenue_estimate", None)
    if hasattr(rev_estimate, "empty") and not rev_estimate.empty:
        candidate_rows = []
        try:
            for idx, row in rev_estimate.iterrows():
                if "0q" in str(idx).lower() or "current" in str(idx).lower():
                    candidate_rows.append(row.to_dict())
        except Exception:
            pass
        if not candidate_rows:
            try:
                candidate_rows = [rev_estimate.iloc[0].to_dict()]
            except Exception:
                pass
        
        for row_map in candidate_rows:
            for k, v in row_map.items():
                if str(k).lower() in ["avg", "avgestimate", "estimate", "revestimate", "revenueestimate"]:
                    try: 
                        # sometimes revenue is in millions or billions, handle string conversion safely
                        if isinstance(v, str) and "B" in v:
                            revenue_consensus = float(v.replace("B", "")) * 1e9
                        elif isinstance(v, str) and "M" in v:
                            revenue_consensus = float(v.replace("M", "")) * 1e6
                        else:
                            revenue_consensus = float(v)
                    except: pass
                    
            if revenue_consensus != 0.0:
                break
                
    return {
        "eps_consensus": eps_consensus,
        "revenue_consensus": revenue_consensus,
        "up_revisions": up_revisions,
        "down_revisions": down_revisions
    }

def main():
    parser = argparse.ArgumentParser(description="Snapshot current earnings estimates for tickers.")
    parser.add_argument("--tickers", type=str, help="Comma separated list of tickers, defaults to DEFAULT_SCREENING_UNIVERSE")
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = DEFAULT_SCREENING_UNIVERSE
        
    db = EstimateRevisionsDB()
    today = datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f"Snapshotting estimates for {len(tickers)} tickers on {today}...")
    
    success = 0
    for idx, ticker in enumerate(tickers):
        try:
            logger.info(f"[{idx+1}/{len(tickers)}] Fetching {ticker}...")
            snapshot = fetch_estimate_snapshot(ticker)
            db.store_snapshot(ticker, today, snapshot)
            success += 1
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            
    logger.info(f"Finished. Successfully stored snapshot for {success}/{len(tickers)} tickers.")

if __name__ == "__main__":
    main()
