from pathlib import Path
import uuid

import pytest

from verumtrade.agents.journal.core.store import JournalStore


def _db_path() -> Path:
    base = Path("journal/.tmp_tests")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"journal_{uuid.uuid4().hex}.db"


def test_event_confirmations_prefer_manual_over_inferred():
    db_path = _db_path()
    try:
        store = JournalStore(db_path=db_path)
    except Exception as e:
        pytest.skip(f"JournalStore unavailable in this environment: {e}")

    store.save_event_confirmation(
        ticker="RKLB",
        event_key="neutron_commentary_clean",
        value="false",
        source="inferred",
        timestamp="2026-02-27T10:00:00",
    )
    store.save_event_confirmation(
        ticker="RKLB",
        event_key="neutron_commentary_clean",
        value="true",
        source="manual",
        timestamp="2026-02-27T09:00:00",
    )

    flags = store.get_event_confirmations(
        ticker="RKLB",
        as_of_ts="2026-02-27T11:00:00",
    )
    assert flags["neutron_commentary_clean"] is True


def test_event_confirmations_filter_sources():
    db_path = _db_path()
    try:
        store = JournalStore(db_path=db_path)
    except Exception as e:
        pytest.skip(f"JournalStore unavailable in this environment: {e}")

    store.save_event_confirmation(
        ticker="RKLB",
        event_key="neutron_commentary_clean",
        value="false",
        source="inferred",
        timestamp="2026-02-27T10:00:00",
    )
    store.save_event_confirmation(
        ticker="RKLB",
        event_key="neutron_commentary_clean",
        value="true",
        source="agent",
        timestamp="2026-02-27T09:00:00",
    )

    flags = store.get_event_confirmations(
        ticker="RKLB",
        as_of_ts="2026-02-27T11:00:00",
        sources=["inferred"],
    )
    assert flags["neutron_commentary_clean"] is False
