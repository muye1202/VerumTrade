from __future__ import annotations

import shutil
from pathlib import Path

from verumtrade.agents.discovery.intelligence.market_policy_llm import build_llm_bias_profile


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, _messages):
        return _FakeResp(self._content)


def test_pre_stage0_llm_contract_clamps_and_defaults():
    cache_dir = Path("tests/.tmp/cache_pre_stage0_llm_1")
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    llm = _FakeLLM(
        '{"regime_label":"TRENDING","risk_posture":"RISK_OFF","preferred_tracks":["anomaly_scan"],'
        '"stage0_overrides":{"min_avg_dollar_volume_20d":999999999,"catalyst_window_days":100},'
        '"stage2_weight_tilts":{"technical_momentum":9,"short_squeeze":0.1},"scan_notes":"ok"}'
    )
    bias = build_llm_bias_profile(
        llm=llm,
        trade_date="2026-02-19",
        snapshot={"a": 1},
        cache_config={"enabled": True, "dir": str(cache_dir), "ttl_hours": 12, "force_refresh": False},
        metrics={},
    )
    assert bias["regime_label"] == "TRENDING"
    assert bias["risk_posture"] == "RISK_OFF"
    assert 0.5 <= bias["stage2_weight_tilts"]["technical_momentum"] <= 1.5
    assert 0.5 <= bias["stage2_weight_tilts"]["short_squeeze"] <= 1.5
    assert bias["stage0_overrides"]["min_avg_dollar_volume_20d"] <= 30_000_000
    shutil.rmtree(cache_dir, ignore_errors=True)


def test_pre_stage0_llm_contract_malformed_fallback():
    cache_dir = Path("tests/.tmp/cache_pre_stage0_llm_2")
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    llm = _FakeLLM("not json")
    bias = build_llm_bias_profile(
        llm=llm,
        trade_date="2026-02-19",
        snapshot={"a": 1},
        cache_config={"enabled": True, "dir": str(cache_dir), "ttl_hours": 12, "force_refresh": True},
        metrics={},
    )
    assert bias["regime_label"] == "NEUTRAL"
    assert bias["risk_posture"] == "NEUTRAL"
    shutil.rmtree(cache_dir, ignore_errors=True)
