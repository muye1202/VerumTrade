from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from verumtrade.agents.discovery.intelligence import pipeline_cache
from verumtrade.agents.discovery.intelligence.market_context_snapshot import (
    PreStage0IntelligenceBuilder,
)


def test_pre_stage0_symbol_series_cache_reuse(monkeypatch):
    pipeline_cache._MEMO.clear()
    cache_dir = Path("tests/.tmp") / f"cache_pre_stage0_symbol_{uuid4().hex}"
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    calls = {"n": 0}

    def fake_route_to_vendor(method: str, symbol: str, start: str, end: str):
        calls["n"] += 1
        return (
            "# header\n"
            "Date,Open,High,Low,Close,Adj Close,Volume\n"
            "2026-02-18,100,101,99,100,100,1000000\n"
            "2026-02-19,101,102,100,101,101,1100000\n"
        )

    b = PreStage0IntelligenceBuilder(
        config={
            "_route_to_vendor": fake_route_to_vendor,
            "pre_stage0_intelligence": {
                "cache": {
                    "enabled": True,
                    "dir": str(cache_dir),
                    "ttl_hours": 24,
                }
            }
        }
    )
    rows1 = b._fetch_series("SPY", "2026-02-01", "2026-02-19")
    rows2 = b._fetch_series("SPY", "2026-02-01", "2026-02-19")
    assert len(rows1) == 2
    assert len(rows2) == 2
    assert calls["n"] == 1
    shutil.rmtree(cache_dir, ignore_errors=True)
