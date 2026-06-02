"""
Import canonical v2 scheduled-order reports into the journal database.

Scans:
  results/stocks/{date}/{TICKER}/reports/final_trade_decision.md

And upserts plan-only theses for journal monitoring/execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root for local imports when running as a standalone script.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from opentrace.agents.journal import JournalStore
from opentrace.agents.journal.ingestion.report_import import import_scheduled_reports


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import v2 scheduled-order ticker reports into journal trade_theses.",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Date folder under results root (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--results-root",
        default="./results/stocks",
        help="Root directory for saved stock analysis results (default: ./results/stocks).",
    )
    parser.add_argument(
        "--db",
        default="./journal_cli/journal/trade_journal.db",
        help="Journal SQLite DB path (default: ./journal_cli/journal/trade_journal.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview import without writing to database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-ticker statuses.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full summary as JSON.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    store = JournalStore(db_path=args.db)
    summary = import_scheduled_reports(
        store=store,
        date=args.date,
        results_root=args.results_root,
        dry_run=bool(args.dry_run),
    )

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    print("Scheduled report import complete")
    print(f"  date:            {summary.get('date')}")
    print(f"  date_dir:        {summary.get('date_dir')}")
    print(f"  dry_run:         {bool(summary.get('dry_run'))}")
    print(f"  tickers_scanned: {int(summary.get('tickers_scanned', 0) or 0)}")
    print(f"  imported:        {int(summary.get('imported', 0) or 0)}")
    print(f"  updated:         {int(summary.get('updated', 0) or 0)}")
    print(f"  created:         {int(summary.get('created', 0) or 0)}")
    print(f"  skipped:         {int(summary.get('skipped', 0) or 0)}")
    print(f"  errors:          {len(summary.get('errors') or [])}")
    if not args.dry_run:
        print(f"  dedup_closed:    {int(summary.get('dedup_closed', 0) or 0)}")

    errors = list(summary.get("errors") or [])
    if errors:
        print("\nErrors:")
        for err in errors[:20]:
            print(f"  - {err}")

    if args.verbose:
        print("\nPer-ticker results:")
        for item in summary.get("items") or []:
            ticker = str(item.get("ticker") or "").ljust(8)
            status = str(item.get("status") or "")
            reason = str(item.get("reason") or "")
            print(f"  {ticker} {status:8s} {reason}")
            parsed = item.get("parsed") or {}
            if parsed:
                print(
                    "    parsed: "
                    f"version={parsed.get('decision_version')} "
                    f"intent={parsed.get('execution_intent')} "
                    f"mode={parsed.get('plan_mode')} "
                    f"action={parsed.get('action')} "
                    f"branches={parsed.get('branch_count')} "
                    f"immediate={parsed.get('immediate_branch_id') or 'none'}"
                )
                branch_ids = parsed.get("branch_ids") or []
                if branch_ids:
                    print(f"    branch_ids: {', '.join(str(x) for x in branch_ids)}")
                ref = parsed.get("reference_template") or {}
                print(
                    "    mapped_for_import: "
                    f"stop_loss={ref.get('stop_loss')} "
                    f"target_1={ref.get('target_1')} "
                    f"order_type={ref.get('order_type')} "
                    f"position_size_pct={ref.get('position_size_pct')} "
                    f"trailing_stop_pct={ref.get('trailing_stop_pct')} "
                    f"time_horizon={ref.get('time_horizon_label')} "
                    f"conviction={ref.get('conviction')}"
                )
            applied = item.get("import_applied") or {}
            if applied:
                print(
                    "    applied: "
                    f"action={applied.get('action')} "
                    f"qty={applied.get('quantity')} "
                    f"stop_loss={applied.get('stop_loss')} "
                    f"target_1={applied.get('target_1')}"
                )
            preserved = item.get("preserved_fields") or []
            if preserved:
                print(f"    preserved: {', '.join(str(x) for x in preserved)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
