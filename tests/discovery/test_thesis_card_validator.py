from __future__ import annotations

from verumtrade.agents.discovery.intelligence.pipeline_models import (
    DiscoveryEvidencePack,
    TwoLayerScoredCandidate,
)
from verumtrade.agents.discovery.intelligence.thesis_card_validator import (
    ThesisCardValidator,
)


def test_thesis_card_validator_builds_evidence_backed_card():
    pack = DiscoveryEvidencePack(
        ticker="CARD",
        primary_theme="AI optics",
        primary_bottleneck="optical networking",
        exposure_type="direct",
        evidence_score=64.0,
        theme_score=88.0,
        business_inflection_score=79.0,
        attention_gap_score=76.0,
        momentum_confirmation_score=68.0,
        catalyst_proximity_score=45.0,
        risk_penalty=9.0,
        evidence_bullets=[
            "Direct exposure to optical networking bottleneck",
            "Revenue and margin inflection detected",
        ],
        attention_reasons=["Low consensus penetration"],
    )
    scored = TwoLayerScoredCandidate(
        ticker="CARD",
        discovery_score=73.0,
        evidence_score=64.0,
        thesis_score=80.0,
        momentum_confirmation_score=68.0,
        attention_gap_score=76.0,
        catalyst_proximity_score=45.0,
        risk_penalty=9.0,
        tier="actionable",
        action="actionable",
        tier_reasons=["Strong thesis quality"],
        evidence_pack=pack,
    )

    cards = ThesisCardValidator().validate([scored])

    assert len(cards) == 1
    card = cards[0]
    assert card.ticker == "CARD"
    assert card.status == "actionable"
    assert "AI optics" in card.bull_thesis
    assert card.evidence[:2] == pack.evidence_bullets
    assert card.kill_conditions
    assert any("margin" in item.lower() for item in card.kill_conditions)
    assert card.to_dict()["ticker"] == "CARD"
