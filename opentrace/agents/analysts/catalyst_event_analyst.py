from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from opentrace.agents.analysts.discovery_lane import merge_workbench_metrics, record_tool_call_links
from opentrace.agents.analysts.tooling import build_tooling_state_update
from opentrace.agents.analysts.workbench import (
    build_ledger_evidence_summary,
    build_minimum_evidence_question,
    build_workbench_metrics,
    build_workbench_prompt_block,
    normalize_ledger,
)
from opentrace.agents.utils.agent_runtime.context_budget import build_report_evidence_summary
from opentrace.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from opentrace.agents.utils.market_data.bundle_tools import get_catalyst_event_bundle
from opentrace.dataflows.config import get_config
from opentrace.schemas.catalyst_events import CatalystEventBundle, CatalystEventReport


REPORT_JSON_HINT_KEYS = {
    "event_risk_rating",
    "catalyst_score",
    "thesis_break_score",
    "thesis_support_score",
    "near_term_catalysts",
    "recent_material_events",
    "thesis_supporting_events",
    "thesis_breaking_events",
    "unresolved_questions",
    "recommended_action",
    "action_rationale",
    "risk_controls",
    "evidence_table",
}


def _looks_like_catalyst_report(parsed: dict[str, Any]) -> bool:
    strong_keys = {
        "event_risk_rating",
        "catalyst_score",
        "thesis_break_score",
        "thesis_support_score",
        "near_term_catalysts",
        "recent_material_events",
        "recommended_action",
        "action_rationale",
        "risk_controls",
        "evidence_table",
    }
    if strong_keys.intersection(parsed.keys()):
        return True
    return len(REPORT_JSON_HINT_KEYS.intersection(parsed.keys())) >= 2


