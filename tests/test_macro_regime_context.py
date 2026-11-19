"""Tests for the Tier-0 macro/regime context bus (verumtrade.agents.utils.market_data.macro_regime)."""

from __future__ import annotations

import pytest

from verumtrade.agents.utils.market_data.macro_regime import (
    build_macro_regime_context,
    extract_macro_events,
    format_macro_regime_markdown,
    summarize_macro_regime,
)
from verumtrade.schemas.catalyst_events import MacroEventRecord


def _synthetic_snapshot() -> dict:
    """Mirror the shape produced by PreStage0IntelligenceBuilder.build()."""
    return {
        "trade_date": "2026-06-11",
        "index_regime": {
            "indices": {
                "SPY": {
                    "returns_pct": {"1d": -1.6, "5d": -3.2, "20d": -1.0, "63d": 5.0, "ytd": 8.0},
                    "distance_to_dma_pct": {"to_20dma_pct": -2.0, "to_50dma_pct": -1.0, "to_200dma_pct": 4.0},
                    "above_all_smas": False,
                    "realized_vol_annualized_pct": {"5d": 22.0, "20d": 18.0},
                },
                "QQQ": {"returns_pct": {"5d": -4.0}, "distance_to_dma_pct": {}, "above_all_smas": False},
            },
            "risk_off_flag": True,
        },
        "vol_options": {"vix": {"level": 24.0, "returns_pct": {"1d": 9.3}}},
        "rates_macro": {"rate_impulse": "RATES_UP", "curve_slopes": {"10y_minus_3m": 0.35}},
        "cross_asset": {
            "oil_brent": {"returns_pct": {"5d": 6.2}},
            "oil_wti": {"returns_pct": {"5d": 5.5}},
        },
        "sector_factor": {
            "sector_heatmap": {
                "XLK": {"returns_pct": {"5d": -4.5}, "rs_vs_spy_pct": {"5d": -1.3}},
                "XLE": {"returns_pct": {"5d": 3.0}, "rs_vs_spy_pct": {"5d": 6.2}},
                "XLU": {"returns_pct": {"5d": 0.5}, "rs_vs_spy_pct": {"5d": 3.7}},
                "XLY": {"returns_pct": {"5d": -5.0}, "rs_vs_spy_pct": {"5d": -1.8}},
            },
            "factor_spreads_20d_pct": {
                "momentum_minus_spy": 5.5,
                "growth_minus_value": 3.0,
                "small_minus_large": -2.0,
                "lowvol_minus_spy": 1.0,
            },
        },
        "calendar": {
            "opex_week_flag": True,
            "quarter_end_flag": False,
            "month_end_flag": False,
            "earnings_season_proxy": {"intensity_label": "high"},
        },
        "global_news": {"headlines_markdown": "## Headlines\n- CPI hot\n- Oil up"},
    }


def test_summarize_extracts_compact_fields():
    regime = summarize_macro_regime(_synthetic_snapshot())
    assert regime["risk_off"] is True
    assert regime["rate_impulse"] == "RATES_UP"
    assert regime["spx"]["ret_1d_pct"] == -1.6
    assert regime["spx"]["above_all_smas"] is False
    assert regime["spx"]["dist_to_50dma_pct"] == -1.0
    assert regime["ndx"]["ret_5d_pct"] == -4.0
    assert regime["vix"]["level"] == 24.0
    assert regime["oil"]["brent_5d_pct"] == 6.2
    assert regime["factor_spreads_20d_pct"]["momentum_minus_spy"] == 5.5
    # Highest RS-vs-SPY sector leads; lowest lags.
    assert regime["sector_leaders_laggards"]["leaders"][0] == "XLE"
    assert "XLY" in regime["sector_leaders_laggards"]["laggards"]


def test_summary_line_reflects_active_flags():
    regime = summarize_macro_regime(_synthetic_snapshot())
    summary = regime["summary"]
    assert "RISK-OFF" in summary
    assert "rates RATES_UP" in summary
    assert "VIX 24.0" in summary
    assert "Brent +6.2%/5d" in summary
    assert "momentum-crowded" in summary


