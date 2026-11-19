import json

from api.utils import build_analysis_reports_payload
from verumtrade.graph.propagation import Propagator
from verumtrade.graph.verumtrade_graph import VerumtradeGraph


def _sample_bundle_dict():
    return {
        "ticker": "MU",
        "company_name": "Micron Technology",
        "as_of": "2026-05-04",
        "recent_events": [
            {
                "event_id": "evt_001",
                "ticker": "MU",
                "event_type": "earnings_result",
                "event_time": "2026-05-01T20:00:00Z",
                "detected_at": "2026-05-02T00:00:00Z",
                "source": "news",
                "title": "Micron raises guidance",
                "summary": "Management lifted revenue guidance.",
                "url": "https://example.com/mu-guidance",
                "materiality_score": 0.82,
                "novelty_score": 0.9,
                "sentiment_score": 0.6,
                "confidence": 0.86,
            }
        ],
        "upcoming_events": [],
        "recent_filings": [
            {
                "accession_number": "0000000000-26-000001",
                "cik": "723125",
                "form_type": "8-K",
                "filing_date": "2026-05-02",
                "report_date": "2026-05-01",
                "primary_document_url": "https://example.com/8k",
                "filing_summary": "Guidance update.",
                "extracted_signals": ["guidance_raise"],
                "materiality_score": 0.8,
            }
        ],
        "macro_events": [
            {
                "event_name": "FOMC",
                "release_time": "2026-05-06T18:00:00Z",
                "series_or_release_id": None,
                "actual": None,
                "consensus": None,
                "previous": None,
                "surprise_score": None,
                "affected_sectors": ["semiconductors"],
                "relevance_to_ticker": 0.4,
            }
        ],
        "market_context": {
            "last_close": 120.5,
            "one_day_return_pct": 4.2,
            "five_day_return_pct": 9.5,
            "volume_ratio": 2.3,
            "price_volume_shock": True,
            "summary": "Breakout on elevated volume.",
        },
        "position_context": {
            "has_position": True,
            "position_size_pct": 0.12,
            "cost_basis": 100.0,
            "unrealized_pnl_pct": 20.5,
            "stop_loss": 108.0,
            "target_price": 135.0,
            "max_position_size_pct": 0.2,
            "holding_period": "1-2 weeks",
        },
        "prior_thesis": {
            "decision": "BUY",
            "thesis_summary": "Memory demand acceleration supports upside.",
            "bull_points": ["Guidance revisions improving"],
            "bear_points": ["Cyclical pricing risk"],
            "thesis_dependencies": ["Revenue acceleration"],
            "invalidation_conditions": ["Guidance cut"],
            "time_horizon": "1-2 weeks",
            "created_at": "2026-05-01T00:00:00Z",
        },
        "source_quality": {"news": {"status": "ok"}},
        "data_freshness": {"news": "2026-05-04T00:00:00Z"},
    }


def test_section_5_bundle_round_trips_and_defaults_optional_inputs():
    from verumtrade.schemas.catalyst_events import CatalystEventBundle

    bundle = CatalystEventBundle.from_dict(_sample_bundle_dict())
    reloaded = CatalystEventBundle.from_json(bundle.to_json())

    assert reloaded.ticker == "MU"
    assert reloaded.recent_events[0].event_type == "earnings_result"
    assert reloaded.recent_filings[0].form_type == "8-K"
    assert reloaded.macro_events[0].affected_sectors == ["semiconductors"]
    assert reloaded.position_context.has_position is True
    assert reloaded.prior_thesis.thesis_dependencies == ["Revenue acceleration"]

    minimal = CatalystEventBundle.from_dict({"ticker": "AAPL", "as_of": "2026-05-04"})
    assert minimal.company_name is None
    assert minimal.recent_events == []
    assert minimal.position_context is None
    assert minimal.market_context.summary == ""


