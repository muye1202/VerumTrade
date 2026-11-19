from __future__ import annotations

import sys
import types

from verumtrade.agents.discovery.intelligence.track_a_enrichment import Stage1BatchEnricher


def _make_ohlcv_csv(bars: int = 90, start: float = 100.0, step: float = 0.8) -> str:
    lines = ["Date,Open,High,Low,Close,Volume,VWAP"]
    px = start
    for i in range(bars):
        px = px + step
        high = px * 1.01
        low = px * 0.99
        vol = 1_000_000 + i * 10_000
        vwap = px * 0.998
        lines.append(f"2026-01-{(i % 28) + 1:02d},{px:.4f},{high:.4f},{low:.4f},{px:.4f},{vol},{vwap:.4f}")
    return "\n".join(lines)


def test_stage1_price_block_computes_trend_quality_fields(monkeypatch):
    csv_stock = _make_ohlcv_csv()
    csv_spy = _make_ohlcv_csv(start=400.0, step=0.3)

    def fake_route_to_vendor(method, symbol, *args):
        if method == "get_stock_data":
            return csv_spy if str(symbol).upper() == "SPY" else csv_stock
        if method == "get_indicators":
            indicator = str(args[0]) if args else ""
            if indicator in {"close_50_sma", "close_200_sma"}:
                return "value: 120.0"
            if indicator == "adx":
                return "\n".join([f"{20+i}: {24.0 + (i % 3)}" for i in range(20)])
            if indicator == "boll_ub":
                return "value: 160.0"
            if indicator == "boll_lb":
                return "value: 110.0"
            return "value: 0.0"
        raise AssertionError(f"Unexpected call: {method}")

    fake_interface = types.ModuleType("verumtrade.dataflows.interface")
    fake_interface.route_to_vendor = fake_route_to_vendor
    monkeypatch.setitem(sys.modules, "verumtrade.dataflows.interface", fake_interface)

    enricher = Stage1BatchEnricher(config={})
    spy_roc, spy_prices = enricher._fetch_spy_context("2026-02-20", ohlcv_cache={})
    out = enricher._fetch_price_and_technical_block(
        ticker="TEST",
        trade_date="2026-02-20",
        spy_roc_20d=spy_roc,
        spy_prices=spy_prices,
        ohlcv_cache={},
    )
    assert out["trend_quality_score"] > 0.0
    assert out["rv5_pct"] >= 0.0
    assert out["rv20_pct"] >= 0.0
    assert "whipsaw_count_20" in out
    assert "breakout_efficiency" in out
