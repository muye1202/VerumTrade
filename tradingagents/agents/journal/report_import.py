from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.agents.journal.models import ThesisStatus, TradeThesis
from tradingagents.agents.journal.store import JournalStore
from tradingagents.graph.decision_schema import (
    extract_decision_json_block,
    validate_structured_decision,
)

logger = logging.getLogger(__name__)


def import_scheduled_reports(
    store: JournalStore,
    date: str,
    results_root: str | Path = "./results/stocks",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Import canonical v2 scheduled-order decisions into journal trade_theses.
    """
    summary: Dict[str, Any] = {
        "date": str(date),
        "results_root": str(results_root),
        "date_dir": str(Path(results_root) / str(date)),
        "tickers_scanned": 0,
        "imported": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "items": [],
        "dry_run": bool(dry_run),
        "dedup_closed": 0,
    }

    try:
        datetime.strptime(str(date), "%Y-%m-%d")
    except ValueError:
        summary["errors"].append(f"invalid_date:{date}")
        return summary

    date_dir = Path(results_root) / str(date)
    if not date_dir.exists() or not date_dir.is_dir():
        summary["errors"].append(f"missing_date_dir:{date_dir}")
        return summary

    ticker_dirs = sorted(p for p in date_dir.iterdir() if p.is_dir())
    summary["tickers_scanned"] = len(ticker_dirs)

    for ticker_dir in ticker_dirs:
        ticker = str(ticker_dir.name or "").strip().upper()
        if not ticker:
            summary["skipped"] += 1
            summary["items"].append({"ticker": "", "status": "skipped", "reason": "empty_ticker_dir"})
            continue

        report_path = ticker_dir / "reports" / "final_trade_decision.md"
        if not report_path.exists():
            summary["skipped"] += 1
            summary["items"].append(
                {
                    "ticker": ticker,
                    "status": "skipped",
                    "reason": "missing_report",
                    "path": str(report_path),
                }
            )
            continue

        try:
            decision_text = report_path.read_text(encoding="utf-8")
            structured, reason = _parse_and_validate_v2(decision_text, expected_ticker=ticker)
            if structured is None:
                summary["skipped"] += 1
                summary["items"].append(
                    {
                        "ticker": ticker,
                        "status": "skipped",
                        "reason": reason or "invalid_decision",
                        "path": str(report_path),
                    }
                )
                continue

            outcome = _upsert_scheduled_thesis(
                store=store,
                trade_date=str(date),
                ticker=ticker,
                final_decision_text=decision_text,
                structured_decision=structured,
                dry_run=dry_run,
            )

            summary["imported"] += 1
            if outcome == "created":
                summary["created"] += 1
            else:
                summary["updated"] += 1
            summary["items"].append(
                {
                    "ticker": ticker,
                    "status": outcome,
                    "reason": "ok",
                    "path": str(report_path),
                }
            )
        except Exception as e:
            err = f"{ticker}:{type(e).__name__}:{e}"
            logger.warning("Scheduled report import failed for %s: %s", ticker, e, exc_info=True)
            summary["errors"].append(err)
            summary["items"].append({"ticker": ticker, "status": "error", "reason": err})

    if not dry_run:
        dedup = store.deduplicate_active_theses(executor=None)
        summary["dedup_closed"] = int((dedup or {}).get("closed_theses", 0) or 0)

    return summary


def _parse_and_validate_v2(
    decision_text: str,
    *,
    expected_ticker: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw, raw_err = extract_decision_json_block(decision_text)
    if raw_err:
        return None, "invalid_json_block"

    structured, err = validate_structured_decision(raw or {}, expected_ticker=expected_ticker)
    if err:
        return None, f"validation_error:{err}"
    if not isinstance(structured, dict):
        return None, "invalid_structured_payload"

    if str(structured.get("decision_version", "")).lower() != "v2":
        return None, "not_v2"
    if str(structured.get("execution_intent", "")).lower() != "wait_for_trigger":
        return None, "not_wait_for_trigger"

    return structured, None


def _upsert_scheduled_thesis(
    *,
    store: JournalStore,
    trade_date: str,
    ticker: str,
    final_decision_text: str,
    structured_decision: Dict[str, Any],
    dry_run: bool,
) -> str:
    existing = store.get_active_thesis_by_ticker(ticker)
    reference_template = _resolve_v2_reference_template(structured_decision) or {}
    imported_qty = _safe_int(reference_template.get("quantity"))

    if existing is None:
        thesis = TradeThesis(
            ticker=ticker,
            trade_date=trade_date,
            action=str(structured_decision.get("action") or "HOLD").upper(),
            status=ThesisStatus.ACTIVE.value,
        )
        _apply_common_import_fields(
            thesis=thesis,
            structured_decision=structured_decision,
            reference_template=reference_template,
            final_decision_text=final_decision_text,
        )
        if imported_qty is not None:
            thesis.quantity = imported_qty
        if not dry_run:
            store.save_thesis(thesis)
        return "created"

    # Update existing active thesis in place while preserving execution-origin fields.
    existing.trade_date = trade_date
    existing.action = str(structured_decision.get("action") or existing.action or "HOLD").upper()
    existing.status = ThesisStatus.ACTIVE.value

    _apply_common_import_fields(
        thesis=existing,
        structured_decision=structured_decision,
        reference_template=reference_template,
        final_decision_text=final_decision_text,
    )

    if existing.quantity is None and imported_qty is not None:
        existing.quantity = imported_qty

    if not dry_run:
        store.save_thesis(existing)
    return "updated"


def _apply_common_import_fields(
    *,
    thesis: TradeThesis,
    structured_decision: Dict[str, Any],
    reference_template: Dict[str, Any],
    final_decision_text: str,
) -> None:
    thesis.decision_plan_json = json.dumps(structured_decision, ensure_ascii=False)
    thesis.final_decision_text = _truncate(final_decision_text, 4000)
    thesis.stop_loss = _safe_float(reference_template.get("stop_loss"))
    thesis.target_1 = _safe_float(reference_template.get("take_profit"))
    thesis.order_type = _safe_str(reference_template.get("order_type"))
    thesis.position_size_pct = _safe_float(reference_template.get("position_size_pct"))
    thesis.trailing_stop_pct = _safe_float(reference_template.get("trail_percent"))
    thesis.time_horizon_label = _safe_str(
        structured_decision.get("time_horizon") or reference_template.get("time_horizon")
    )
    thesis.conviction = _confidence_to_score(structured_decision.get("confidence"))


def _resolve_v2_reference_template(structured_decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(structured_decision.get("decision_version", "")).lower() != "v2":
        return None

    plan = structured_decision.get("execution_plan") or []
    if not isinstance(plan, list):
        plan = []

    immediate_branch_id = str(structured_decision.get("immediate_branch_id") or "").strip()
    if immediate_branch_id:
        for branch in plan:
            if not isinstance(branch, dict):
                continue
            if str(branch.get("branch_id") or "").strip() == immediate_branch_id:
                tmpl = branch.get("action_template")
                if isinstance(tmpl, dict):
                    return tmpl

    default_action = structured_decision.get("default_action")
    if isinstance(default_action, dict):
        return default_action

    sorted_branches: List[Dict[str, Any]] = []
    for branch in plan:
        if isinstance(branch, dict):
            sorted_branches.append(branch)
    if not sorted_branches:
        return None
    sorted_branches.sort(key=lambda b: _safe_int(b.get("priority")) or 0)
    first_tmpl = sorted_branches[0].get("action_template")
    if isinstance(first_tmpl, dict):
        return first_tmpl
    return None


def _confidence_to_score(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if 0.0 <= v <= 100.0:
            return v
    s = str(value).strip().upper()
    if not s:
        return None
    if s == "HIGH":
        return 85.0
    if s == "MEDIUM":
        return 70.0
    if s == "LOW":
        return 55.0
    return _safe_float(value)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        f = _safe_float(value)
        return int(f) if f is not None else None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _truncate(text: Optional[str], max_len: int) -> Optional[str]:
    if not text:
        return None
    value = str(text).strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."
