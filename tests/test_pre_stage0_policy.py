from verumtrade.agents.discovery.intelligence.market_policy_llm import (
    _sanitize_bias,
)


def test_sanitize_bias_supports_policy_shape():
    raw = {
        "schema_version": "1.0",
        "regime_label": "TRENDING",
        "risk_posture": "NEUTRAL",
        "preferred_tracks": ["enricher", "anomaly_scan"],
        "policy": {
            "universe": {
                "stage0_overrides": {
                    "min_avg_dollar_volume_20d": 9_000_000,
                    "catalyst_mode": "daily_calendar",
                    "catalyst_window_days": 9,
                },
                "sector_weights": {
                    "XLK": 1.2,
                    "XLF": 0.8,
                },
                "allocation": {
                    "max_tickers": {
                        "enricher": 120,
                        "anomaly_scan": 140,
                        "dual_track_total": 200,
                    },
                    "dual_track_split": {
                        "enricher": 0.6,
                        "anomaly_scan": 0.4,
                    },
                },
            },
            "scoring": {
                "stage2_weight_tilts": {
                    "technical_momentum": 1.4,
                    "options_flow": 0.9,
                    "sector_momentum": 1.3,
                    "short_squeeze": 0.8,
                },
                "stage2_hard_filter_overrides": {
                    "require_above_sma50": True,
                    "min_rs_vs_spy_differential": -2.5,
                },
            },
            "anomaly_scan": {
                "enabled_scans": [
                    "momentum_acceleration",
                    "rs_divergence",
                ],
                "thresholds": {
                    "momentum_acceleration_min": 1.8,
                    "rs_divergence_top_quantile": 0.92,
                },
            },
        },
    }
    out = _sanitize_bias(raw)
    assert out["schema_version"] == "1.0"
    assert "policy" in out
    assert out["stage0_overrides"]["catalyst_mode"] == "daily_calendar"
    assert out["stage2_weight_tilts"]["technical_momentum"] >= 1.1


def test_sanitize_bias_supports_legacy_shape():
    raw = {
        "regime_label": "NEUTRAL",
        "risk_posture": "RISK_OFF",
        "preferred_tracks": ["dual_track"],
        "stage0_overrides": {
            "min_avg_dollar_volume_20d": 8_000_000,
        },
        "stage2_weight_tilts": {
            "technical_momentum": 1.0,
            "options_flow": 1.0,
            "sector_momentum": 1.0,
            "short_squeeze": 1.0,
        },
    }
    out = _sanitize_bias(raw)
    assert "policy" in out
    assert "universe" in out["policy"]
    assert out["stage0_overrides"]["min_avg_dollar_volume_20d"] >= 8_000_000

