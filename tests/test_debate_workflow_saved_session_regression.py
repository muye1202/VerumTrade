from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from verumtrade.graph.debate_schema import (
    DebateWorkflowHardFault,
    debate_validation_context_from_state,
    require_valid_thesis_ledger,
)


FIXTURE = Path(__file__).parent / "fixtures" / "wdc_2026_06_18_debate_replay.json"


def _fixture_reports() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["reports"]


def _state_from_fixture(*, empty_ledger: bool = False) -> dict:
    reports = _fixture_reports()
    return {
        "company_of_interest": "WDC",
        "trade_date": "2026-06-18",
        "time_horizon": "2-3 months",
        "evidence_graph": reports["evidence_graph"],
        "evidence_ledger": [] if empty_ledger else reports["evidence_ledger"],
    }


def test_wdc_saved_session_thesis_source_ref_aliases_validate_against_ledger():
    reports = _fixture_reports()
    context = debate_validation_context_from_state(_state_from_fixture())

    validation = require_valid_thesis_ledger(
        reports["thesis_ledger_source_ref_alias"],
        stage="research_manager",
        evidence_ids=context["evidence_ids"],
        evidence_aliases=context["evidence_aliases"],
    )

    assert validation == {"valid": True, "violations": []}


def test_wdc_saved_session_thesis_aliases_validate_when_ledger_rebuilds_from_graph():
    reports = _fixture_reports()
    context = debate_validation_context_from_state(_state_from_fixture(empty_ledger=True))

    assert context["evidence_ids"]
    assert "fact_market_obs_004_recovery" in context["evidence_aliases"]
    validation = require_valid_thesis_ledger(
        reports["thesis_ledger_source_ref_alias"],
        stage="research_manager",
        evidence_ids=context["evidence_ids"],
        evidence_aliases=context["evidence_aliases"],
    )

    assert validation["valid"] is True


def test_wdc_saved_session_unknown_thesis_alias_still_hard_faults():
    reports = _fixture_reports()
    thesis = copy.deepcopy(reports["thesis_ledger_source_ref_alias"])
    thesis["accepted_claims"][0]["evidence_ids"] = ["fact_wdc_unknown_alias"]
    context = debate_validation_context_from_state(_state_from_fixture())

    with pytest.raises(DebateWorkflowHardFault, match="fact_wdc_unknown_alias"):
        require_valid_thesis_ledger(
            thesis,
            stage="research_manager",
            evidence_ids=context["evidence_ids"],
            evidence_aliases=context["evidence_aliases"],
        )
