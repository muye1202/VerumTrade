from datetime import datetime

import pytest

from tradingagents.agents.journal.models import ThesisStatus, TradeThesis
from tradingagents.agents.journal.portfolio_sync import sync_missing_positions


class _StoreStub:
    def __init__(self):
        self.saved = []

    def get_active_thesis_by_ticker(self, ticker):
        ticker_u = str(ticker or "").upper()
        for thesis in reversed(self.saved):
            if thesis.ticker.upper() == ticker_u and thesis.status == ThesisStatus.ACTIVE.value:
                return thesis
        return None

    def save_thesis(self, thesis):
        self.saved.append(thesis)


class _ExecutorStub:
    def __init__(self, positions):
        self._positions = positions

    def get_portfolio_summary(self):
        return {"positions": list(self._positions)}


class _ExecutorRaises:
    def get_portfolio_summary(self):
        raise RuntimeError("boom")


def test_sync_creates_missing_live_position():
    store = _StoreStub()
    executor = _ExecutorStub(
        [{"symbol": "AAPL", "qty": "10", "avg_entry_price": "100.0", "market_value": "1050.0"}]
    )

    summary = sync_missing_positions(store=store, executor=executor, now=datetime(2026, 2, 19, 12, 0, 0))

    assert summary["positions_seen"] == 1
    assert summary["created"] == 1
    assert summary["skipped_existing"] == 0
    assert summary["created_tickers"] == ["AAPL"]

    thesis = store.get_active_thesis_by_ticker("AAPL")
    assert thesis is not None
    assert thesis.status == ThesisStatus.ACTIVE.value
    assert thesis.action == "BUY"
    assert thesis.stop_loss == pytest.approx(95.0)
    assert thesis.target_1 == pytest.approx(110.0)


def test_sync_skips_existing_active_ticker():
    store = _StoreStub()
    store.save_thesis(
        TradeThesis(
            ticker="MSFT",
            trade_date="2026-02-19",
            action="BUY",
            entry_price=300.0,
            status=ThesisStatus.ACTIVE.value,
        )
    )

    executor = _ExecutorStub(
        [{"symbol": "MSFT", "qty": "5", "avg_entry_price": "300.0", "market_value": "1500.0"}]
    )
    summary = sync_missing_positions(store=store, executor=executor)

    assert summary["positions_seen"] == 1
    assert summary["created"] == 0
    assert summary["skipped_existing"] == 1
    assert summary["created_tickers"] == []


def test_sync_handles_partial_errors_and_keeps_processing():
    store = _StoreStub()
    executor = _ExecutorStub(
        [
            {"symbol": "NVDA", "qty": "3", "avg_entry_price": "120.0", "market_value": "360.0"},
            {"symbol": "", "qty": "2", "avg_entry_price": "10.0", "market_value": "20.0"},
        ]
    )

    summary = sync_missing_positions(store=store, executor=executor)

    assert summary["positions_seen"] == 2
    assert summary["created"] == 1
    assert summary["skipped_existing"] == 0
    assert summary["created_tickers"] == ["NVDA"]
    assert len(summary["errors"]) == 1


def test_sync_short_position_uses_side_aware_defaults():
    store = _StoreStub()
    executor = _ExecutorStub(
        [{"symbol": "TSLA", "qty": "-4", "avg_entry_price": "200.0", "market_value": "-800.0"}]
    )

    summary = sync_missing_positions(store=store, executor=executor)

    assert summary["created"] == 1
    thesis = store.get_active_thesis_by_ticker("TSLA")
    assert thesis is not None
    assert thesis.action == "SELL"
    assert thesis.stop_loss == pytest.approx(210.0)
    assert thesis.target_1 == pytest.approx(180.0)


def test_sync_executor_missing_or_unavailable_returns_zero_created():
    store = _StoreStub()

    no_exec = sync_missing_positions(store=store, executor=None)
    bad_exec = sync_missing_positions(store=store, executor=_ExecutorRaises())

    assert no_exec["positions_seen"] == 0
    assert no_exec["created"] == 0
    assert no_exec["errors"] == []
    assert bad_exec["positions_seen"] == 0
    assert bad_exec["created"] == 0
    assert bad_exec["errors"] == []
