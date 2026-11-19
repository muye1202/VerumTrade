from verumtrade.graph.decision_schema import (
    extract_decision_json_block,
    validate_structured_decision,
)


def test_v1_decision_still_valid():
    payload = {
        "action": "HOLD",
        "ticker": "RKLB",
        "quantity": None,
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "extended_hours": False,
        "limit_price": None,
        "stop_price": None,
        "trail_percent": None,
        "trail_price": None,
        "stop_loss": 68.0,
        "take_profit": 95.0,
        "position_size_pct": None,
        "time_horizon": "2-4 weeks",
        "confidence": "HIGH",
        "rationale": "Wait for confirmation.",
        "decision_version": "v1",
        "execution_intent": "act_now",
        "override_reason": None,
    }
    parsed, err = validate_structured_decision(payload, expected_ticker="RKLB")
    assert err is None
    assert parsed is not None
    assert parsed["decision_version"] == "v1"
    assert parsed["action"] == "HOLD"


def test_v2_conditional_decision_validates():
    payload = {
        "decision_version": "v2",
        "ticker": "RKLB",
        "plan_mode": "conditional",
        "execution_plan": [
            {
                "branch_id": "breakout",
                "priority": 1,
                "conditions": {
                    "price": {"close_above": 80.0},
                    "schedule": {"valid_from": "2026-02-26", "session_constraint": "MARKET_HOURS"},
                },
                "event_conditions": [
                    {"event_key": "neutron_commentary_clean", "requires_confirmation": True}
                ],
                "action_template": {
                    "action": "BUY",
                    "quantity": None,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "extended_hours": False,
                    "limit_price": 81.0,
                    "stop_price": None,
                    "trail_percent": None,
                    "trail_price": None,
                    "stop_loss": 72.0,
                    "take_profit": 95.0,
                    "position_size_pct": 0.06,
                    "time_horizon": "2-4 weeks",
                    "confidence": "MEDIUM",
                    "rationale": "Breakout setup.",
                },
            }
        ],
        "default_action": None,
        "action": "HOLD",
        "execution_intent": "wait_for_trigger",
        "override_reason": None,
    }
    parsed, err = validate_structured_decision(payload, expected_ticker="RKLB")
    assert err is None
    assert parsed is not None
    assert parsed["decision_version"] == "v2"
    assert parsed["plan_mode"] == "conditional"
    assert parsed["action"] == "HOLD"
    assert len(parsed["execution_plan"]) == 1


def test_v2_rejects_contradictory_price_range():
    payload = {
        "decision_version": "v2",
        "ticker": "RKLB",
        "plan_mode": "conditional",
        "execution_plan": [
            {
                "branch_id": "bad_range",
                "conditions": {"price": {"close_above": 80.0, "close_below": 79.0}},
                "action_template": {
                    "action": "BUY",
                    "quantity": 10,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "extended_hours": False,
                    "limit_price": 80.5,
                    "stop_price": None,
                    "trail_percent": None,
                    "trail_price": None,
                    "stop_loss": 72.0,
                    "take_profit": 95.0,
                    "position_size_pct": None,
                    "time_horizon": "2-4 weeks",
                    "confidence": "MEDIUM",
                    "rationale": "Invalid.",
                },
            }
        ],
        "execution_intent": "wait_for_trigger",
    }
    parsed, err = validate_structured_decision(payload, expected_ticker="RKLB")
    assert parsed is None
    assert "contradictory range" in str(err)


def test_v1_rejects_wrong_execution_intent():
    payload = {
        "action": "BUY",
        "ticker": "RKLB",
        "quantity": 5,
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "extended_hours": False,
        "limit_price": 80.5,
        "stop_price": None,
        "trail_percent": None,
        "trail_price": None,
        "stop_loss": 72.0,
        "take_profit": 95.0,
        "position_size_pct": None,
        "time_horizon": "2-4 weeks",
        "confidence": "MEDIUM",
        "rationale": "Wrong intent",
        "decision_version": "v1",
        "execution_intent": "wait_for_trigger",
    }
    parsed, err = validate_structured_decision(payload, expected_ticker="RKLB")
    assert parsed is None
    assert "execution_intent" in str(err)


def test_extract_decision_json_uses_last_block():
    text = """
BEGIN_DECISION_JSON
{"action":"HOLD","ticker":"RKLB","decision_version":"v1"}
END_DECISION_JSON

BEGIN_DECISION_JSON
{"action":"BUY","ticker":"RKLB","decision_version":"v1"}
END_DECISION_JSON
"""
    parsed, err = extract_decision_json_block(text)
    assert err is None
    assert parsed is not None
    assert parsed["action"] == "BUY"


def test_v2_rejects_act_now_intent():
    payload = {
        "decision_version": "v2",
        "ticker": "RKLB",
        "plan_mode": "conditional",
        "execution_intent": "act_now",
        "execution_plan": [
            {
                "branch_id": "b1",
                "action_template": {
                    "action": "BUY",
                    "quantity": 5,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "extended_hours": False,
                    "limit_price": 80.5,
                    "stop_price": None,
                    "trail_percent": None,
                    "trail_price": None,
                    "stop_loss": 72.0,
                    "take_profit": 95.0,
                    "position_size_pct": None,
                    "time_horizon": "2-4 weeks",
                    "confidence": "MEDIUM",
                    "rationale": "x",
                },
            }
        ],
    }
    parsed, err = validate_structured_decision(payload, expected_ticker="RKLB")
    assert parsed is None
    assert "execution_intent" in str(err)