def _json_from_brace_balanced_text(raw: str) -> dict[str, Any] | None:
    start = raw.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(raw)):
            char = raw[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                    except Exception:
                        break
                    if isinstance(parsed, dict) and _looks_like_catalyst_report(parsed):
                        return parsed
                    break
        start = raw.find("{", start + 1)
    return None


def _tool_call_args_from_content(content: Any) -> dict[str, Any] | None:
    tool_calls = getattr(content, "tool_calls", None)
    if not tool_calls and isinstance(content, dict):
        tool_calls = content.get("tool_calls")
    for call in tool_calls or []:
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _extract_json_block(text: Any, start_tag: str, end_tag: str) -> tuple[dict[str, Any] | None, str, str]:
    raw = str(getattr(text, "content", text) or "")
    pattern = rf"{re.escape(start_tag)}\s*(\{{.*?\}})\s*{re.escape(end_tag)}"
    match = re.search(pattern, raw, flags=re.DOTALL | re.IGNORECASE)
    candidates: list[tuple[str, str]] = []
    if match:
        candidates.append(("tagged_json", match.group(1)))
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(("fenced_json", fenced.group(1)))
    stripped = raw.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(("whole_message_json", stripped))
    for stage, candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if isinstance(parsed, dict) and _looks_like_catalyst_report(parsed):
            return parsed, stage, ""
    parsed = _json_from_brace_balanced_text(raw)
    if parsed:
        return parsed, "brace_balanced_json", ""
    tool_args = _tool_call_args_from_content(text)
    if tool_args:
        return tool_args, "tool_call_args", ""
    return None, "json_not_found", locals().get("last_error", "")


def _bundle_from_any(value: Any, ticker: str = "", as_of: str = "") -> dict[str, Any]:
    if isinstance(value, CatalystEventBundle):
        return value.to_dict()
    if isinstance(value, dict) and value:
        return CatalystEventBundle.from_dict(value).to_dict()
    if isinstance(value, str) and value.strip():
        try:
            return CatalystEventBundle.from_json(value).to_dict()
        except Exception:
            pass
    return CatalystEventBundle.from_dict({"ticker": ticker, "as_of": as_of}).to_dict()


def _latest_bundle_from_messages(messages: list[Any], ticker: str, as_of: str) -> dict[str, Any]:
    for msg in reversed(messages or []):
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except Exception:
            continue
        if isinstance(parsed, dict) and (
            parsed.get("bundle") == "CatalystEventBundle" or "recent_events" in parsed
        ):
            return _bundle_from_any(parsed, ticker=ticker, as_of=as_of)
    return {}


def _fallback_report(bundle: dict[str, Any], reason: str = "", *, parse_failed: bool = False) -> CatalystEventReport:
    normalized = CatalystEventBundle.from_dict(bundle).to_dict()
    recent = normalized.get("recent_events", []) or []
    upcoming = normalized.get("upcoming_events", []) or []
    filings = normalized.get("recent_filings", []) or []
    quality = normalized.get("bundle_quality") or {}
    quarantined = normalized.get("quarantined_events", []) or []
    evidence = []
    for event in [*recent, *upcoming][:5]:
        evidence.append(
            {
                "source": event.get("source") or "event_bundle",
                "event_type": event.get("event_type") or "other",
                "date": event.get("event_time") or event.get("detected_at") or normalized.get("as_of", ""),
                "claim": event.get("title") or event.get("summary") or "Catalyst event in bundle.",
                "thesis_impact": "unknown",
                "confidence": event.get("confidence", 0.5),
                "url": event.get("url"),
                "source_event_id": event.get("event_id"),
            }
        )
    for filing in filings[:3]:
        evidence.append(
            {
                "source": filing.get("form_type") or "filing",
                "event_type": "sec_filing",
                "date": filing.get("filing_date") or normalized.get("as_of", ""),
                "claim": filing.get("filing_summary") or f"{filing.get('form_type', 'SEC filing')} filed.",
                "thesis_impact": "unknown",
                "confidence": filing.get("materiality_score", 0.5),
                "url": filing.get("primary_document_url"),
                "source_event_id": filing.get("accession_number"),
            }
        )

    max_materiality = 0.0
    scores = [event.get("materiality_score", 0.0) for event in recent + upcoming]
    scores += [filing.get("materiality_score", 0.0) for filing in filings]
    for score in scores:
        try:
            max_materiality = max(max_materiality, float(score))
        except Exception:
            pass
    accepted_material_event_exists = any(
        float(event.get("materiality_score") or 0.0) >= 0.65 for event in recent + upcoming
    ) or any(float(filing.get("materiality_score") or 0.0) >= 0.65 for filing in filings)
    max_contamination = float(quality.get("max_source_contamination") or 0.0)
    accepted_count = int(quality.get("accepted_event_count") or len(recent) + len(upcoming))
    missing_sources = [
        name
        for name, item in (normalized.get("source_quality") or {}).items()
        if isinstance(item, dict) and item.get("status") == "missing"
    ]
    insufficient_data = accepted_count == 0 or len(missing_sources) >= 3 or quality.get("quality_gate") in {"failed", "sparse"}
    fallback_mode = "valid_low_materiality"
    rating = "LOW"
    action = "ignore_low_materiality"
    unresolved = []
    data_quality_notes = []

    if max_contamination >= 0.50 or quality.get("quality_gate") == "contaminated":
        fallback_mode = "source_contaminated"
        rating = "MEDIUM"
        action = "risk_judge_review"
        unresolved.append("Catalyst source contamination detected; verify target-company relevance before trading.")
        data_quality_notes.append(f"max_source_contamination={max_contamination:.2f}")
    elif parse_failed and accepted_material_event_exists:
        fallback_mode = "material_event_detected"
        rating = "HIGH"
        action = "risk_judge_review"
        unresolved.append("Material accepted catalyst detected, but analyst output could not be parsed.")
    elif insufficient_data:
        fallback_mode = "insufficient_data"
        rating = "MEDIUM"
        action = "rerun_full_analysis"
        unresolved.append("Insufficient accepted catalyst data; rerun or verify source availability.")
    elif parse_failed:
        fallback_mode = "parse_failed_clean_bundle"
        rating = "MEDIUM"
        action = "rerun_full_analysis"
        unresolved.append("Catalyst analyst output was malformed despite usable bundle quality.")
    else:
        rating = "HIGH" if max_materiality >= 0.75 else "MEDIUM" if max_materiality >= 0.45 else "LOW"
        action = "continue_analysis" if rating != "LOW" else "ignore_low_materiality"
    for event in quarantined[:3]:
        title = event.get("title") or event.get("summary")
        if title:
            unresolved.append(f"Quarantined event requires target-relevance verification: {title}")
    if not unresolved:
        unresolved.append("Review event materiality manually if key source data is missing.")
    rationale = reason or "Catalyst report was built from available structured events."
    return CatalystEventReport.from_dict(
        {
            "ticker": normalized.get("ticker", ""),
            "as_of": normalized.get("as_of", ""),
            "event_risk_rating": rating,
            "catalyst_score": max_materiality,
            "thesis_break_score": 0.0,
            "thesis_support_score": 0.0,
            "near_term_catalysts": [event.get("title", "") for event in upcoming if event.get("title")],
            "recent_material_events": [
                event.get("title", "") for event in recent if float(event.get("materiality_score") or 0.0) >= 0.45
            ],
            "thesis_supporting_events": [],
            "thesis_breaking_events": [],
            "unresolved_questions": unresolved,
            "recommended_action": action,
            "action_rationale": rationale,
            "risk_controls": ["Size conservatively around unresolved catalysts."],
            "evidence_table": evidence,
            "fallback_mode": fallback_mode,
            "data_quality_notes": data_quality_notes,
        }
    )


def parse_catalyst_report(content: Any, bundle: Any, *, include_telemetry: bool = False) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    bundle_dict = _bundle_from_any(bundle)
    raw_content = str(getattr(content, "content", content) or "")
    telemetry = {
        "parse_ok": False,
        "failure_stage": "",
        "exception": "",
        "output_preview": raw_content[:800],
        "model_name": "",
        "used_structured_output": False,
        "repair_attempted": False,
        "repair_succeeded": False,
        "parse_stage": "",
    }
    parsed, stage, exception = _extract_json_block(
        content,
        "BEGIN_CATALYST_EVENT_REPORT_JSON",
        "END_CATALYST_EVENT_REPORT_JSON",
    )
    if not parsed:
        telemetry["failure_stage"] = stage
        telemetry["exception"] = exception
        report = _fallback_report(
            bundle_dict,
            "Malformed catalyst analyst output; using validated fallback.",
            parse_failed=True,
        ).to_dict()
        return (report, telemetry) if include_telemetry else report
    parsed.setdefault("ticker", bundle_dict.get("ticker", ""))
    parsed.setdefault("as_of", bundle_dict.get("as_of", ""))
    report = CatalystEventReport.from_dict(parsed).to_dict()
    telemetry["parse_ok"] = True
    telemetry["parse_stage"] = stage
    return (report, telemetry) if include_telemetry else report


def format_catalyst_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Catalyst / Event-Risk Report",
        f"- Event risk rating: **{report.get('event_risk_rating', 'MEDIUM')}**",
        f"- Catalyst score: `{report.get('catalyst_score', 0.0)}`",
        f"- Thesis break score: `{report.get('thesis_break_score', 0.0)}`",
        f"- Thesis support score: `{report.get('thesis_support_score', 0.0)}`",
        f"- Recommended action: `{report.get('recommended_action', 'continue_analysis')}`",
        f"- Rationale: {report.get('action_rationale', '')}",
        "",
        "### Near-Term Catalysts",
        *[f"- {item}" for item in report.get("near_term_catalysts", []) or ["None identified."]],
        "",
        "### Recent Material Events",
        *[f"- {item}" for item in report.get("recent_material_events", []) or ["None identified."]],
        "",
        "### Thesis Impact",
        *[f"- Supporting: {item}" for item in report.get("thesis_supporting_events", [])],
        *[f"- Breaking: {item}" for item in report.get("thesis_breaking_events", [])],
        "",
        "### Risk Controls",
        *[f"- {item}" for item in report.get("risk_controls", []) or ["No extra controls."]],
        "",
        "| Source | Event type | Date | Thesis impact | Confidence | Claim |",
        "|---|---|---:|---|---:|---|",
    ]
    for item in report.get("evidence_table", []) or []:
        lines.append(
            "| {source} | {event_type} | {date} | {impact} | {confidence:.2f} | {claim} |".format(
                source=str(item.get("source", ""))[:80],
                event_type=str(item.get("event_type", ""))[:60],
                date=str(item.get("date", ""))[:40],
                impact=str(item.get("thesis_impact", ""))[:60],
                confidence=float(item.get("confidence", 0.0) or 0.0),
                claim=str(item.get("claim", "")).replace("|", "/")[:180],
            )
        )
    return "\n".join(lines).strip()


