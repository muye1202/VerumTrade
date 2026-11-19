from __future__ import annotations

from verumtrade.agents.discovery.intelligence.market_context_snapshot import (
    PreStage0IntelligenceBuilder,
)


def test_pre_stage0_availability_matrix_strict_skip():
    availability = PreStage0IntelligenceBuilder._build_indicator_availability()
    assert "computed" in availability
    assert "skipped_unavailable" in availability
    assert "fedwatch_probabilities" in availability["skipped_unavailable"]
    assert "hy_oas_and_ccc_oas" in availability["skipped_unavailable"]