def test_extract_macro_events_fires_expected_signals():
    regime = summarize_macro_regime(_synthetic_snapshot())
    names = " || ".join(ev["event_name"] for ev in regime["macro_events"])
    assert "risk-off" in names
    assert "rate impulse up" in names
    assert "Oil spike" in names
    assert "VIX" in names
    assert "Crowded momentum factor" in names
    assert "Sector under distribution" in names
    assert "Options-expiration week" in names
    # Quarter-end flag is off in the synthetic snapshot.
    assert "Quarter-end" not in names


def test_macro_events_roundtrip_through_schema():
    regime = summarize_macro_regime(_synthetic_snapshot())
    assert regime["macro_events"], "expected at least one derived macro event"
    for ev in regime["macro_events"]:
        record = MacroEventRecord.from_dict(ev)
        assert record.event_name == ev["event_name"]
        assert 0.0 <= record.relevance_to_ticker <= 1.0
        assert isinstance(record.affected_sectors, list)


def test_empty_inputs_degrade_gracefully():
    assert summarize_macro_regime({}) == {}
    assert summarize_macro_regime(None) == {}  # type: ignore[arg-type]
    assert extract_macro_events({}) == []
    assert format_macro_regime_markdown({}) == ""
    assert format_macro_regime_markdown(None) == ""  # type: ignore[arg-type]


def test_format_markdown_contains_key_sections():
    md = format_macro_regime_markdown(summarize_macro_regime(_synthetic_snapshot()))
    assert "Market Regime Context" in md
    assert "Tape:" in md
    assert "VIX 24.0" in md
    assert "Derived macro/regime events:" in md


def test_build_respects_disable_flag():
    out = build_macro_regime_context("2026-06-11", config={"enable_macro_regime_context": False})
    assert out == {}


def test_build_uses_provided_snapshot_without_network():
    out = build_macro_regime_context(
        "2026-06-11",
        config={"enable_macro_regime_context": True},
        snapshot=_synthetic_snapshot(),
    )
    assert out["risk_off"] is True
    assert "RISK-OFF" in out["summary"]
    assert out["macro_events"]


def test_build_swallows_errors_and_returns_empty():
    # A snapshot that triggers an internal error path still yields {} (never raises).
    out = build_macro_regime_context(
        "2026-06-11",
        config={"enable_macro_regime_context": True},
        snapshot={"index_regime": "not-a-dict"},
    )
    assert isinstance(out, dict)


# --- Tier-3B foreign-market / FX channel -----------------------------------

def _foreign_stressed_snapshot() -> dict:
    """Korea-shock-shaped: EWY craters 1d, others calm (mirrors the May-2026 KOSPI -5%)."""
    return {
        "trade_date": "2026-05-12",
        "index_regime": {"indices": {}, "risk_off_flag": False},
        "foreign_markets": {
            "korea": {"returns_pct": {"1d": -5.1, "5d": -6.0}},
            "taiwan": {"returns_pct": {"1d": -0.5, "5d": -1.0}},
            "japan": {"returns_pct": {"1d": 0.2, "5d": 0.5}},
            "china": {"returns_pct": {"1d": -0.3, "5d": -0.8}},
        },
        "global_news": {"headlines_markdown": ""},
    }


def test_foreign_block_reduced_into_regime():
    regime = summarize_macro_regime(_foreign_stressed_snapshot())
    fm = regime["foreign_markets"]
    assert fm["korea"]["ret_1d_pct"] == -5.1
    assert fm["taiwan"]["ret_1d_pct"] == -0.5


def test_foreign_stress_derives_event_for_korea_only():
    regime = summarize_macro_regime(_foreign_stressed_snapshot())
    names = " || ".join(ev["event_name"] for ev in regime["macro_events"])
    assert "Foreign-market stress: Korea (EWY)" in names
    # Calm markets must not fire.
    assert "Japan" not in names and "China" not in names
    korea_ev = next(ev for ev in regime["macro_events"] if "Korea" in ev["event_name"])
    assert "memory" in korea_ev["affected_sectors"]


def test_foreign_markets_rendered_in_markdown():
    md = format_macro_regime_markdown(summarize_macro_regime(_foreign_stressed_snapshot()))
    assert "Foreign markets" in md
    assert "Korea (EWY)" in md


def test_no_foreign_block_is_silent():
    # The original synthetic snapshot has no foreign_markets — no foreign event, no markdown line.
    regime = summarize_macro_regime(_synthetic_snapshot())
    assert regime["foreign_markets"] == {}
    names = " || ".join(ev["event_name"] for ev in regime["macro_events"])
    assert "Foreign-market stress" not in names
