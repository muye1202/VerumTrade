from __future__ import annotations

from verumtrade.agents.discovery.intelligence.pipeline_orchestrator import IntelligenceScanner


def test_dual_track_split_not_head_tail_bias():
    scanner = IntelligenceScanner(llm=None, config={})
    ordered = [f"T{i:02d}" for i in range(10)]
    allocation = {
        "max_tickers": {"dual_track_total": 10},
        "dual_track_split": {"enricher": 0.5, "anomaly_scan": 0.5},
    }
    track_a, track_b = scanner._dual_track_universe_split(ordered, allocation)
    assert len(track_a) == 5
    assert len(track_b) == 5
    assert track_a != ordered[:5]
    assert track_b != ordered[-5:]
    assert sorted(track_a + track_b) == sorted(ordered)


def test_neutral_sector_order_is_deterministic_for_trade_date():
    scanner = IntelligenceScanner(llm=None, config={})
    universe = ["CCC", "AAA", "BBB", "DDD"]
    weights = {"XLK": 1.0, "XLF": 1.0}
    o1 = scanner._order_universe_by_sector_weights(universe, weights, trade_date="2026-02-20")
    o2 = scanner._order_universe_by_sector_weights(universe, weights, trade_date="2026-02-20")
    assert o1 == o2
    assert sorted(o1) == sorted(["AAA", "BBB", "CCC", "DDD"])
