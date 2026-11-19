"""Regression: the decision trace must report the judge's real action, not the first action word.

The risk manager builds the decision trace before ``final_trade_decision_structured`` is in
state. The old fallback took the first BUY/SELL/HOLD in the prose, so a HOLD decision that
said "SELL is invalid" was traced as SELL (seen in the MU 2026-06-23 example run).
"""

import json

import pytest

from verumtrade.agents.utils.agent_runtime.evidence_graph import (
    build_decision_trace,
    infer_decision_action,
)

HOLD_DECISION_TEXT = (
    "Narrative and decision rationale\n\n"
    "- Portfolio rules: no existing MU position assumed; SELL is invalid.\n"
    "- Recommendation: WAIT_FOR_TRIGGER conditional BUY — do not buy now.\n\n"
    "BEGIN_DECISION_JSON\n"
    + json.dumps({"decision_version": "v2", "ticker": "MU", "action": "HOLD", "execution_intent": "wait_for_trigger"})
    + "\nEND_DECISION_JSON"
)


def test_trace_uses_embedded_decision_json_over_prose():
    trace = build_decision_trace({"final_trade_decision": HOLD_DECISION_TEXT}, HOLD_DECISION_TEXT)
    assert trace["decision"]["action"] == "HOLD"


@pytest.mark.parametrize(
    ("structured", "text", "expected"),
    [
        ({"action": "BUY"}, HOLD_DECISION_TEXT, "BUY"),
        (None, HOLD_DECISION_TEXT, "HOLD"),
        (None, "SELL is invalid here.\nFINAL TRANSACTION PROPOSAL: **BUY**", "BUY"),
        (None, "No SELL is allowed?\n- **Action**: HOLD", "HOLD"),
        (None, "SELL is invalid since there is no position. We BUY on the breakout.", "BUY"),
        (None, "I recommend to SELL now.", "SELL"),
        (None, "", ""),
    ],
)
def test_infer_decision_action(structured, text, expected):
    assert infer_decision_action(structured, text) == expected