def test_section_6_report_validates_allowed_values_and_evidence_table():
    from verumtrade.schemas.catalyst_events import CatalystEventReport

    report = CatalystEventReport.from_dict(
        {
            "ticker": "MU",
            "as_of": "2026-05-04",
            "event_risk_rating": "invalid",
            "catalyst_score": 1.5,
            "thesis_break_score": -0.2,
            "thesis_support_score": 0.7,
            "near_term_catalysts": ["earnings"],
            "recent_material_events": ["8-K guidance raise"],
            "thesis_supporting_events": ["guidance raise"],
            "thesis_breaking_events": [],
            "unresolved_questions": ["How durable is pricing?"],
            "recommended_action": "not_allowed",
            "action_rationale": "Malformed values should be made safe.",
            "risk_controls": ["do not chase"],
            "evidence_table": [
                {
                    "source": "8-K",
                    "event_type": "guidance_change",
                    "date": "2026-05-02",
                    "claim": "Guidance increased.",
                    "thesis_impact": "supporting",
                    "confidence": 1.2,
                    "url": "https://example.com/8k",
                }
            ],
        }
    )

    payload = json.loads(report.to_json())
    assert payload["event_risk_rating"] == "MEDIUM"
    assert payload["recommended_action"] == "continue_analysis"
    assert payload["catalyst_score"] == 1.0
    assert payload["thesis_break_score"] == 0.0
    assert payload["evidence_table"][0]["confidence"] == 1.0


