from opentrace.execution.decision_guard import (
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
