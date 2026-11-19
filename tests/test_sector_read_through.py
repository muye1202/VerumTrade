"""Tests for the Tier-2 Phase-2b sector read-through (peer_read_through.py)."""

from __future__ import annotations

from verumtrade.agents.utils.market_data.peer_read_through import (
    build_sector_read_through,
    format_sector_read_through_markdown,
)

_PEERS = ["AVGO", "AMD", "MRVL", "000660.KS"]  # last is a foreign peer (no US news feed)
_CALENDAR = [
    {"symbol": "AVGO", "date": "2026-06-03"},
    {"symbol": "MRVL", "date": "2026-06-05"},
    # AMD has no upcoming earnings row
]


def _news(sym: str) -> str:
    return f"{sym}: management guided next-quarter AI revenue below consensus; shares slip."


def _enabled_cfg(max_peers: int = 2) -> dict:
    return {
        "enable_peer_read_through": True,
        "peer_read_through": {"fetch_peer_news": True, "max_peers": max_peers},
    }


def _build(**kw):
    return build_sector_read_through(
        "NVDA", "2026-06-02", config=_enabled_cfg(kw.pop("max_peers", 2)),
        peers=list(_PEERS), calendar_raw=list(_CALENDAR), news_fn=_news, **kw,
    )


def test_disabled_by_default():
    # fetch_peer_news defaults False even when peer read-through is on.
    out = build_sector_read_through(
        "NVDA", "2026-06-02", config={"enable_peer_read_through": True},
        peers=list(_PEERS), calendar_raw=list(_CALENDAR), news_fn=_news,
    )
    assert out == {}


def test_disabled_when_peer_read_through_off():
    out = build_sector_read_through(
        "NVDA", "2026-06-02",
        config={"enable_peer_read_through": False, "peer_read_through": {"fetch_peer_news": True}},
        peers=list(_PEERS), calendar_raw=list(_CALENDAR), news_fn=_news,
    )
    assert out == {}


def test_ranks_by_nearest_earnings_and_skips_foreign():
    out = _build()
    peers = [e["peer"] for e in out["entries"]]
    assert peers == ["AVGO", "MRVL"]  # soonest-reporting first; AMD (no date) and foreign dropped
    assert out["entries"][0]["next_earnings"] == "2026-06-03"
    assert "000660.KS" not in out["peers_considered"]
    assert out["target"] == "NVDA"


def test_bounded_by_max_peers():
    out = _build(max_peers=1)
    assert [e["peer"] for e in out["entries"]] == ["AVGO"]


def test_empty_news_degrades_to_empty():
    out = build_sector_read_through(
        "NVDA", "2026-06-02", config=_enabled_cfg(),
        peers=list(_PEERS), calendar_raw=list(_CALENDAR), news_fn=lambda s: "",
    )
    assert out == {}


def test_news_fn_error_is_caught():
    def boom(sym: str) -> str:
        raise RuntimeError("vendor down")

    out = build_sector_read_through(
        "NVDA", "2026-06-02", config=_enabled_cfg(),
        peers=list(_PEERS), calendar_raw=list(_CALENDAR), news_fn=boom,
    )
    assert out == {}  # all fetches failed -> no entries


def test_no_peers_returns_empty():
    out = build_sector_read_through(
        "NVDA", "2026-06-02", config=_enabled_cfg(), peers=[], calendar_raw=[], news_fn=_news,
    )
    assert out == {}


def test_markdown_formatting():
    md = format_sector_read_through_markdown(_build())
    assert "Sector Read-Through (peers of NVDA)" in md
    assert "AVGO" in md and "reports 2026-06-03" in md
    assert format_sector_read_through_markdown({}) == ""
    assert format_sector_read_through_markdown({"target": "X", "entries": []}) == ""
