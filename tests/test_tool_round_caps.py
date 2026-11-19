from langchain_core.messages import AIMessage

from verumtrade.graph.conditional_logic import ConditionalLogic


def _state(rounds, total=None):
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sample_tool",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_round_counts": rounds,
        "tool_call_total": sum(rounds.values()) if total is None else total,
    }


def test_conditional_logic_allows_rounds_below_per_analyst_cap():
    logic = ConditionalLogic(max_tool_calls_per_analyst=4, max_tool_calls_total=50)

    route = logic.should_continue_social(_state({"social": 3}))

    assert route == "tools_social"


def test_conditional_logic_blocks_at_per_analyst_cap():
    logic = ConditionalLogic(max_tool_calls_per_analyst=4, max_tool_calls_total=50)

    route = logic.should_continue_social(_state({"social": 4}))

    assert route == "Force Finalize Social"


def test_conditional_logic_zero_per_analyst_cap_disables_per_analyst_limit():
    logic = ConditionalLogic(max_tool_calls_per_analyst=0, max_tool_calls_total=50)

    route = logic.should_continue_social(_state({"social": 20}, total=20))

    assert route == "tools_social"


def test_conditional_logic_zero_total_cap_disables_total_limit():
    logic = ConditionalLogic(max_tool_calls_per_analyst=0, max_tool_calls_total=0)

    route = logic.should_continue_social(_state({"social": 20}, total=200))

    assert route == "tools_social"
