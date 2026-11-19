from __future__ import annotations

from verumtrade.agents.discovery.intelligence.attention_gap import AttentionGapSignal
from verumtrade.agents.discovery.intelligence.business_inflection import (
    BusinessInflectionSignal,
)
from verumtrade.agents.discovery.intelligence.pipeline_models import IntelligenceResult
from verumtrade.agents.discovery.intelligence.pipeline_models import (
    DiscoveryEvidencePack,
    ThesisCard,
    TwoLayerScoredCandidate,
)
from verumtrade.agents.discovery.intelligence_integration import IntelligenceDrivenRecommender


def test_report_contains_layer0_sections():
    rec = IntelligenceDrivenRecommender(llm=None, config={})
    intelligence = IntelligenceResult(
        discovery_track="enricher",
        pre_stage0_snapshot={"trade_date": "2026-02-19", "cache_metrics": {}},
        llm_bias_profile={
            "regime_label": "TRENDING",
            "risk_posture": "RISK_ON",
            "preferred_tracks": ["anomaly_scan"],
        },
        indicator_availability={
            "computed": ["x"],
            "skipped_unavailable": ["fedwatch_probabilities"],
            "failed_runtime": [],
        },
        stage0_metrics={},
    )
    report = rec._build_report(
        intelligence=intelligence,
        rankings=[],
        trade_date="2026-02-19",
        excluded_tickers=[],
    )
    assert "## Pre-Stage-0 Snapshot" in report
    assert "## LLM Bias Profile" in report
    assert "## Skipped Indicators (Unavailable with current tools)" in report


def test_report_contains_inflection_and_attention_gap_sections():
    rec = IntelligenceDrivenRecommender(llm=None, config={})
    intelligence = IntelligenceResult(
        discovery_track="enricher",
        business_inflection_signals=[
            BusinessInflectionSignal(
                ticker="GAP",
                inflection_score=82.0,
                inflection_types=["revenue_acceleration"],
                evidence=["Revenue grew 30.0% sequentially"],
                metrics={"revenue_growth_qoq_pct": 30.0},
                confidence=0.8,
                source_type="fundamentals",
            )
        ],
        attention_gap_signals=[
            AttentionGapSignal(
                ticker="GAP",
                attention_gap_score=76.0,
                business_inflection_strength=82.0,
                theme_exposure_strength=90.0,
                price_accumulation_from_low_base=35.0,
                consensus_penetration=10.0,
                media_saturation=5.0,
                crowding_score=8.0,
                reasons=["Strong business inflection with low consensus penetration"],
            )
        ],
    )

    report = rec._build_report(
        intelligence=intelligence,
        rankings=[],
        trade_date="2026-02-19",
        excluded_tickers=[],
    )

    assert "## Business Inflection Signals" in report
    assert "GAP(82.0)" in report
    assert "## Attention Gap Signals" in report
    assert "GAP(76.0)" in report


def test_report_contains_two_layer_scores_and_thesis_cards():
    rec = IntelligenceDrivenRecommender(llm=None, config={})
    pack = DiscoveryEvidencePack(
        ticker="CARD",
        primary_theme="AI optics",
        primary_bottleneck="optical networking",
        evidence_score=64.0,
        theme_score=88.0,
        business_inflection_score=79.0,
        attention_gap_score=76.0,
        momentum_confirmation_score=68.0,
        evidence_bullets=["Direct exposure to optical networking bottleneck"],
    )
    intelligence = IntelligenceResult(
        discovery_track="enricher",
        evidence_packs=[pack],
        two_layer_candidates=[
            TwoLayerScoredCandidate(
                ticker="CARD",
                discovery_score=73.0,
                evidence_score=64.0,
                thesis_score=80.0,
                momentum_confirmation_score=68.0,
                attention_gap_score=76.0,
                tier="actionable",
                action="actionable",
                tier_reasons=["Strong thesis quality"],
                evidence_pack=pack,
            )
        ],
        thesis_cards=[
            ThesisCard(
                ticker="CARD",
                status="actionable",
                bull_thesis="CARD has direct exposure to AI optics.",
                evidence=["Direct exposure to optical networking bottleneck"],
                kill_conditions=["Revenue or margin inflection reverses."],
                confidence=0.73,
            )
        ],
    )

    report = rec._build_report(
        intelligence=intelligence,
        rankings=[],
        trade_date="2026-02-19",
        excluded_tickers=[],
    )

    assert "## Two-Layer Discovery Scores" in report
    assert "CARD: discovery 73.0" in report
    assert "tier actionable" in report
    assert "## Thesis Cards" in report
    assert "CARD has direct exposure to AI optics." in report
    assert "Revenue or margin inflection reverses." in report
