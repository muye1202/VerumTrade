"""Tests for the Tier-4 deterministic positioning-risk gate (compute_positioning_gate)."""

from __future__ import annotations

from verumtrade.agents.utils.market_data.pullback_vulnerability import compute_positioning_gate


def _vuln(rating="CRITICAL", score=84.0, drivers=None):
    return {
        "rating": rating,
        "score": score,
        "drivers": drivers if drivers is not None else ["Parabolic +258% YTD", "Crowded momentum"],
    }


def test_gate_fires_on_critical_vuln_and_riskoff_tape():
    gate = compute_positioning_gate(_vuln(), {"risk_off": True, "rate_impulse": "RATES_UP"})
    assert gate["triggered"] is True
    assert gate["severity"] == "CRITICAL"
    assert "risk-off tape" in gate["warning_text"]
    assert "rising-rate impulse" in gate["warning_text"]
    assert "Parabolic" in gate["warning_text"]  # drivers surfaced


def test_gate_silent_when_tape_is_calm():
    # HIGH vuln but no fragile-tape factor -> gate does not fire.
    gate = compute_positioning_gate(
        _vuln(rating="HIGH", score=55.0),
        {"risk_off": False, "rate_impulse": "RATES_FLAT"},
    )
    assert gate["triggered"] is False
    assert gate["warning_text"] == ""


def test_gate_silent_when_vuln_low():
    gate = compute_positioning_gate(
        {"rating": "MEDIUM", "score": 40.0},
        {"risk_off": True, "rate_impulse": "RATES_UP"},
    )
    assert gate["triggered"] is False


def test_high_vuln_single_factor_is_high_severity():
    gate = compute_positioning_gate(_vuln(rating="HIGH", score=55.0), {"risk_off": True})
    assert gate["triggered"] is True
    assert gate["severity"] == "HIGH"


def test_high_vuln_two_factors_escalates_to_critical():
    gate = compute_positioning_gate(
        _vuln(rating="HIGH", score=58.0),
        {"risk_off": True, "rate_impulse": "RATES_UP"},
    )
    assert gate["severity"] == "CRITICAL"
    assert len(gate["tape_factors"]) >= 2


def test_foreign_market_stress_is_a_tape_factor():
    gate = compute_positioning_gate(
        _vuln(),
        {"foreign_markets": {"korea": {"ret_1d_pct": -5.1, "ret_5d_pct": -6.0}}},
    )
    assert gate["triggered"] is True
    assert any("foreign-market stress" in f for f in gate["tape_factors"])


def test_oil_spike_is_a_tape_factor():
    gate = compute_positioning_gate(_vuln(), {"oil": {"brent_5d_pct": 6.2}})
    assert gate["triggered"] is True
    assert any("oil" in f for f in gate["tape_factors"])


def test_empty_inputs_degrade_gracefully():
    assert compute_positioning_gate({}, {})["triggered"] is False
    assert compute_positioning_gate(None, None)["triggered"] is False  # type: ignore[arg-type]