def test_malformed_llm_output_produces_valid_fallback_report():
    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    report = parse_catalyst_report("not json", _sample_bundle_dict())

    assert report["ticker"] == "MU"
    assert report["event_risk_rating"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert report["recommended_action"] == "risk_judge_review"
    assert report["fallback_mode"] == "material_event_detected"
    assert isinstance(report["evidence_table"], list)


def test_tagged_nested_catalyst_json_parses_without_fallback():
    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    content = """
    Here is the report.
    BEGIN_CATALYST_EVENT_REPORT_JSON
    {
      "ticker": "MU",
      "as_of": "2026-05-04",
      "event_risk_rating": "HIGH",
      "catalyst_score": 0.82,
      "thesis_break_score": 0.12,
      "thesis_support_score": 0.75,
      "near_term_catalysts": ["earnings call"],
      "recent_material_events": ["guidance raise"],
      "thesis_supporting_events": ["8-K supports revenue acceleration"],
      "thesis_breaking_events": [],
      "unresolved_questions": ["durability of demand"],
      "recommended_action": "risk_judge_review",
      "action_rationale": "Guidance changed inside the trade horizon.",
      "risk_controls": ["avoid full-size entry before confirmation"],
      "evidence_table": [
        {
          "source": "news",
          "event_type": "guidance_change",
          "date": "2026-05-01",
          "claim": "Management lifted guidance.",
          "thesis_impact": "supporting",
          "confidence": 0.86,
          "url": "https://example.com/mu-guidance",
          "source_event_id": "evt_001"
        }
      ]
    }
    END_CATALYST_EVENT_REPORT_JSON
    """

    report = parse_catalyst_report(content, _sample_bundle_dict())

    assert report["event_risk_rating"] == "HIGH"
    assert report["recommended_action"] == "risk_judge_review"
    assert report["action_rationale"] == "Guidance changed inside the trade horizon."
    assert report["evidence_table"][0]["source_event_id"] == "evt_001"


def test_workbench_ledger_json_is_not_accepted_as_catalyst_report():
    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    content = """
    BEGIN_ANALYST_LEDGER_JSON
    {
      "observations": [
        {
          "id": "obs_001",
          "source_fact_ids": ["price_action_summary_001"],
          "text": "MU is at a 60-day high with elevated volume."
        }
      ],
      "hypothesis_candidates": [
        {
          "id": "hyp_001",
          "claim": "Momentum may reverse sharply.",
          "unresolved_questions": ["q_drivers_of_rally"]
        }
      ]
    }
    END_ANALYST_LEDGER_JSON
    """

    report, telemetry = parse_catalyst_report(content, _sample_bundle_dict(), include_telemetry=True)

    assert telemetry["parse_ok"] is False
    assert telemetry["failure_stage"] == "json_not_found"
    assert report["fallback_mode"] == "material_event_detected"
    assert report["evidence_table"]


def test_catalyst_event_extraction_filters_urls_etfs_and_field_fragments():
    from verumtrade.agents.utils.market_data.bundle_tools import _events_from_text

    raw = "\n".join(
        [
            "https://malaysia.news.yahoo.com/morgan-stanley-raises-price-targets-214700120.html",
            "The SCHD ETF is underperforming because it lacks AI exposure and dividend stocks lag.",
            '"security_type": "Common Stock",',
            '"share_price": "172.0"',
            "QCOM rallied Thursday on elevated volume after Qualcomm joined an Open RAN industry group.",
            "QCOM guidance update: management raised revenue outlook for the next quarter.",
        ]
    )

    events = _events_from_text("QCOM", "company_news_window", raw, "2026-05-08")
    titles = [event["title"] for event in events]

    assert titles == [
        "QCOM rallied Thursday on elevated volume after Qualcomm joined an Open RAN industry group.",
        "QCOM guidance update: management raised revenue outlook for the next quarter.",
    ]
    assert all(event["source_event_id"] == event["event_id"] for event in events)


def test_catalyst_schema_round_trips_aliases_and_relevance_diagnostics():
    from verumtrade.schemas.catalyst_events import CatalystEventBundle

    bundle = CatalystEventBundle.from_dict(
        {
            "ticker": "INTC",
            "company_name": "Intel Corporation",
            "aliases": ["INTC", "Intel", "Intel Corporation"],
            "recent_events": [
                {
                    "event_id": "evt_001",
                    "ticker": "INTC",
                    "title": "Intel issues guidance update",
                    "relevance_score": 0.9,
                    "matched_aliases": ["Intel"],
                    "mentioned_tickers": ["INTC"],
                    "contamination_flags": [],
                }
            ],
        }
    )

    payload = bundle.to_dict()

    assert payload["aliases"] == ["INTC", "Intel", "Intel Corporation"]
    assert payload["recent_events"][0]["relevance_score"] == 0.9
    assert payload["recent_events"][0]["matched_aliases"] == ["Intel"]
    assert payload["recent_events"][0]["mentioned_tickers"] == ["INTC"]
    assert payload["recent_events"][0]["quarantine_reason"] is None


def test_relevance_scoring_accepts_target_alias_and_quarantines_mixed_ticker_lines():
    from verumtrade.agents.utils.market_data.bundle_tools import (
        extract_mentioned_tickers,
        score_event_relevance,
    )

    target = score_event_relevance(
        text="Intel Corporation raises quarterly guidance after stronger data-center demand.",
        ticker="INTC",
        aliases=["INTC", "Intel", "Intel Corporation"],
        source="company_news_raw",
    )
    mixed = score_event_relevance(
        text="MPWR insider sale disclosed while Intel supplier headlines mention INTC sympathy.",
        ticker="INTC",
        aliases=["INTC", "Intel", "Intel Corporation"],
        source="company_news_raw",
    )

    assert extract_mentioned_tickers("The CEO discussed AI demand; INTC rose while MPWR fell.") == ["INTC", "MPWR"]
    assert target["decision"] == "accept"
    assert target["relevance_score"] >= 0.65
    assert "Intel Corporation" in target["matched_aliases"]
    assert mixed["decision"] == "quarantine"
    assert "mixed_target_and_unrelated_tickers" in mixed["contamination_flags"]


def test_company_alias_resolver_reuses_fundamentals_overview_metadata():
    from verumtrade.agents.utils.market_data.bundle_tools import resolve_company_aliases

    identity = resolve_company_aliases(
        "INTC",
        "2026-05-08",
        fundamentals_raw='{"Symbol":"INTC","Name":"Intel Corporation","Exchange":"NASDAQ"}',
    )

    assert identity["company_name"] == "Intel Corporation"
    assert identity["source"] == "fundamentals_company_overview"
    assert identity["confidence"] >= 0.85
    assert "Intel" in identity["aliases"]
    assert "Intel Corp" in identity["aliases"]


def test_catalyst_event_extraction_quarantines_irrelevant_news_before_keyword_classification():
    from verumtrade.agents.utils.market_data.bundle_tools import _events_from_text

    raw = "\n".join(
        [
            "MPWR insider sale disclosed in latest Form 4 filing.",
            "Stardust Power announces registered direct offering.",
            "Intel Corporation raises guidance after stronger PC demand.",
            "INTC price jumped on elevated volume after the guidance update.",
        ]
    )

    accepted, quarantined, dropped = _events_from_text(
        "INTC",
        "company_news_raw",
        raw,
        "2026-05-08",
        aliases=["INTC", "Intel", "Intel Corporation"],
        include_diagnostics=True,
    )

    assert [event["title"] for event in accepted] == [
        "Intel Corporation raises guidance after stronger PC demand.",
        "INTC price jumped on elevated volume after the guidance update.",
    ]
    assert quarantined == []
    assert [event["title"] for event in dropped] == [
        "MPWR insider sale disclosed in latest Form 4 filing.",
        "Stardust Power announces registered direct offering.",
    ]
    assert all(event["relevance_score"] >= 0.65 for event in accepted)


def test_source_contamination_accounting_marks_degraded_bundle_quality():
    from verumtrade.agents.utils.market_data.bundle_tools import _source_quality_from_event_diagnostics

    quality = _source_quality_from_event_diagnostics(
        {"company_news_raw": "raw news text"},
        {
            "company_news_raw": {
                "accepted_events": [{"event_id": "accepted_001"}],
                "quarantined_events": [{"event_id": "quarantine_001"}],
                "dropped_events": [{"event_id": "drop_001"}, {"event_id": "drop_002"}],
            }
        },
    )

    source_quality = quality["source_quality"]["company_news_raw"]
    bundle_quality = quality["bundle_quality"]

    assert source_quality["status"] == "contaminated"
    assert source_quality["accepted_events"] == 1
    assert source_quality["quarantined_events"] == 1
    assert source_quality["dropped_events"] == 2
    assert source_quality["contamination_score"] == 0.75
    assert bundle_quality["accepted_event_count"] == 1
    assert bundle_quality["quarantined_event_count"] == 1
    assert bundle_quality["dropped_event_count"] == 2
    assert bundle_quality["quality_gate"] == "contaminated"


def test_parser_accepts_fenced_json_and_reports_parse_telemetry():
    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    report, telemetry = parse_catalyst_report(
        """
        Narrative before.
        ```json
        {"ticker":"MU","as_of":"2026-05-04","event_risk_rating":"LOW","recommended_action":"ignore_low_materiality"}
        ```
        """,
        _sample_bundle_dict(),
        include_telemetry=True,
    )

    assert report["event_risk_rating"] == "LOW"
    assert report["recommended_action"] == "ignore_low_materiality"
    assert telemetry["parse_ok"] is True
    assert telemetry["failure_stage"] == ""
    assert telemetry["used_structured_output"] is False


def test_parser_accepts_tool_call_arguments_shape():
    from types import SimpleNamespace

    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    message = SimpleNamespace(
        content="",
        tool_calls=[
            {
                "args": {
                    "ticker": "MU",
                    "as_of": "2026-05-04",
                    "event_risk_rating": "MEDIUM",
                    "recommended_action": "rerun_full_analysis",
                }
            }
        ],
    )

    report, telemetry = parse_catalyst_report(message, _sample_bundle_dict(), include_telemetry=True)

    assert report["recommended_action"] == "rerun_full_analysis"
    assert telemetry["parse_ok"] is True
    assert telemetry["parse_stage"] == "tool_call_args"


def test_contaminated_parse_failure_uses_conservative_fallback():
    from verumtrade.agents.analysts.catalyst_event_analyst import parse_catalyst_report

    bundle = {
        "ticker": "INTC",
        "as_of": "2026-05-08",
        "recent_events": [],
        "quarantined_events": [
            {
                "event_id": "quarantine_001",
                "ticker": "INTC",
                "title": "MPWR insider sale mentions INTC sympathy",
                "materiality_score": 0.9,
            }
        ],
        "bundle_quality": {
            "accepted_event_count": 0,
            "quarantined_event_count": 1,
            "dropped_event_count": 3,
            "max_source_contamination": 0.75,
            "quality_gate": "contaminated",
        },
    }

    report, telemetry = parse_catalyst_report("not json", bundle, include_telemetry=True)

    assert report["event_risk_rating"] == "MEDIUM"
    assert report["recommended_action"] == "risk_judge_review"
    assert report["fallback_mode"] == "source_contaminated"
    assert report["recent_material_events"] == []
    assert any("contamination" in item.lower() for item in report["unresolved_questions"])
    assert telemetry["parse_ok"] is False
    assert telemetry["output_preview"] == "not json"


def test_material_filing_conversion_preserves_source_event_id_and_defaults():
    from verumtrade.agents.utils.market_data.bundle_tools import _filing_event_from_record

    event = _filing_event_from_record(
        {
            "accession_number": "0000000000-26-000123",
            "form_type": "S-3",
            "filing_date": "2026-05-08",
            "primary_document_url": "https://example.com/s3",
            "filing_summary": "Shelf registration statement.",
        },
        ticker="INTC",
        as_of="2026-05-08",
        idx=1,
    )

    assert event["event_type"] == "sec_filing"
    assert event["source_event_id"] == "0000000000-26-000123"
    assert event["materiality_score"] == 0.8
    assert event["relevance_score"] == 1.0


def test_earnings_calendar_conversion_adds_upcoming_target_event():
    from verumtrade.agents.utils.market_data.bundle_tools import _earnings_events_from_calendar

    events = _earnings_events_from_calendar(
        [
            {"symbol": "INTC", "date": "2026-07-24", "hour": "amc"},
            {"symbol": "MPWR", "date": "2026-07-25", "hour": "bmo"},
        ],
        ticker="INTC",
        as_of="2026-05-10",
        aliases=["INTC", "Intel", "Intel Corporation"],
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "earnings_date"
    assert events[0]["event_time"] == "2026-07-24"
    assert events[0]["source"] == "earnings_calendar"
    assert events[0]["relevance_score"] == 1.0
    assert events[0]["matched_aliases"] == ["INTC"]


def test_position_context_parser_reuses_portfolio_context_payload():
    from verumtrade.agents.utils.market_data.bundle_tools import _position_context_from_portfolio

    context = _position_context_from_portfolio(
        '{"positions":[{"symbol":"INTC","market_value":8000,"portfolio_value":100000,'
        '"avg_entry_price":42.5,"unrealized_plpc":0.18,"stop_loss":38,"target_price":55}]}',
        ticker="INTC",
    )

    assert context["has_position"] is True
    assert context["position_size_pct"] == 0.08
    assert context["cost_basis"] == 42.5
    assert context["unrealized_pnl_pct"] == 0.18
    assert context["stop_loss"] == 38
    assert context["target_price"] == 55


def test_catalyst_ledger_links_observations_to_source_event_ids():
    from verumtrade.agents.analysts.catalyst_event_analyst import _ledger_from_report

    ledger = _ledger_from_report(
        {
            "ticker": "MU",
            "as_of": "2026-05-04",
            "event_risk_rating": "HIGH",
            "catalyst_score": 0.82,
            "unresolved_questions": [],
            "evidence_table": [
                {
                    "source": "news",
                    "event_type": "guidance_change",
                    "date": "2026-05-01",
                    "claim": "Management lifted guidance.",
                    "thesis_impact": "supporting",
                    "confidence": 0.86,
                    "url": "https://example.com/mu-guidance",
                    "source_event_id": "evt_001",
                }
            ],
        }
    )

    assert ledger["observations"][0]["source_fact_ids"] == ["evt_001"]
    assert ledger["active_hypotheses"][0]["support"] == ["obs_catalyst_001"]


def test_catalyst_bundle_tool_output_becomes_source_facts():
    from types import SimpleNamespace

    from verumtrade.agents.utils.agent_runtime.evidence_graph import (
        create_capture_evidence_facts_node,
    )

    update = create_capture_evidence_facts_node("catalyst")(
        {"messages": [SimpleNamespace(content=json.dumps(_sample_bundle_dict()))]}
    )

    fact_ids = {fact["id"] for fact in update["evidence_source_facts"]}

    assert "evt_001" in fact_ids
    fact = next(fact for fact in update["evidence_source_facts"] if fact["id"] == "evt_001")
    assert fact["domain"] == "catalyst"
    assert fact["source_type"] == "vendor"
    assert fact["claim"] == "Micron raises guidance"


def test_initial_state_payload_and_order_include_catalyst_fields():
    state = Propagator().create_initial_state("MU", "2026-05-04")

    assert state["catalyst_report"] == ""
    assert state["catalyst_event_bundle"] == {}
    assert state["catalyst_event_report_structured"] == {}
    assert state["catalyst_ledger"]["analyst_domain"] == "catalyst"

    ordered = VerumtradeGraph.normalize_selected_analysts(
        ["market", "news", "catalyst", "fundamentals"]
    )
    assert ordered == ["catalyst", "market", "news", "fundamentals"]

    reports = build_analysis_reports_payload(
        {
            "catalyst_report": "## Catalyst",
            "catalyst_event_bundle": {"ticker": "MU"},
            "catalyst_event_report_structured": {"event_risk_rating": "HIGH"},
            "catalyst_parse_telemetry": {"parse_ok": True},
            "catalyst_ledger": {"analyst_domain": "catalyst"},
            "catalyst_evidence": "Catalyst evidence",
        }
    )
    assert reports["catalyst_event_report_structured"]["event_risk_rating"] == "HIGH"
    assert reports["catalyst_parse_telemetry"]["parse_ok"] is True
