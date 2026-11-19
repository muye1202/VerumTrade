import asyncio
import json

import pytest

from verumtrade.agents.analysts.discovery_lane import select_allowed_question
from verumtrade.agents.utils.market_data.bundle_tools import (
    _events_from_text,
    _source_quality_from_event_diagnostics,
)
from verumtrade.agents.utils.market_data import bundle_tools
from verumtrade.agents.utils.market_data.fundamentals_parser import (
    build_fundamentals_packet,
    latest_periods,
    parse_fundamentals_sections,
)
from verumtrade.agents.utils.market_data.news_data_tools import get_recent_sec_filings
from verumtrade.dataflows.vendors.alpha_vantage.alpha_vantage_common import get_api_key
from verumtrade.dataflows.vendors.sec_edgar.sec_edgar_vendor import fetch_recent_filings
from verumtrade.execution.decision_guard import evaluate_data_quality_fault


def test_alpha_vantage_accepts_legacy_env_var_alias(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "legacy-key")

    assert get_api_key() == "legacy-key"


def test_target_scoped_price_action_lines_are_not_counted_as_contamination():
    raw = "\n".join(
        [
            "- Last close: 1694.98 (prev: 1641.64)",
            "- ATR(14): 119.15 (7.03% of price)",
            "| 20D high | 1708.82 | near-term resistance / breakout trigger |",
        ]
    )

    accepted, quarantined, dropped = _events_from_text(
        "SNDK",
        "price_action_summary",
        raw,
        "2026-06-01",
        include_diagnostics=True,
    )
    quality = _source_quality_from_event_diagnostics(
        {"price_action_summary": raw},
        {
            "price_action_summary": {
                "accepted_events": accepted,
                "quarantined_events": quarantined,
                "dropped_events": dropped,
            }
        },
    )

    assert accepted
    assert dropped == []
    assert quality["source_quality"]["price_action_summary"]["status"] == "ok"
    assert quality["bundle_quality"]["quality_gate"] == "clean"


def test_contaminated_data_quality_hard_faults_actionable_decisions():
    fault = evaluate_data_quality_fault(
        {
            "catalyst_event_bundle": {
                "bundle_quality": {
                    "quality_gate": "contaminated",
                    "max_source_contamination": 0.97,
                }
            },
            "market_snapshot": {
                "reference_price": None,
                "price_anchor_missing": True,
            },
            "final_trade_decision_structured": {
                "action": "HOLD",
                "execution_intent": "wait_for_trigger",
                "execution_plan": [
                    {
                        "action_template": {
                            "action": "BUY",
                            "stop_loss": 92.5,
                            "take_profit": 114.0,
                        }
                    }
                ],
            },
        }
    )

    assert fault
    assert "data quality hard fault" in fault
    assert "contaminated" in fault
    assert "price anchor unavailable" in fault


def test_clean_hold_without_actionable_plan_does_not_fault():
    assert (
        evaluate_data_quality_fault(
            {
                "catalyst_event_bundle": {"bundle_quality": {"quality_gate": "clean"}},
                "market_snapshot": {"reference_price": None, "price_anchor_missing": True},
                "final_trade_decision_structured": {"action": "HOLD"},
            }
        )
        == ""
    )


def test_alpha_vantage_income_statement_derives_high_operating_margin():
    parsed = parse_fundamentals_sections(
        {
            "fundamentals": json.dumps(
                {
                    "OperatingMarginTTM": "0.7",
                    "ProfitMargin": "0.342",
                    "RevenueTTM": "13184000000",
                }
            ),
            "income_statement": json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "totalRevenue": "5950000000",
                            "costOfRevenue": "1285000000",
                            "operatingIncome": "4111000000",
                            "netIncome": "3615000000",
                        }
                    ]
                }
            ),
        },
        symbol="SNDK",
        curr_date="2026-06-01",
    )

    period = parsed["periods"][0]

    assert period["period_end"] == "2026-04-03"
    assert period["statement_type"] == "income_statement"
    assert period["operating_margin"] == pytest.approx(0.691, rel=0.001)
    assert period["net_margin"] == pytest.approx(0.6075, rel=0.001)
    assert "confirmed_high_operating_margin" in parsed["reconciliation_flags"]
    assert "overview_statement_consistent" in parsed["reconciliation_flags"]


