"""Tests for the Tier-2 peer read-through (peer_sets.py + peer earnings in the catalyst bundle)."""

from __future__ import annotations

from verumtrade.agents.utils.market_data.peer_sets import (
    resolve_peers,
    sector_etf_for,
    foreign_markets_for,
)
from verumtrade.agents.utils.market_data.bundle_tools import _peer_earnings_events_from_calendar
from verumtrade.schemas.catalyst_events import CatalystEventBundle


# --- Peer resolution -------------------------------------------------------

def test_curated_peers_include_basket_and_exclude_self():
    peers = resolve_peers("MU")
    # MU sits in both the memory and AI-semis baskets.
    assert "WDC" in peers and "SNDK" in peers  # memory
    assert "AVGO" in peers and "NVDA" in peers  # AI-semis (the AVGO read-through)
    assert "MU" not in peers
    assert len(peers) <= 12


def test_resolve_peers_disabled_returns_empty():
    assert resolve_peers("MU", config={"enable_peer_read_through": False}) == []


def test_foreign_markets_for_memory_and_semis():
    assert foreign_markets_for("MU") == ["korea", "taiwan"]
    assert foreign_markets_for("NVDA") == ["taiwan", "korea"]
    assert foreign_markets_for("KO") == []  # not in a curated basket


def test_foreign_markets_for_disabled_returns_empty():
    assert foreign_markets_for("MU", config={"enable_foreign_market_channel": False}) == []


def test_resolve_peers_unknown_ticker_degrades_to_list():
    # Uncurated ticker -> taxonomy fallback; must never raise and always returns a list.
    out = resolve_peers("ZZZZ")
    assert isinstance(out, list)


def test_sector_etf_resolution():
    assert sector_etf_for("MU") == "SMH"
    assert sector_etf_for("AAPL") == "XLK"
    assert sector_etf_for("ZZZZ") is None
    assert sector_etf_for("MU", config={"enable_sector_parabola": False}) is None


# --- Peer earnings -> peer_catalyst rows -----------------------------------

def _calendar() -> list[dict]:
    return [
        {"symbol": "AVGO", "date": "2026-06-03", "hour": "amc"},   # peer
        {"symbol": "MRVL", "date": "2026-06-05", "hour": "bmo"},   # peer
        {"symbol": "NVDA", "date": "2026-06-04"},                  # target -> excluded
        {"symbol": "KO", "date": "2026-06-04"},                    # non-peer -> dropped
    ]


def test_peer_earnings_keeps_peers_excludes_target_and_nonpeers():
    rows = _peer_earnings_events_from_calendar(
        _calendar(),
        peers=["AVGO", "MRVL", "NVDA", "INTC"],
        target="NVDA",
        as_of="2026-06-02",
    )
    syms = {r["mentioned_tickers"][0] for r in rows}
    assert syms == {"AVGO", "MRVL"}  # NVDA (target) excluded, KO (non-peer) dropped
    assert all(r["event_type"] == "peer_catalyst" for r in rows)
    assert all(r["ticker"] == "NVDA" for r in rows)
    # Sorted by date.
    assert [r["event_time"] for r in rows] == ["2026-06-03", "2026-06-05"]
    # Materiality below the target-material-event threshold (0.65) so it doesn't masquerade
    # as a company-specific catalyst.
    assert all(r["materiality_score"] < 0.65 for r in rows)


def test_peer_earnings_empty_when_no_peers():
    assert _peer_earnings_events_from_calendar(_calendar(), peers=[], target="NVDA", as_of="x") == []


def test_peer_catalyst_row_round_trips_through_schema():
    rows = _peer_earnings_events_from_calendar(
        _calendar(), peers=["AVGO"], target="NVDA", as_of="2026-06-02"
    )
    bundle = CatalystEventBundle.from_dict({"ticker": "NVDA", "upcoming_events": rows})
    assert len(bundle.upcoming_events) == 1
    ev = bundle.upcoming_events[0]
    assert ev.event_type == "peer_catalyst"
    assert ev.event_time == "2026-06-03"
