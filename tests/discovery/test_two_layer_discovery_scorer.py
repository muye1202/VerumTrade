from __future__ import annotations

from verumtrade.agents.discovery.intelligence.pipeline_models import DiscoveryEvidencePack
from verumtrade.agents.discovery.intelligence.two_layer_discovery_scoring import (
    TwoLayerDiscoveryScorer,
)


def test_two_layer_scorer_promotes_thesis_rich_early_candidate():
    early = DiscoveryEvidencePack(
        ticker="EARLY",
        evidence_score=48.0,
        theme_score=92.0,
        bottleneck_score=88.0,
        business_inflection_score=84.0,
        attention_gap_score=81.0,
        momentum_confirmation_score=55.0,
        catalyst_proximity_score=40.0,
        risk_penalty=7.0,
        evidence_bullets=["Direct theme exposure", "Business inflection"],
    )
    technical_only = DiscoveryEvidencePack(
        ticker="OLD",
        evidence_score=76.0,
        theme_score=10.0,
        business_inflection_score=0.0,
        attention_gap_score=0.0,
        momentum_confirmation_score=72.0,
        catalyst_proximity_score=0.0,
        risk_penalty=4.0,
        evidence_bullets=["Technically strong"],
    )

    scored = TwoLayerDiscoveryScorer().score([technical_only, early])

    assert [item.ticker for item in scored] == ["EARLY", "OLD"]
    assert scored[0].tier == "actionable"
    assert scored[0].thesis_score > scored[1].thesis_score
    assert scored[0].discovery_score > scored[1].discovery_score
    assert scored[1].tier in {"watchlist", "theme_candidate", "rejected"}


def test_two_layer_scorer_assigns_watchlist_to_good_thesis_without_momentum():
    pack = DiscoveryEvidencePack(
        ticker="WATCH",
        evidence_score=35.0,
        theme_score=86.0,
        business_inflection_score=77.0,
        attention_gap_score=72.0,
        momentum_confirmation_score=18.0,
        risk_penalty=5.0,
    )

    scored = TwoLayerDiscoveryScorer().score([pack])

    assert scored[0].ticker == "WATCH"
    assert scored[0].tier == "watchlist"
    assert scored[0].action == "watchlist"
    assert "momentum" in " ".join(scored[0].tier_reasons).lower()
