import pytest


def test_route_to_vendor_records_fallback_telemetry(monkeypatch):
    import tradingagents.dataflows.interface as interface

    def primary_impl(*args, **kwargs):
        raise RuntimeError("primary unavailable")

    def fallback_impl(symbol, start, end):
        return f"{symbol},{start},{end}"

    monkeypatch.setattr(
        interface,
        "VENDOR_METHODS",
        {
            **interface.VENDOR_METHODS,
            "get_stock_data": {
                "primary_vendor": primary_impl,
                "fallback_vendor": fallback_impl,
            },
        },
    )
    monkeypatch.setattr(
        interface,
        "get_config",
        lambda: {
            "data_vendors": {"core_stock_apis": "primary_vendor"},
            "tool_vendors": {},
            "context_budget_mode": "off",
        },
    )

    interface.clear_vendor_telemetry()

    assert interface.route_to_vendor("get_stock_data", "AMD", "2026-05-01", "2026-05-06") == (
        "AMD,2026-05-01,2026-05-06"
    )

    events = interface.pop_vendor_telemetry()

    assert len(events) == 1
    event = events[0]
    assert event["method"] == "get_stock_data"
    assert event["category"] == "core_stock_apis"
    assert event["configured_vendors"] == ["primary_vendor"]
    assert event["successful_vendor"] == "fallback_vendor"
    assert event["vendor_attempt_count"] == 2
    assert [a["vendor"] for a in event["attempts"]] == ["primary_vendor", "fallback_vendor"]
    assert event["attempts"][0]["status"] == "error"
    assert event["attempts"][1]["status"] == "success"
    assert event["result_count"] == 1
    assert event["result_chars"] > 0

