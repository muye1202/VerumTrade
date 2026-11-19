import json
from pathlib import Path

from verumtrade.agents.journal.core.models import ThesisStatus, TradeThesis
from verumtrade.agents.journal.ingestion.report_import import import_scheduled_reports
from verumtrade.agents.journal.core.store import JournalStore


def _make_store(tmp_path: Path) -> JournalStore:
    db_path = tmp_path / "journal.db"
    return JournalStore(db_path=db_path)


def _write_report(
    base: Path,
    *,
    date: str,
    ticker: str,
    payload: dict,
    prelude: str = "Decision\n\n",
) -> None:
    out = base / date / ticker / "reports"
    out.mkdir(parents=True, exist_ok=True)
    text = (
        f"{prelude}"
        "BEGIN_DECISION_JSON\n"
        f"{json.dumps(payload, indent=2)}\n"
        "END_DECISION_JSON\n"
    )
    (out / "final_trade_decision.md").write_text(text, encoding="utf-8")


def _v2_payload(ticker: str) -> dict:
    return {
        "decision_version": "v2",
        "ticker": ticker,
        "plan_mode": "conditional",
        "execution_plan": [
            {
                "branch_id": "breakout",
                "priority": 1,
                "conditions": {
                    "price": {"close_above": 101.0},
                    "schedule": {
                        "valid_from": "2026-02-24",
                        "valid_to": "2026-03-07",
                        "session_constraint": "MARKET_HOURS",
                    },
                },
                "event_conditions": [],
                "action_template": {
                    "action": "BUY",
                    "quantity": 25,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "extended_hours": False,
                    "limit_price": 101.5,
                    "stop_price": None,
                    "trail_percent": 1.5,
                    "trail_price": None,
                    "stop_loss": 95.0,
                    "take_profit": 112.0,
                    "position_size_pct": 0.05,
                    "time_horizon": "2-4 weeks",
                    "confidence": "MEDIUM",
                    "rationale": "Test branch",
                },
            }
        ],
        "default_action": "breakout",
        "time_horizon": "2-4 weeks",
        "confidence": "MEDIUM",
        "rationale": "Wait for trigger.",
        "action": "HOLD",
        "order_type": "MARKET",
        "limit_price": None,
        "quantity": None,
        "position_size_pct": None,
        "stop_loss": 95.0,
        "take_profit": 112.0,
        "execution_intent": "wait_for_trigger",
        "override_reason": None,
    }


def _v1_payload(ticker: str) -> dict:
    return {
        "decision_version": "v1",
        "ticker": ticker,
        "action": "BUY",
        "quantity": 1,
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "extended_hours": False,
        "limit_price": None,
        "stop_price": None,
        "trail_percent": None,
        "trail_price": None,
        "stop_loss": 10.0,
        "take_profit": 12.0,
        "position_size_pct": None,
        "time_horizon": "1-2 weeks",
        "confidence": "LOW",
        "rationale": "v1",
        "execution_intent": "act_now",
        "override_reason": None,
    }


def test_import_creates_new_v2_thesis(tmp_path: Path):
    store = _make_store(tmp_path)
    root = tmp_path / "results" / "stocks"
    _write_report(root, date="2026-02-20", ticker="AKAM", payload=_v2_payload("AKAM"))

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=False)

    assert summary["tickers_scanned"] == 1
    assert summary["imported"] == 1
    assert summary["created"] == 1
    assert summary["updated"] == 0
    thesis = store.get_active_thesis_by_ticker("AKAM")
    assert thesis is not None
    assert thesis.status == ThesisStatus.ACTIVE.value
    assert thesis.decision_plan_json is not None
    assert json.loads(thesis.decision_plan_json)["decision_version"] == "v2"


def test_import_updates_existing_active_ticker_and_preserves_execution_fields(tmp_path: Path):
    store = _make_store(tmp_path)
    existing = TradeThesis(
        ticker="AKAM",
        trade_date="2026-02-18",
        action="BUY",
        status=ThesisStatus.ACTIVE.value,
        entry_price=100.25,
        entry_price_source="filled_avg_price",
        entry_price_pending=False,
        order_id="abc123",
        quantity=100,
    )
    store.save_thesis(existing)

    root = tmp_path / "results" / "stocks"
    _write_report(root, date="2026-02-20", ticker="AKAM", payload=_v2_payload("AKAM"))

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=False)

    assert summary["imported"] == 1
    assert summary["updated"] == 1
    all_akam = store.get_theses_by_ticker("AKAM")
    assert len(all_akam) == 1
    thesis = all_akam[0]
    assert thesis.trade_date == "2026-02-20"
    assert thesis.entry_price == 100.25
    assert thesis.entry_price_source == "filled_avg_price"
    assert thesis.order_id == "abc123"
    assert thesis.quantity == 100
    assert thesis.decision_plan_json is not None


def test_import_skips_when_report_missing(tmp_path: Path):
    store = _make_store(tmp_path)
    root = tmp_path / "results" / "stocks"
    (root / "2026-02-20" / "MSFT").mkdir(parents=True, exist_ok=True)

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=False)

    assert summary["tickers_scanned"] == 1
    assert summary["skipped"] == 1
    assert summary["imported"] == 0
    assert store.get_active_thesis_by_ticker("MSFT") is None


def test_import_skips_invalid_json_block(tmp_path: Path):
    store = _make_store(tmp_path)
    root = tmp_path / "results" / "stocks"
    out = root / "2026-02-20" / "RNG" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "final_trade_decision.md").write_text("no decision json here", encoding="utf-8")

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=False)

    assert summary["skipped"] == 1
    assert summary["imported"] == 0
    assert store.get_active_thesis_by_ticker("RNG") is None


def test_import_skips_non_v2_decisions(tmp_path: Path):
    store = _make_store(tmp_path)
    root = tmp_path / "results" / "stocks"
    _write_report(root, date="2026-02-20", ticker="NET", payload=_v1_payload("NET"))

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=False)

    assert summary["skipped"] == 1
    assert summary["imported"] == 0
    assert store.get_active_thesis_by_ticker("NET") is None


def test_import_dry_run_reports_counts_without_mutation(tmp_path: Path):
    store = _make_store(tmp_path)
    root = tmp_path / "results" / "stocks"
    _write_report(root, date="2026-02-20", ticker="AKAM", payload=_v2_payload("AKAM"))
    _write_report(root, date="2026-02-20", ticker="NET", payload=_v1_payload("NET"))
    (root / "2026-02-20" / "RNG").mkdir(parents=True, exist_ok=True)

    summary = import_scheduled_reports(store, "2026-02-20", root, dry_run=True)

    assert summary["tickers_scanned"] == 3
    assert summary["imported"] == 1
    assert summary["created"] == 1
    assert summary["updated"] == 0
    assert summary["skipped"] == 2
    assert store.get_active_thesis_by_ticker("AKAM") is None
