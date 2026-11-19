from __future__ import annotations

from verumtrade.agents.discovery.intelligence.attention_gap import AttentionGapDetector
from verumtrade.agents.discovery.intelligence.business_inflection import (
    BusinessInflectionSignal,
)
from verumtrade.agents.discovery.intelligence.pipeline_models import (
    Stage1EnrichmentScorecard,
)
from verumtrade.agents.discovery.theme_engine.models import ThemeExposureCandidate


def test_attention_gap_rewards_theme_and_inflection_before_revisions():
    detector = AttentionGapDetector(config={})
    scorecard = Stage1EnrichmentScorecard(
        ticker="GAP",
        roc_20d=7.0,
        accum_distrib_ratio_20d=2.0,
        eps_revision_breadth_30d=5.0,
        eps_revision_magnitude_30d=0.0,
        finnhub_sentiment_score=0.05,
        options_unusual_score=5.0,
    )
    theme = ThemeExposureCandidate(
        theme="AI memory and storage",
        bottleneck="HBM and advanced memory",
        ticker="GAP",
        exposure_type="direct",
        exposure_confidence=0.9,
        evidence=["Seed evidence"],
        theme_acceleration=0.3,
    )
    inflection = BusinessInflectionSignal(
        ticker="GAP",
        inflection_score=86.0,
        inflection_types=["revenue_acceleration", "margin_expansion"],
        evidence=["Revenue and margins improved"],
        metrics={"revenue_growth_qoq_pct": 35.0},
        confidence=0.9,
        source_type="fundamentals",
    )

    signals = detector.score(
        scorecards=[scorecard],
        theme_candidates=[theme],
        inflection_signals=[inflection],
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.ticker == "GAP"
    assert signal.attention_gap_score > 70.0
    assert signal.business_inflection_strength == 86.0
    assert signal.theme_exposure_strength == 90.0
    assert signal.consensus_penetration < 15.0
    assert any("business inflection" in reason.lower() for reason in signal.reasons)
