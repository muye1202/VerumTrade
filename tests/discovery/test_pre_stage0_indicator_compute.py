from __future__ import annotations

from datetime import datetime, timedelta

from verumtrade.agents.discovery.intelligence.market_context_snapshot import (
    PreStage0IntelligenceBuilder,
)


def _make_rows(trade_date: str, n: int, start_close: float, step: float):
    end = datetime.strptime(trade_date, "%Y-%m-%d")
    rows = []
    for i in range(n):
        dt = (end - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        close = start_close + i * step
        rows.append(
            {
                "Date": dt,
                "Open": close - 0.2,
                "High": close + 0.8,
                "Low": close - 0.8,
                "Close": close,
                "Volume": 1_000_000 + i,
            }
        )
    # Force a known overnight gap on last bar.
    rows[-1]["Open"] = rows[-2]["Close"] + 1.0
    return rows


def test_pre_stage0_indicator_compute(monkeypatch):
    trade_date = "2026-02-19"
    builder = PreStage0IntelligenceBuilder(config={})

    symbol_rows = {
        "SPY": _make_rows(trade_date, 260, 100.0, 0.5),
        "QQQ": _make_rows(trade_date, 260, 120.0, 0.45),
        "IWM": _make_rows(trade_date, 260, 90.0, 0.25),
        "DIA": _make_rows(trade_date, 260, 95.0, 0.35),
    }

    def fake_fetch_series(symbol: str, start_date: str, end_date: str):
        if symbol in symbol_rows:
            return symbol_rows[symbol]
        return _make_rows(trade_date, 120, 50.0, 0.1)

    monkeypatch.setattr(builder, "_fetch_series", fake_fetch_series)
    monkeypatch.setattr(builder, "_earnings_intensity", lambda *_: {"intensity_label": "medium"})

    snapshot, _availability = builder.build(trade_date)
    spy = snapshot["index_regime"]["indices"]["SPY"]
    assert spy["returns_pct"]["1d"] is not None
    assert spy["returns_pct"]["20d"] is not None
    assert spy["distance_to_dma_pct"]["to_50dma_pct"] is not None
    assert isinstance(spy["above_all_smas"], bool)
    assert spy["overnight_gap_pct"] is not None
    assert spy["realized_vol_annualized_pct"]["20d"] is not None
