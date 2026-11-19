"""Tests for the Tier-3 narrative catalyst tagger.

(``verumtrade.agents.utils.market_data.narrative_catalyst`` + its integration into
``summarize_macro_regime``.) Covers the two real counterfactuals — the May-2026 Korean "citizen
dividend" policy trial balloon and the June-2026 Broadcom peer guide-down — plus a clean control
(routine headlines must not false-positive), the disable flag, schema round-trip, and the fold into
``macro_events``.
"""

from __future__ import annotations

from verumtrade.agents.utils.market_data.narrative_catalyst import tag_narrative_events
from verumtrade.agents.utils.market_data.macro_regime import summarize_macro_regime
from verumtrade.schemas.catalyst_events import MacroEventRecord


def _types(events):
    return {e["event_type"] for e in events}


def test_korea_citizen_dividend_tags_policy_trial_balloon():
    headlines = (
        "## Global Headlines\n"
        "- South Korea policy chief floats a 'citizen dividend' from the AI/memory tax windfall\n"
        "- Officials later call it his personal opinion\n"
    )
    events = tag_narrative_events(headlines, as_of="2026-05-12")
    assert "policy_trial_balloon" in _types(events)
    row = next(e for e in events if e["event_type"] == "policy_trial_balloon")
    assert "semiconductors" in row["affected_sectors"]
    assert row["relevance_to_ticker"] > 0.0


def test_avgo_guide_down_tags_peer_guidance():
    headlines = (
        "## Headlines\n"
        "- Broadcom beats on revenue but guided below consensus on Q3 AI; did not raise FY26 outlook\n"
    )
    events = tag_narrative_events(headlines, as_of="2026-06-03")
    assert "peer_guidance" in _types(events)


def test_positioning_unwind_and_regulatory_narrative():
    headlines = (
        "- Analysts warn the parabolic semiconductor basket faces a crowded-trade unwind\n"
        "- New export controls on advanced chips spark a probe into shipments\n"
    )
    events = tag_narrative_events(headlines, as_of="2026-06-04")
    assert {"positioning_unwind", "regulatory_narrative"}.issubset(_types(events))


def test_clean_control_no_false_positives():
    headlines = (
        "## Headlines\n"
        "- Company opens a new distribution center\n"
        "- CEO speaks at an industry conference about long-term demand\n"
        "- Quarterly dividend declared, in line with prior quarters\n"
    )
    # "dividend" alone (a routine declared dividend) must NOT trip policy_trial_balloon.
    events = tag_narrative_events(headlines, as_of="2026-06-01")
    assert events == []


def test_disable_flag_returns_empty():
    headlines = "- citizen dividend windfall tax proposal"
    assert tag_narrative_events(headlines, config={"enable_narrative_catalysts": False}) == []


def test_empty_text_returns_empty():
    assert tag_narrative_events("") == []
    assert tag_narrative_events(None) == []  # type: ignore[arg-type]


def test_dedupe_one_row_per_event_type():
    headlines = "- citizen dividend ... windfall tax ... redistribute the AI windfall"
    events = tag_narrative_events(headlines, as_of="2026-05-12")
    assert sum(1 for e in events if e["event_type"] == "policy_trial_balloon") == 1


def test_rows_round_trip_through_macro_event_record():
    headlines = "- Broadcom guided below consensus; analysts flag a parabolic basket unwind"
    events = tag_narrative_events(headlines, as_of="2026-06-03")
    assert events
    for ev in events:
        rec = MacroEventRecord.from_dict(ev)
        assert rec.event_name
        assert 0.0 <= rec.relevance_to_ticker <= 1.0


def test_folds_into_macro_regime_events():
    snapshot = {
        "trade_date": "2026-05-12",
        "index_regime": {"indices": {}, "risk_off_flag": False},
        "global_news": {
            "headlines_markdown": "- South Korea floats a citizen dividend / windfall tax on the AI boom",
        },
    }
    regime = summarize_macro_regime(snapshot, trade_date="2026-05-12")
    assert "policy_trial_balloon" in {e.get("event_type") for e in regime["macro_events"]}
    assert "narrative_events" in regime


def test_macro_regime_disable_flag_suppresses_narrative():
    snapshot = {
        "trade_date": "2026-05-12",
        "index_regime": {"indices": {}, "risk_off_flag": False},
        "global_news": {"headlines_markdown": "- citizen dividend windfall tax proposal"},
    }
    regime = summarize_macro_regime(
        snapshot, trade_date="2026-05-12", config={"enable_narrative_catalysts": False}
    )
    assert "policy_trial_balloon" not in {e.get("event_type") for e in regime["macro_events"]}
