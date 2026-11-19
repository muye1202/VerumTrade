from __future__ import annotations

from verumtrade.agents.discovery.intelligence.pipeline_models import MomentumScanHit
from verumtrade.agents.discovery.intelligence_integration import IntelligenceDrivenRecommender


def test_track_b_ranking_prefers_higher_normalized_strength():
    hits = [
        MomentumScanHit(
            ticker="AAA",
            scan_name="volatility_breakout",
            signal_value=90.0,
            raw_value=10.0,
            normalized_strength=90.0,
            trigger_details={"bb_width_percentile": 10.0},
        ),
        MomentumScanHit(
            ticker="BBB",
            scan_name="volatility_breakout",
            signal_value=10.0,
            raw_value=90.0,
            normalized_strength=10.0,
            trigger_details={"bb_width_percentile": 90.0},
        ),
    ]
    ranked = IntelligenceDrivenRecommender._rankings_from_track_b(hits, excluded_set=set())
    assert ranked[0]["ticker"] == "AAA"
    assert ranked[0]["composite"] > ranked[1]["composite"]
