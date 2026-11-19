from __future__ import annotations

from verumtrade.agents.discovery.intelligence.attention_gap import AttentionGapSignal
from verumtrade.agents.discovery.intelligence.business_inflection import (
    BusinessInflectionSignal,
)
from verumtrade.agents.discovery.intelligence.discovery_evidence_pack import (
    DiscoveryEvidencePackBuilder,
)
from verumtrade.agents.discovery.intelligence.pipeline_models import (
    Stage1EnrichmentScorecard,
    Stage2ScoredCandidate,
)
from verumtrade.agents.discovery.theme_engine.models import ThemeExposureCandidate


def test_evidence_pack_builder_merges_signals_by_ticker():
    scorecard = Stage1EnrichmentScorecard(
        ticker="PACK",
        roc_20d=8.0,
        accum_distrib_ratio_20d=2.2,
        eps_revision_breadth_30d=12.0,
        options_unusual_score=18.0,
        short_interest_pct_float=9.0,
    )
    stage2 = Stage2ScoredCandidate(
        ticker="PACK",
        composite_score=61.0,
        technical_momentum_score=72.0,
        estimate_revision_score=18.0,
        accum_distrib_score=75.0,
        stage1_scorecard=scorecard,
    )
    theme = ThemeExposureCandidate(
        theme="AI memory and storage",
        bottleneck="enterprise SSD demand",
        ticker="PACK",
        exposure_type="direct",
        exposure_confidence=0.88,
        evidence=["PACK sells into a constrained storage bottleneck"],
        why_it_matters="Direct leverage to AI storage demand.",
        theme_acceleration=0.45,
    )
    inflection = BusinessInflectionSignal(
        ticker="PACK",
        inflection_score=82.0,
        inflection_types=["revenue_acceleration", "margin_expansion"],
        evidence=["Revenue grew and margins expanded"],
        confidence=0.85,
    )
    attention = AttentionGapSignal(
        ticker="PACK",
        attention_gap_score=74.0,
        business_inflection_strength=82.0,
        theme_exposure_strength=88.0,
        price_accumulation_from_low_base=51.0,
        consensus_penetration=18.0,
        media_saturation=12.0,
        crowding_score=22.0,
        reasons=["Low consensus penetration"],
    )

    packs = DiscoveryEvidencePackBuilder().build(
        stage1_scorecards=[scorecard],
        stage2_candidates=[stage2],
        theme_candidates=[theme],
        business_inflection_signals=[inflection],
        attention_gap_signals=[attention],
    )

    assert len(packs) == 1
    pack = packs[0]
    assert pack.ticker == "PACK"
    assert pack.evidence_score == 61.0
    assert pack.theme_score > 85.0
    assert pack.business_inflection_score == 82.0
    assert pack.attention_gap_score == 74.0
    assert pack.momentum_confirmation_score >= 70.0
    assert pack.risk_penalty > 0.0
    assert pack.primary_theme == "AI memory and storage"
    assert "PACK sells into" in pack.evidence_bullets[0]
    assert pack.to_dict()["ticker"] == "PACK"
