from __future__ import annotations

from verumtrade.agents.discovery.intelligence.market_policy_llm import build_llm_bias_profile


class _ExplodingLLM:
    def invoke(self, _messages):
        raise AssertionError("LLM should not be called in deterministic/cached-only mode")


def test_pre_stage0_llm_can_be_disabled_even_when_llm_is_present():
    out = build_llm_bias_profile(
        llm=_ExplodingLLM(),
        trade_date="2026-02-20",
        snapshot={"x": 1},
        cache_config={"enabled": False},
        metrics={},
        allow_llm_call=False,
    )
    assert out["regime_label"] == "NEUTRAL"
    assert out["risk_posture"] == "NEUTRAL"
