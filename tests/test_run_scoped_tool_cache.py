import asyncio
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


def test_tool_cache_key_normalizes_argument_order_and_numeric_strings():
    from tradingagents.agents.utils.agent_runtime.tool_cache import make_tool_cache_key

    a = make_tool_cache_key(
        "get_indicators",
        {"symbol": "AMD", "look_back_days": "90", "indicator": "rsi"},
    )
    b = make_tool_cache_key(
        "get_indicators",
        {"indicator": "rsi", "look_back_days": 90, "symbol": "AMD"},
    )

    assert a == b


def test_cache_aware_tool_node_reuses_result_without_reinvoking_tool():
    from tradingagents.agents.utils.agent_runtime.tool_cache import create_cache_aware_tool_node

    calls = {"count": 0}

    @tool
    async def sample_vendor_tool(symbol: str, look_back_days: int) -> str:
        """Return sample vendor data."""
        calls["count"] += 1
        return f"{symbol}:{look_back_days}:payload"

    node = create_cache_aware_tool_node([sample_vendor_tool])
    first_state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sample_vendor_tool",
                        "args": {"symbol": "AMD", "look_back_days": "90"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_result_cache": {},
        "tool_cache_metrics": {},
    }

    first = asyncio.run(node(first_state))
    second_state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sample_vendor_tool",
                        "args": {"look_back_days": 90, "symbol": "AMD"},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_result_cache": first["tool_result_cache"],
        "tool_cache_metrics": first["tool_cache_metrics"],
    }

    second = asyncio.run(node(second_state))

    assert calls["count"] == 1
    assert first["messages"][0].content == "AMD:90:payload"
    assert second["messages"][0].content == "AMD:90:payload"
    assert second["messages"][0].tool_call_id == "call-2"
    assert second["tool_cache_metrics"]["hits"] == 1
    assert second["tool_cache_metrics"]["misses"] == 1


def test_cache_aware_tool_node_captures_vendor_telemetry_from_threaded_tools(monkeypatch):
    import tradingagents.dataflows.interface as interface
    from tradingagents.agents.utils.agent_runtime.tool_cache import create_cache_aware_tool_node

    def fake_vendor(symbol, start, end):
        return f"{symbol}:{start}:{end}"

    monkeypatch.setattr(
        interface,
        "VENDOR_METHODS",
        {
            **interface.VENDOR_METHODS,
            "get_stock_data": {"fake_vendor": fake_vendor},
        },
    )
    monkeypatch.setattr(
        interface,
        "get_config",
        lambda: {
            "data_vendors": {"core_stock_apis": "fake_vendor"},
            "tool_vendors": {},
            "context_budget_mode": "off",
        },
    )

    @tool
    async def threaded_route_tool(symbol: str) -> str:
        """Route through the vendor layer in a worker thread."""
        return await asyncio.to_thread(
            interface.route_to_vendor,
            "get_stock_data",
            symbol,
            "2026-05-01",
            "2026-05-06",
        )

    node = create_cache_aware_tool_node([threaded_route_tool])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "threaded_route_tool",
                        "args": {"symbol": "AMD"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_result_cache": {},
        "tool_cache_metrics": {},
        "vendor_telemetry": [],
    }

    result = asyncio.run(node(state))

    assert result["messages"][0].content == "AMD:2026-05-01:2026-05-06"
    assert len(result["vendor_telemetry"]) == 1
    assert result["vendor_telemetry"][0]["successful_vendor"] == "fake_vendor"
