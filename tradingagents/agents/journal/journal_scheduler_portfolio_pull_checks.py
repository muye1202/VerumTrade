from tradingagents.agents.journal import scheduler as scheduler_module
from tradingagents.agents.journal.scheduler import JournalScheduler


class _StoreStub:
    def get_thesis(self, _thesis_id):
        return None

    def get_active_theses(self):
        return []

    def get_alerts(self, **_kwargs):
        return []


class _MonitorStub:
    def __init__(self, summary, recorder=None):
        self._summary = summary
        self.executor = object()
        self._recorder = recorder

    def run_tick(self):
        if self._recorder is not None:
            self._recorder.append("monitor")
        return dict(self._summary)


class _OutcomeStub:
    def __init__(self, recorder=None):
        self._recorder = recorder

    def record_all_closed(self):
        if self._recorder is not None:
            self._recorder.append("outcome")
        return []


def _base_monitor_summary():
    return {
        "timestamp": "2026-02-19T12:00:00",
        "theses_checked": 0,
        "snapshots_taken": 0,
        "alerts_fired": 0,
        "positions_closed": 0,
        "actions_evaluated": 0,
        "actions_recommended": 0,
        "actions_blocked": 0,
        "actions_executed": 0,
        "actions_failed": 0,
        "errors": [],
    }


def test_scheduler_runs_portfolio_pull_before_monitor(monkeypatch):
    order = []

    def _sync(*, store, executor):
        order.append("pull")
        return {
            "positions_seen": 2,
            "created": 1,
            "skipped_existing": 1,
            "errors": [],
            "created_tickers": ["AAPL"],
        }

    monkeypatch.setattr(scheduler_module, "sync_missing_positions", _sync)

    scheduler = JournalScheduler(store=_StoreStub(), executor=None)
    scheduler.monitor = _MonitorStub(_base_monitor_summary(), recorder=order)
    scheduler.outcome_recorder = _OutcomeStub(recorder=order)

    summary = scheduler._execute_tick()

    assert order == ["pull", "monitor", "outcome"]
    assert summary["portfolio_pull_seen"] == 2
    assert summary["portfolio_pull_created"] == 1
    assert summary["portfolio_pull_skipped_existing"] == 1
    assert summary["portfolio_pull_created_tickers"] == ["AAPL"]
    assert summary["portfolio_pull_errors"] == []


def test_scheduler_portfolio_pull_error_is_non_fatal(monkeypatch):
    def _sync(*, store, executor):
        raise RuntimeError("sync failed")

    monkeypatch.setattr(scheduler_module, "sync_missing_positions", _sync)

    scheduler = JournalScheduler(store=_StoreStub(), executor=None)
    scheduler.monitor = _MonitorStub(_base_monitor_summary())
    scheduler.outcome_recorder = _OutcomeStub()

    summary = scheduler._execute_tick()

    assert summary["portfolio_pull_seen"] == 0
    assert summary["portfolio_pull_created"] == 0
    assert summary["portfolio_pull_skipped_existing"] == 0
    assert summary["portfolio_pull_created_tickers"] == []
    assert len(summary["portfolio_pull_errors"]) == 1
    assert "sync failed" in summary["portfolio_pull_errors"][0]
    assert any("Portfolio pull:" in err for err in summary["errors"])
    assert summary["outcomes_recorded"] == 0


def test_scheduler_summary_includes_pull_fields_when_monitor_fails(monkeypatch):
    def _sync(*, store, executor):
        return {
            "positions_seen": 1,
            "created": 1,
            "skipped_existing": 0,
            "errors": ["minor warning"],
            "created_tickers": ["MSFT"],
        }

    class _MonitorRaises:
        executor = object()

        def run_tick(self):
            raise RuntimeError("monitor failure")

    monkeypatch.setattr(scheduler_module, "sync_missing_positions", _sync)

    scheduler = JournalScheduler(store=_StoreStub(), executor=None)
    scheduler.monitor = _MonitorRaises()
    scheduler.outcome_recorder = _OutcomeStub()

    summary = scheduler._execute_tick()

    assert summary["portfolio_pull_seen"] == 1
    assert summary["portfolio_pull_created"] == 1
    assert summary["portfolio_pull_created_tickers"] == ["MSFT"]
    assert summary["portfolio_pull_errors"] == ["minor warning"]
    assert any("monitor failure" in err for err in summary["errors"])
    assert any("Portfolio pull: minor warning" in err for err in summary["errors"])
