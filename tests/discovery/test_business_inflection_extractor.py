from __future__ import annotations

from verumtrade.agents.discovery.intelligence.business_inflection import (
    BusinessInflectionExtractor,
)


def test_extracts_revenue_and_margin_inflection_from_latest_periods():
    extractor = BusinessInflectionExtractor(config={})
    fundamentals_by_ticker = {
        "INFLECT": {
            "latest_periods": [
                {
                    "period_end": "2026-03-31",
                    "total_revenue": 160.0,
                    "gross_margin": 0.46,
                    "operating_margin": 0.18,
                    "operating_cashflow_margin": 0.21,
                    "net_cash": 40.0,
                    "source": "unit",
                },
                {
                    "period_end": "2025-12-31",
                    "total_revenue": 120.0,
                    "gross_margin": 0.39,
                    "operating_margin": 0.10,
                    "operating_cashflow_margin": 0.11,
                    "net_cash": -5.0,
                    "source": "unit",
                },
            ]
        }
    }

    signals = extractor.extract_from_fundamentals(
        ["INFLECT"],
        fundamentals_by_ticker=fundamentals_by_ticker,
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.ticker == "INFLECT"
    assert signal.inflection_score > 80.0
    assert "revenue_acceleration" in signal.inflection_types
    assert "margin_expansion" in signal.inflection_types
    assert "cashflow_margin_expansion" in signal.inflection_types
    assert signal.metrics["revenue_growth_qoq_pct"] == 33.33
    assert any("Revenue grew" in item for item in signal.evidence)
