from __future__ import annotations

import sys
import types

from verumtrade.agents.discovery.intelligence.track_a_enrichment import Stage1BatchEnricher
from verumtrade.agents.discovery.intelligence.technical_momentum_metrics import TechnicalMomentumScanner
from verumtrade.agents.discovery.intelligence.pipeline_utils import compute_return_pct


def _make_price_csv(start_close: float = 100.0, bars: int = 21) -> str:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for i in range(bars):
        close = start_close + i
        lines.append(f"2026-01-{i+1:02d},{close},{close},{close},{close},1000000")
    return "\n".join(lines)


def test_compute_return_pct_uses_n_plus_one_observations():
    prices = [100.0 + i for i in range(21)]  # 100..120
    ret20 = compute_return_pct(prices, 20)
    assert ret20 == 20.0


def test_stage1_spy_roc_20d_uses_correct_window(monkeypatch):
    raw_csv = _make_price_csv()

    def fake_route_to_vendor(method, symbol, start, end):
        assert method == "get_stock_data"
        return raw_csv

    fake_interface = types.ModuleType("verumtrade.dataflows.interface")
    fake_interface.route_to_vendor = fake_route_to_vendor
    monkeypatch.setitem(sys.modules, "verumtrade.dataflows.interface", fake_interface)
    enricher = Stage1BatchEnricher(config={})
    roc = enricher._fetch_spy_roc_20d("2026-02-20")
    assert roc == 20.0


def test_technical_spy_roc_20d_uses_correct_window(monkeypatch):
    raw_csv = _make_price_csv()

    def fake_route_to_vendor(method, symbol, start, end):
        assert method == "get_stock_data"
        return raw_csv

    fake_interface = types.ModuleType("verumtrade.dataflows.interface")
    fake_interface.route_to_vendor = fake_route_to_vendor
    monkeypatch.setitem(sys.modules, "verumtrade.dataflows.interface", fake_interface)
    scanner = TechnicalMomentumScanner(llm=None, config={})
    roc = scanner._fetch_spy_roc_20d("2026-02-20")
    assert roc == 20.0
