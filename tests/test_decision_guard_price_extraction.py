from verumtrade.execution.decision_guard import (
    build_market_snapshot,
    extract_analysis_price_hint,
    extract_last_close_from_market_report,
)


APP_STYLE_REPORT = """
APP is in a clear intermediate downtrend. Price is -44% off its 60D high ($738), sitting at $412.

| Level | Price | Role |
|---|---:|---|
| Current price | $412.00 | Last close |
| 10 EMA resistance | $419.28 | First overhead hurdle |
"""


def test_extract_analysis_price_hint_ignores_percent_tokens():
    assert extract_analysis_price_hint(APP_STYLE_REPORT) == 412.0


def test_extract_last_close_from_market_report_parses_table_style():
    assert extract_last_close_from_market_report(APP_STYLE_REPORT) == 412.0


def test_build_market_snapshot_uses_report_anchor_when_quote_missing():
    snap = build_market_snapshot(
        symbol="APP",
        market_report=APP_STYLE_REPORT,
        quote=None,
        structured_decision=None,
    )
    assert snap["reference_price"] == 412.0
    assert snap["analysis_price_hint"] == 412.0
    assert snap["source"] == "analysis_fallback"


def test_build_market_snapshot_flags_anchor_conflict():
    snap = build_market_snapshot(
        symbol="APP",
        market_report=APP_STYLE_REPORT,
        quote={"reference_price": 50.0, "source": "test_quote"},
        structured_decision=None,
        snapshot_source="executor_quote_first",
    )
    assert snap["reference_price"] == 50.0
    assert snap["analysis_price_hint"] == 412.0
    assert snap["price_anchor_conflict"] is True


def test_extract_price_hint_parses_comma_formatted_prices():
    report = "SNDK is testing resistance. Current price $1,694.98 is near the 20D high."

    assert extract_analysis_price_hint(report) == 1694.98


def test_market_snapshot_does_not_use_decision_levels_as_reference_price():
    snap = build_market_snapshot(
        symbol="SNDK",
        market_report="No reliable market price was available.",
        quote=None,
        structured_decision={"stop_loss": 92.5, "take_profit": 114.0},
    )

    assert snap["reference_price"] is None
    assert snap["analysis_price_hint"] is None
    assert snap["price_anchor_missing"] is True
