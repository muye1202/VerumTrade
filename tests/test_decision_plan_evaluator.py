import json

from verumtrade.agents.journal.evaluation.decision_plan_evaluator import evaluate_decision_plan
from verumtrade.agents.journal.core.models import PositionSnapshot, TradeThesis


def _make_plan(event_required: bool = True) -> str:
    return json.dumps(
        {
            "decision_version": "v2",
            "plan_mode": "conditional",
            "execution_plan": [
                {
                    "branch_id": "breakout",
                    "priority": 1,
                    "conditions": {
                        "price": {"close_above": 80.0},
                        "schedule": {
                            "valid_from": "2026-02-26",
                            "valid_to": "2026-03-05",
                            "session_constraint": "market_hours",
                        },
                    },
                    "event_conditions": [
                        {
                            "event_key": "neutron_commentary_clean",
                            "requires_confirmation": event_required,
                            "expected_value": "true",
                        }
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
                        "rationale": "Breakout plan",
                    },
                }
            ],
        }
    )


def test_plan_does_not_trigger_without_required_event_confirmation():
    thesis = TradeThesis(ticker="RKLB", decision_plan_json=_make_plan(event_required=True))
    snapshot = PositionSnapshot(ticker="RKLB", current_price=81.2, timestamp="2026-02-27T15:00:00+00:00")
    result = evaluate_decision_plan(
        thesis=thesis,
        snapshot=snapshot,
        market_session="market_hours",
        event_confirmations={},
    )
    assert result is None


def test_plan_triggers_with_event_confirmation():
    thesis = TradeThesis(ticker="RKLB", decision_plan_json=_make_plan(event_required=True))
    snapshot = PositionSnapshot(ticker="RKLB", current_price=81.2, timestamp="2026-02-27T15:00:00+00:00")
    result = evaluate_decision_plan(
        thesis=thesis,
        snapshot=snapshot,
        market_session="market_hours",
        event_confirmations={"neutron_commentary_clean": "true"},
    )
    assert result is not None
    assert result["branch_id"] == "breakout"
    assert result["action_template"]["action"] == "BUY"


def test_plan_triggers_with_price_and_schedule_only():
    thesis = TradeThesis(
        ticker="RKLB",
        decision_plan_json=json.dumps(
            {
                "decision_version": "v2",
                "plan_mode": "conditional",
                "execution_plan": [
                    {
                        "branch_id": "simple_breakout",
                        "conditions": {
                            "price": {"close_above": 80.0},
                            "schedule": {"session_constraint": "market_hours"},
                        },
                        "action_template": {
                            "action": "BUY",
                            "quantity": 10,
                            "order_type": "LIMIT",
                            "time_in_force": "DAY",
                            "extended_hours": False,
                            "limit_price": 81.0,
                            "stop_price": None,
                            "trail_percent": None,
                            "trail_price": None,
                            "stop_loss": 72.0,
                            "take_profit": 95.0,
                            "position_size_pct": None,
                            "time_horizon": "2-4 weeks",
                            "confidence": "MEDIUM",
                            "rationale": "Simple breakout",
                        },
                    }
                ],
            }
        ),
    )
    snapshot = PositionSnapshot(ticker="RKLB", current_price=82.0, timestamp="2026-02-27T15:00:00+00:00")
    result = evaluate_decision_plan(
        thesis=thesis,
        snapshot=snapshot,
        market_session="market_hours",
    )
    assert result is not None
    assert result["branch_id"] == "simple_breakout"
