from __future__ import annotations

from verumtrade.agents.discovery.intelligence.pipeline_models import Stage1EnrichmentScorecard
from verumtrade.agents.discovery.intelligence.candidate_scoring import Stage2Scorer


def _base_scorecard(ticker: str) -> Stage1EnrichmentScorecard:
    return Stage1EnrichmentScorecard(
        ticker=ticker,
        price=50.0,
        roc_20d=6.0,
        rs_vs_spy_20d=1.0,
        adx=28.0,
        volume_ratio=1.2,
        vs_sma50_pct=2.0,
        vs_sma200_pct=5.0,
        avg_dollar_volume_20d=15_000_000.0,
        earnings_beat_rate_4q=75.0,
        options_unusual_score=0.0,
        short_interest_pct_float=8.0,
        days_to_cover=2.0,
        data_quality_flags=[],
    )


def test_stage2_options_score_uses_0_100_scale(monkeypatch):
    scorer = Stage2Scorer(config={})
    monkeypatch.setattr(scorer, "_ticker_to_sector_etf", lambda _ticker: None)

    low = _base_scorecard("AAA")
    low.options_unusual_score = 10.0
    high = _base_scorecard("BBB")
    high.options_unusual_score = 100.0

    out = scorer.score_and_filter([low, high], trade_date="2026-02-20")
    by_ticker = {c.ticker: c for c in out}
    assert by_ticker["BBB"].options_flow_score > by_ticker["AAA"].options_flow_score
    assert by_ticker["BBB"].composite_score > by_ticker["AAA"].composite_score


def test_stage2_min_candidates_relaxation_applies(monkeypatch):
    scorer = Stage2Scorer(
        config={
            "stage2_scoring": {
                "output": {
                    "min_candidates": 2,
                    "max_candidates": 5,
                    "min_candidates_relaxation": ["disable_sma50_requirement"],
                }
            }
        }
    )
    monkeypatch.setattr(scorer, "_ticker_to_sector_etf", lambda _ticker: None)

    a = _base_scorecard("AAA")
    b = _base_scorecard("BBB")
    # Both fail initial require_above_sma50 hard filter.
    a.vs_sma50_pct = -0.5
    b.vs_sma50_pct = -1.0

    out = scorer.score_and_filter([a, b], trade_date="2026-02-20")
    meta = scorer.get_last_run_metadata()
    assert len(out) == 2
    assert "disable_sma50_requirement" in (meta.get("filter_relaxations_applied") or [])


def test_stage2_rv_shock_noise_filter_blocks_unless_breakout_exception(monkeypatch):
    scorer = Stage2Scorer(
        config={
            "stage2_scoring": {
                "breadth": {"enabled": False},
                "noise": {"rv_shock_ratio_threshold": 1.8},
                "output": {"min_candidates": 1, "max_candidates": 20},
            }
        }
    )
    monkeypatch.setattr(scorer, "_ticker_to_sector_etf", lambda _ticker: None)

    normal_cards = []
    for i in range(12):
        sc = _base_scorecard(f"N{i:02d}")
        sc.breakout_efficiency = 0.2
        sc.rv5_pct = 12.0
        sc.rv20_pct = 10.0
        normal_cards.append(sc)

    shock = _base_scorecard("SHOCK")
    shock.rv5_pct = 30.0
    shock.rv20_pct = 10.0
    shock.breakout_efficiency = 0.0

    out1 = scorer.score_and_filter([*normal_cards, shock], trade_date="2026-02-20")
    assert "SHOCK" not in {c.ticker for c in out1}

    shock.breakout_efficiency = 3.0
    out2 = scorer.score_and_filter([*normal_cards, shock], trade_date="2026-02-20")
    assert "SHOCK" in {c.ticker for c in out2}


def test_stage2_whipsaw_penalty_reduces_composite(monkeypatch):
    scorer = Stage2Scorer(config={"stage2_scoring": {"breadth": {"enabled": False}}})
    monkeypatch.setattr(scorer, "_ticker_to_sector_etf", lambda _ticker: None)

    calm = _base_scorecard("CALM")
    calm.whipsaw_count_20 = 2
    calm.trend_quality_score = 70.0

    choppy = _base_scorecard("CHOP")
    choppy.whipsaw_count_20 = 14
    choppy.trend_quality_score = 70.0

    out = scorer.score_and_filter([calm, choppy], trade_date="2026-02-20")
    by_ticker = {c.ticker: c for c in out}
    assert by_ticker["CALM"].composite_score > by_ticker["CHOP"].composite_score
