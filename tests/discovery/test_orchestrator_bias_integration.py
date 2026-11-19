from __future__ import annotations

import pytest

from verumtrade.agents.discovery.intelligence_integration import IntelligenceDrivenRecommender
from verumtrade.agents.discovery.intelligence.pipeline_orchestrator import IntelligenceScanner
from verumtrade.agents.discovery.theme_engine.models import ThemeExposureCandidate


class _DummyLLM:
    def invoke(self, _messages):
        class _R:
            content = "{}"
        return _R()


def test_orchestrator_applies_stage0_and_stage2_bias(monkeypatch):
    scanner = IntelligenceScanner(llm=_DummyLLM(), config={})
    captured = {"stage0": None, "stage2": None}

    monkeypatch.setattr(
        scanner.pre_stage0_builder,
        "build",
        lambda trade_date: ({"trade_date": trade_date, "cache_metrics": {}}, {"computed": [], "skipped_unavailable": [], "failed_runtime": []}),
    )
    monkeypatch.setattr(
        "verumtrade.agents.discovery.intelligence.pipeline_orchestrator.build_llm_bias_profile",
        lambda **kwargs: {
            "regime_label": "TRENDING",
            "risk_posture": "RISK_ON",
            "preferred_tracks": ["enricher"],
            "stage0_overrides": {"min_avg_dollar_volume_20d": 12000000},
            "stage2_weight_tilts": {"technical_momentum": 1.2},
            "scan_notes": "test",
        },
    )

    def fake_build_numeric_universe(trade_date, excluded_tickers=None, stage0_overrides=None):
        captured["stage0"] = stage0_overrides
        return ["AAPL"]

    monkeypatch.setattr(scanner.technical_scanner, "build_numeric_universe", fake_build_numeric_universe)
    monkeypatch.setattr(scanner.stage1_enricher, "enrich_universe", lambda universe, trade_date: [])
    monkeypatch.setattr(scanner.technical_scanner, "scan_numeric_filter", lambda universe, trade_date: [])

    def fake_score_and_filter(scorecards, trade_date, weight_tilts=None, **kwargs):
        captured["stage2"] = weight_tilts
        return []

    monkeypatch.setattr(scanner.stage2_scorer, "score_and_filter", fake_score_and_filter)

    scanner.scan_with_prefilter_universe(
        trade_date="2026-02-19",
        excluded_tickers=[],
        discovery_track="enricher",
    )
    assert captured["stage0"]["min_avg_dollar_volume_20d"] == 12000000
    assert captured["stage2"]["technical_momentum"] == 1.2


def test_recommender_preserves_stage0_runtime_error(monkeypatch):
    recommender = IntelligenceDrivenRecommender(
        deep_llm=_DummyLLM(),
        quick_llm=_DummyLLM(),
        config={},
    )
    scanner = recommender.scanner

    monkeypatch.setattr(
        scanner.pre_stage0_builder,
        "build",
        lambda trade_date: (
            {"trade_date": trade_date, "cache_metrics": {}},
            {"computed": [], "skipped_unavailable": [], "failed_runtime": []},
        ),
    )
    monkeypatch.setattr(
        "verumtrade.agents.discovery.intelligence.pipeline_orchestrator.build_llm_bias_profile",
        lambda **kwargs: {
            "regime_label": "NEUTRAL",
            "risk_posture": "NEUTRAL",
            "preferred_tracks": ["enricher"],
            "stage0_overrides": {},
            "stage2_weight_tilts": {},
            "policy": {
                "universe": {
                    "allocation": {"max_tickers": {"enricher": 10}},
                    "sector_weights": {},
                },
                "scoring": {},
                "anomaly_scan": {},
            },
        },
    )
    monkeypatch.setattr(
        scanner.technical_scanner,
        "build_numeric_universe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Missing Alpaca credentials for universe scan.")
        ),
    )
    monkeypatch.setattr(
        scanner.theme_scanner,
        "scan",
        lambda trade_date: [
            ThemeExposureCandidate(
                theme="GLP-1 supply chain",
                bottleneck="Fill-finish and aseptic manufacturing",
                ticker="NVO",
                exposure_type="direct",
                exposure_confidence=0.93,
                evidence=["Seeded direct exposure"],
                theme_acceleration=0.70,
                freshness_date=trade_date,
            )
        ],
    )
    monkeypatch.setattr(
        scanner.exposure_scorer,
        "score",
        lambda candidates, **kwargs: candidates,
    )
    monkeypatch.setattr(
        "verumtrade.agents.discovery.intelligence.pipeline_orchestrator.build_ohlcv_cache",
        lambda **kwargs: ({}, {}),
    )
    monkeypatch.setattr(
        scanner.stage1_enricher,
        "enrich_universe",
        lambda universe, trade_date, ohlcv_cache=None: [],
    )
    monkeypatch.setattr(
        scanner.technical_scanner,
        "scan_numeric_filter",
        lambda universe, trade_date: [],
    )
    monkeypatch.setattr(
        scanner.stage2_scorer,
        "score_and_filter",
        lambda **kwargs: [],
    )

    with pytest.raises(RuntimeError, match="Missing Alpaca credentials"):
        recommender.recommend(
            trade_date="2026-06-05",
            excluded_tickers=[],
            discovery_track="enricher",
        )


def test_discovery_failure_events_are_visible():
    from api.routes.discovery import _discovery_failure_events
    from verumtrade.graph.stock_discovery import DiscoveryResult

    result = DiscoveryResult(
        tickers=[],
        report="Discovery failed: Missing Alpaca credentials for universe scan.",
        trade_date="2026-06-05",
        success=False,
        error="Missing Alpaca credentials for universe scan. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY.",
    )

    events = _discovery_failure_events(result)

    assert events == [
        {
            "event": "system",
            "content": (
                "Pipeline error: Missing Alpaca credentials for universe scan. "
                "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY."
            ),
        },
        {
            "event": "error",
            "content": (
                "Discovery aborted: Missing Alpaca credentials for universe scan. "
                "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY."
            ),
        },
    ]