def _ledger_from_report(report: dict[str, Any]) -> dict[str, Any]:
    observations = []
    for idx, item in enumerate(report.get("evidence_table", [])[:8], 1):
        source_ids = []
        if item.get("source_event_id"):
            source_ids.append(str(item.get("source_event_id")))
        for source_id in item.get("source_fact_ids", []) or []:
            if str(source_id) and str(source_id) not in source_ids:
                source_ids.append(str(source_id))
        observations.append(
            {
                "id": f"obs_catalyst_{idx:03d}",
                "domain": "catalyst",
                "claim": item.get("claim", ""),
                "source_fact_ids": source_ids,
                "surprise_score": item.get("confidence", 0.5),
                "why_it_matters": item.get("thesis_impact", "Event may affect thesis timing or risk."),
                "status": "explained",
            }
        )
    if not observations:
        observations.append(
            {
                "id": "obs_catalyst_001",
                "domain": "catalyst",
                "claim": report.get("action_rationale", "No material catalyst found."),
                "source_fact_ids": [],
                "surprise_score": report.get("catalyst_score", 0.0),
                "why_it_matters": "Catalyst context affects entry timing and risk budget.",
                "status": "explained",
            }
        )
    ledger = {
        "analyst_domain": "catalyst",
        "observations": observations,
        "active_hypotheses": [
            {
                "id": "h_catalyst_001",
                "claim": f"Catalyst risk is {report.get('event_risk_rating', 'MEDIUM')}",
                "origin": "anomaly_generated",
                "support": [obs["id"] for obs in observations[:3]],
                "against": [],
                "confidence": report.get("catalyst_score", 0.5),
                "falsifier": "New event data shows materially different timing or thesis impact.",
                "unresolved_questions": report.get("unresolved_questions", []),
            }
        ],
        "open_questions": report.get("unresolved_questions", []),
        "unexplained_but_decision_relevant": report.get("unresolved_questions", []),
    }
    return normalize_ledger("catalyst", ledger)