def test_yfinance_csv_statement_treats_columns_as_period_dates():
    parsed = parse_fundamentals_sections(
        {
            "income_statement": "\n".join(
                [
                    "# Income Statement data for SNDK (quarterly)",
                    "",
                    ",2026-04-03,2025-12-26",
                    "Total Revenue,5950000000,3025000000",
                    "Cost Of Revenue,1285000000,1485000000",
                    "Operating Income,4111000000,1065000000",
                    "Net Income,3615000000,827000000",
                ]
            )
        },
        symbol="SNDK",
        curr_date="2026-06-01",
    )

    periods = latest_periods(parsed["periods"], limit=2)

    assert [period["period_end"] for period in periods] == ["2026-04-03", "2025-12-26"]
    assert periods[0]["total_revenue"] == 5950000000
    assert periods[1]["operating_margin"] == pytest.approx(0.3521, rel=0.001)


def test_non_numeric_values_remain_none_and_emit_missing_flags():
    parsed = parse_fundamentals_sections(
        {
            "income_statement": json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "totalRevenue": "not-a-number",
                            "costOfRevenue": "1285000000",
                            "operatingIncome": "None",
                        }
                    ]
                }
            )
        },
        symbol="SNDK",
        curr_date="2026-06-01",
    )

    period = parsed["periods"][0]

    assert period["total_revenue"] is None
    assert period["operating_income"] is None
    assert period["operating_margin"] is None
    assert "missing_operating_income" in parsed["reconciliation_flags"]


def test_clipped_alpha_vantage_json_recovers_complete_report_objects():
    clipped = (
        '{"symbol":"SNDK","annualReports":[{"fiscalDateEnding":"2025-12-31",'
        '"totalRevenue":"100"}],"quarterlyReports":[{"fiscalDateEnding":"2026-04-03",'
        '"totalRevenue":"5950000000","operatingIncome":"4111000000"}'
        ',{"fiscalDateEnding":"2025-12-26","totalRevenue":"3025000000"'
        "\n...[TRUNCATED 1234 chars]...\n"
    )

    parsed = parse_fundamentals_sections(
        {"income_statement": clipped},
        symbol="SNDK",
        curr_date="2026-06-01",
    )

    assert parsed["periods"][0]["period_end"] == "2026-04-03"
    assert parsed["periods"][0]["operating_margin"] == pytest.approx(0.691, rel=0.001)


def test_fundamentals_packet_emits_stable_structured_fact_ids():
    packet = build_fundamentals_packet(
        "SNDK",
        "2026-06-01",
        {
            "fundamentals": json.dumps({"OperatingMarginTTM": "0.7"}),
            "income_statement": json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "totalRevenue": "5950000000",
                            "operatingIncome": "4111000000",
                        }
                    ]
                }
            ),
        },
    )

    fact_ids = {fact["id"] for fact in packet["facts"]}

    assert "fact_fundamentals_income_statement_2026_04_03_operating_margin" in fact_ids
    assert packet["latest_periods"][0]["period_end"] == "2026-04-03"