def create_catalyst_event_analyst(llm):
    def catalyst_event_analyst_node(state):
        if state.get("catalyst_report"):
            return {
                "catalyst_report": state["catalyst_report"],
                "catalyst_event_bundle": state.get("catalyst_event_bundle", {}),
                "catalyst_event_report_structured": state.get("catalyst_event_report_structured", {}),
                "catalyst_parse_telemetry": state.get("catalyst_parse_telemetry", {}),
            }

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        bundle = _bundle_from_any(state.get("catalyst_event_bundle"), ticker=ticker, as_of=current_date)
        latest_tool_bundle = _latest_bundle_from_messages(state.get("messages", []), ticker, current_date)
        if latest_tool_bundle:
            bundle = latest_tool_bundle

        config = get_config()
        tool_round_cap = int(config.get("analyst_tool_round_cap", 4) or 0)
        global_tool_round_cap = int(config.get("max_tool_calls_total", 50) or 50)
        rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
        rounds_used = int(rounds.get("catalyst", 0) or 0)
        total_rounds_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
        force_no_tools = (
            state.get("force_no_tools_for") == "catalyst"
            or (tool_round_cap > 0 and rounds_used >= tool_round_cap)
            or (global_tool_round_cap > 0 and total_rounds_used >= global_tool_round_cap)
            or bool(latest_tool_bundle)
        )
        tools = [] if force_no_tools or state.get("catalyst_event_bundle") else [get_catalyst_event_bundle]
        selected_question = build_minimum_evidence_question(
            "catalyst", getattr(get_catalyst_event_bundle, "name", "get_catalyst_event_bundle")
        )

        system_message = f"""You are the Catalyst / Event-Risk Analyst in an agentic stock analysis system.

Your job is not to summarize all news. Identify discrete events that can change trade thesis, timing, or risk budget for {ticker}.

Input contract:
- You receive a CatalystEventBundle with recent events, upcoming catalysts, filings, market context, optional position context, optional prior thesis, source quality, and freshness.
- The bundle's `macro_events` carry cross-asset / regime / positioning context (risk-off tape, rising-rate impulse, oil shock, elevated volatility, crowded momentum factor, sector distribution, OPEX/quarter-end). These are pullback-risk signals: a crowded/extended sector can unwind on a soft or second-order catalyst (a peer's guidance tone, a policy trial balloon, a foreign-market shock) with no company-specific bad news. Weigh material `macro_events` in `event_risk_rating`, `thesis_break_score`, and `risk_controls`, and surface them in the evidence_table when relevant.

Output contract:
- Produce one JSON object between BEGIN_CATALYST_EVENT_REPORT_JSON and END_CATALYST_EVENT_REPORT_JSON.
- JSON fields: ticker, as_of, event_risk_rating, catalyst_score, thesis_break_score, thesis_support_score, near_term_catalysts, recent_material_events, thesis_supporting_events, thesis_breaking_events, unresolved_questions, recommended_action, action_rationale, risk_controls, evidence_table.
- event_risk_rating must be LOW, MEDIUM, HIGH, or CRITICAL.
- recommended_action must be one of: continue_analysis, rerun_full_analysis, risk_judge_review, freeze_new_buys, reduce_position, exit_review, watchlist_only, ignore_low_materiality.
- evidence_table rows require source, event_type, date, claim, thesis_impact, confidence, and url.

Current bundle:
{json.dumps(bundle, ensure_ascii=False)}

Use HIGH/CRITICAL only for discrete material events, near-term timing risk, likely thesis breaks, or severe position-aware risk. Prefer LOW/MEDIUM when evidence is sparse.
"""
        system_message += "\n\n---\nANALYST WORKBENCH DISCOVERY LANE:\n"
        system_message += build_workbench_prompt_block("catalyst", selected_question)

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant with access to tools: {tool_names}.\n{system_message}\n"
                    "For reference, current date is {current_date}; ticker is {ticker}.",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | (llm if not tools else bind_tools_parallel_safe(llm, tools))
        result = chain.invoke(state["messages"])
        tool_calls_count = len(getattr(result, "tool_calls", None) or [])
        tooling_state = build_tooling_state_update(state, "catalyst", tool_calls_count)
        tool_link_update = {}
        if tool_calls_count > 0:
            link_state = state
            for tool_call in getattr(result, "tool_calls", None) or []:
                tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
                link_state = record_tool_call_links(
                    link_state, "catalyst", str(tool_name or ""), selected_question, tool_calls_count=1
                )
            tool_link_update = {
                "analyst_tool_call_links": link_state.get(
                    "analyst_tool_call_links", state.get("analyst_tool_call_links", {})
                )
            }

        report = ""
        structured_report = {}
        parse_telemetry = {}
        ledger = None
        evidence = ""
        workbench_metrics_update = {}
        if tool_calls_count == 0:
            structured_report, parse_telemetry = parse_catalyst_report(
                result,
                bundle,
                include_telemetry=True,
            )
            report = format_catalyst_report_markdown(structured_report)
            ledger = _ledger_from_report(structured_report)
            evidence = build_ledger_evidence_summary("catalyst", ledger) or build_report_evidence_summary(
                "catalyst", report
            )
            workbench_metrics_update = merge_workbench_metrics(
                {**state, **tool_link_update},
                "catalyst",
                build_workbench_metrics(ledger),
            )

        out = {
            "messages": [result],
            "catalyst_report": report,
            "catalyst_event_bundle": bundle,
            "catalyst_event_report_structured": structured_report,
            "catalyst_parse_telemetry": parse_telemetry,
            "catalyst_evidence": evidence,
            "force_no_tools_for": "",
            **tooling_state,
            **tool_link_update,
            **workbench_metrics_update,
        }
        if ledger is not None:
            out["catalyst_ledger"] = ledger
        return out

    return catalyst_event_analyst_node