def test_fundamentals_bundle_preserves_confirmed_high_margin(monkeypatch):
    async def fake_run_tool(tool_obj, payload):
        section = getattr(tool_obj, "name", "")
        if section == "get_fundamentals":
            return json.dumps({"OperatingMarginTTM": "0.7", "ProfitMargin": "0.342"})
        if section == "get_income_statement":
            return json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "totalRevenue": "5950000000",
                            "costOfRevenue": "1285000000",
                            "operatingIncome": "4111000000",
                            "netIncome": "3615000000",
                        },
                        {
                            "fiscalDateEnding": "2025-12-26",
                            "totalRevenue": "3025000000",
                            "costOfRevenue": "1485000000",
                            "operatingIncome": "1065000000",
                            "netIncome": "827000000",
                        },
                    ]
                }
            )
        if section == "get_balance_sheet":
            return json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "cashAndShortTermInvestments": "1481000000",
                            "shortLongTermDebtTotal": "2042000000",
                        }
                    ]
                }
            )
        if section == "get_cashflow":
            return json.dumps(
                {
                    "quarterlyReports": [
                        {
                            "fiscalDateEnding": "2026-04-03",
                            "operatingCashflow": "84000000",
                        }
                    ]
                }
            )
        return "No data available"

    monkeypatch.setattr(bundle_tools, "_run_tool", fake_run_tool)

    raw = asyncio.run(
        bundle_tools.get_fundamentals_data_bundle.ainvoke(
            {"ticker": "SNDK", "curr_date": "2026-06-01", "freq": "quarterly"}
        )
    )
    packet = json.loads(raw)

    assert "confirmed_high_operating_margin" in packet["reconciliation_flags"]
    assert "overview_statement_consistent" in packet["reconciliation_flags"]
    assert packet["latest_periods"][0]["operating_margin"] == pytest.approx(0.691, rel=0.001)
    assert any(
        fact["id"] == "fact_fundamentals_income_statement_2026_04_03_operating_margin"
        for fact in packet["facts"]
    )
    assert not any(fact["claim"] == '"totalRevenue": "5950000000",' for fact in packet["facts"])


def test_fetch_recent_filings_accepts_curr_date_without_treating_it_as_form_types(monkeypatch):
    captured = []

    class FakeSecClient:
        def list_filings(self, ticker, *, as_of, form_types, limit):
            captured.append({"ticker": ticker, "as_of": as_of, "form_types": list(form_types), "limit": limit})
            return [
                {
                    "accession_number": "0000000000-26-000001",
                    "cik": "0002023554",
                    "form_type": "10-Q",
                    "company_name": "Sandisk Corp",
                    "ticker": "SNDK",
                    "filing_date": "2026-05-08",
                },
                {
                    "accession_number": "0000000000-25-000009",
                    "cik": "0002023554",
                    "form_type": "8-K",
                    "company_name": "Sandisk Corp",
                    "ticker": "SNDK",
                    "filing_date": "2025-11-01",
                },
            ]

    monkeypatch.setattr("verumtrade.research.sec_client.get_sec_client", lambda: FakeSecClient())

    filings = json.loads(fetch_recent_filings("SNDK", curr_date="2026-06-01"))

    assert captured == [
        {"ticker": "SNDK", "as_of": "2026-06-01", "form_types": ["10-Q", "10-K", "8-K"], "limit": 50}
    ]
    # Only filings inside the 45-day lookback before curr_date are returned.
    assert [f["accession_number"] for f in filings] == ["0000000000-26-000001"]


def test_recent_sec_filings_tool_forwards_curr_date(monkeypatch):
    captured = {}

    def fake_route(method, *args):
        captured["method"] = method
        captured["args"] = args
        return []

    monkeypatch.setattr("verumtrade.agents.utils.market_data.news_data_tools.route_to_vendor", fake_route)

    asyncio.run(get_recent_sec_filings.ainvoke({"ticker": "SNDK", "curr_date": "2026-06-01"}))

    assert captured == {
        "method": "get_recent_sec_filings",
        "args": ("SNDK", "2026-06-01"),
    }


def test_malformed_questions_are_not_selected_and_are_flagged():
    ledger = {
        "analyst_domain": "fundamentals",
        "observations": [],
        "question_backlog": [
            {
                "id": "Q1",
                "question": "",
                "triggered_by": ["O1"],
                "expected_information_gain": 0.9,
                "evidence_surprise": 0.9,
                "estimated_tool_cost": 1.0,
                "cheapest_tool": "get_fundamentals / SEC EDGAR",
                "stop_condition": "Verify filing.",
            }
        ],
        "open_questions": ["Q1"],
    }

    assert select_allowed_question(ledger, ["get_recent_sec_filings"]) is None
    assert "Q1:blank_question" in ledger["critic_flags"]
    assert "Q1:unknown_cheapest_tool:get_fundamentals / SEC EDGAR" in ledger["critic_flags"]
