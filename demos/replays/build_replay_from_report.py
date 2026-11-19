"""Build a replay recording from an exported VerumTrade example report.

The web app's analysis WebSocket streams ``system`` and ``chunk`` events while a
run is in progress. This script turns a completed example report (the markdown
under ``demos/example-reports``) into an ordered list of those same events, so
the backend can play a real run back without any LLM or market-data API keys.

Usage:
    python demos/replays/build_replay_from_report.py \
        demos/example-reports/MU-2026-06-23.md demos/replays/MU-2026-06-23.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# <summary> title in the exported report -> report payload key.
SECTION_KEYS = {
    "Catalyst": "catalyst_report",
    "Market": "market_report",
    "Sentiment": "sentiment_report",
    "News": "news_report",
    "Fundamentals": "fundamentals_report",
    "Full Evidence Graph JSON": "evidence_graph",
    "Decision Trace JSON": "decision_trace",
    "Investment Debate State": "investment_debate_state",
    "Risk Debate State": "risk_debate_state",
    "Trader Investment Plan": "trader_investment_plan",
    "Trader Decision Brief": "trader_decision_brief",
    "Trade Setup Diagnosis": "trade_setup_diagnosis",
    "Scenario Analysis": "scenario_analysis",
    "Execution Plan Compiler": "execution_plan_compiler",
    "Trader Self Audit": "trader_self_audit",
    "Final Trade Decision": "final_trade_decision",
    "Market Snapshot": "market_snapshot",
    "Decision Guard": "decision_guard",
    "Tool Cache Metrics": "tool_cache_metrics",
    "Vendor Telemetry": "vendor_telemetry",
    "Analyst Workbench Metrics": "analyst_workbench_metrics",
}

# Analyst order matches the frontend's DEFAULT_ANALYSTS.
ANALYSTS = [
    ("catalyst", "Catalyst Analyst", "catalyst_report"),
    ("market", "Market Analyst", "market_report"),
    ("social", "Social Media Analyst", "sentiment_report"),
    ("news", "News Analyst", "news_report"),
    ("fundamentals", "Fundamentals Analyst", "fundamentals_report"),
]

# Best-effort mapping of data-vendor methods to the analyst that calls them.
METHOD_ANALYST = [
    ("catalyst", ("catalyst", "filing", "earnings_calendar", "sec", "event")),
    ("fundamentals", ("fundamental", "balance", "cashflow", "income", "insider")),
    ("social", ("sentiment", "social", "reddit")),
    ("news", ("news",)),
    ("market", ("stock", "indicator", "option", "price", "quote", "bars")),
]

SUMMARY_RE = re.compile(r"^<summary><strong>(.+?)</strong></summary>\s*$")
FIELD_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$")


def parse_sections(markdown: str) -> dict[str, str]:
    """Return {summary title: raw body} for every <details> block."""
    sections: dict[str, str] = {}
    title: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        match = SUMMARY_RE.match(line)
        if match:
            title, body = match.group(1), []
            continue
        if title is not None and line.strip() == "</details>":
            sections.setdefault(title, "\n".join(body).strip())
            title = None
            continue
        if title is not None:
            body.append(line)
    return sections


def parse_run_snapshot(markdown: str) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    in_block = False
    for line in markdown.splitlines():
        if line.startswith("## Run Snapshot"):
            in_block = True
            continue
        if in_block and line.startswith("## "):
            break
        match = FIELD_RE.match(line) if in_block else None
        if match and match.group(1) not in ("Field", ":--"):
            snapshot[match.group(1)] = match.group(2)
    return snapshot


def decode_body(body: str) -> Any:
    """JSON code blocks become objects; everything else stays markdown text."""
    stripped = body.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return json.loads(stripped[len("```json"):-3])
    return stripped


def analyst_for_method(method: str) -> str:
    lowered = method.lower()
    for analyst, needles in METHOD_ANALYST:
        if any(needle in lowered for needle in needles):
            return analyst
    return "market"


def _is_label_line(line: str) -> bool:
    """Short heading-like lines ("Domain Inference", "Human memo") read badly in excerpts."""
    text = line.strip().strip("*_")
    return len(text) < 32 and not text.startswith(("-", "*")) and not text.endswith((".", ":", ")"))


def first_paragraph(text: str, limit: int = 420) -> str:
    """Pick a short, readable excerpt to show as the agent's streamed message."""
    for block in re.split(r"\n\s*\n", text):
        cleaned = " ".join(
            line.strip().lstrip("-*# ").strip()
            for line in block.splitlines()
            if line.strip()
            and not line.strip().startswith(("|", "```", "<", "#"))
            and not _is_label_line(line)
        )
        if len(cleaned) > 60:
            return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"
    return text[:limit]


def debate_turns(history: str, speakers: tuple[str, ...]) -> list[str]:
    """Split a debate transcript like '\\nBull Analyst: ...\\nBear Analyst: ...' into turns."""
    pattern = r"\n(?=(?:%s):)" % "|".join(re.escape(s) for s in speakers)
    return [turn.strip() for turn in re.split(pattern, history or "") if turn.strip()]


def chunk(updates: list[dict] | None = None, reports: dict | None = None, delay_ms: int = 900) -> dict:
    payload: dict[str, Any] = {"event": "chunk", "updates": updates or []}
    if reports:
        payload["reports"] = reports
    return {"delay_ms": delay_ms, "payload": payload}


def system(content: str, delay_ms: int = 500) -> dict:
    return {"delay_ms": delay_ms, "payload": {"event": "system", "content": content}}


def message(content: str, msg_type: str = "Ai") -> dict:
    return {"event": "message", "type": msg_type, "content": content}


def tool_call(tool: str, args: dict) -> dict:
    return {"event": "tool_call", "tool": tool, "args": args}


def build_events(reports: dict[str, Any], snapshot: dict[str, str]) -> list[dict]:
    ticker = snapshot.get("Ticker", "MU")
    date = snapshot.get("Analysis date", "")
    events: list[dict] = [
        system(f"Fetching portfolio context for {ticker}..."),
        system("Portfolio context loaded."),
        system("Starting analysis stream..."),
    ]

    telemetry = reports.get("vendor_telemetry") or []
    calls_by_analyst: dict[str, list[dict]] = {}
    for entry in telemetry if isinstance(telemetry, list) else []:
        method = entry.get("method")
        if method:
            calls_by_analyst.setdefault(analyst_for_method(method), []).append(entry)

    for analyst, label, report_key in ANALYSTS:
        report = reports.get(report_key)
        if not report:
            continue
        events.append(chunk([message(f"{label}: gathering evidence for {ticker} as of {date}.")], delay_ms=700))
        for entry in calls_by_analyst.get(analyst, []):
            vendor = entry.get("successful_vendor") or "n/a"
            events.append(chunk(
                [tool_call(entry["method"], {"symbol": ticker, "curr_date": date, "vendor": vendor})],
                delay_ms=450,
            ))
        events.append(chunk(
            [message(f"{label}: {first_paragraph(report)}")],
            reports={report_key: report},
            delay_ms=1400,
        ))

    if reports.get("evidence_graph"):
        events.append(chunk(
            [message("Evidence Graph: distilling analyst findings into structured, citable facts.")],
            reports={"evidence_graph": reports["evidence_graph"]},
            delay_ms=1500,
        ))

    invest = reports.get("investment_debate_state")
    if isinstance(invest, dict):
        for turn in debate_turns(invest.get("history", ""), ("Bull Analyst", "Bear Analyst")):
            events.append(chunk([message(turn)], delay_ms=1300))
        events.append(chunk(
            [message(f"Research Manager: {first_paragraph(invest.get('judge_decision', ''))}")],
            reports={"investment_debate_state": invest},
            delay_ms=1300,
        ))

    trader_keys = [
        "trader_decision_brief",
        "trade_setup_diagnosis",
        "scenario_analysis",
        "execution_plan_compiler",
        "trader_self_audit",
    ]
    trader_reports = {key: reports[key] for key in trader_keys if key in reports}
    if trader_reports:
        events.append(chunk(
            [message("Trader: diagnosing the setup, scenarios, and execution plan.")],
            reports=trader_reports,
            delay_ms=1300,
        ))
    if reports.get("trader_investment_plan"):
        events.append(chunk(
            [message(f"Trader: {first_paragraph(reports['trader_investment_plan'])}")],
            reports={"trader_investment_plan": reports["trader_investment_plan"]},
            delay_ms=1300,
        ))

    risk = reports.get("risk_debate_state")
    if isinstance(risk, dict):
        for turn in debate_turns(risk.get("history", ""), ("Risky Analyst", "Safe Analyst", "Neutral Analyst")):
            events.append(chunk([message(turn)], delay_ms=1100))
        events.append(chunk(reports={"risk_debate_state": risk}, delay_ms=600))

    final_keys = [
        "final_trade_decision",
        "final_trade_decision_structured",
        "decision_trace",
        "market_snapshot",
        "decision_guard",
    ]
    final_reports = {key: reports[key] for key in final_keys if key in reports}
    if final_reports:
        events.append(chunk(
            [message(f"Risk Judge: {first_paragraph(str(reports.get('final_trade_decision', '')))}")],
            reports=final_reports,
            delay_ms=1500,
        ))

    diagnostics = {
        key: reports[key]
        for key in ("tool_cache_metrics", "vendor_telemetry", "analyst_workbench_metrics")
        if key in reports
    }
    if diagnostics:
        events.append(chunk(reports=diagnostics, delay_ms=300))
    return events


DECISION_JSON_RE = re.compile(r"BEGIN_DECISION_JSON\s*(\{.*?\})\s*END_DECISION_JSON", re.DOTALL)


def attach_structured_decision(reports: dict[str, Any]) -> None:
    """Recover ``final_trade_decision_structured`` from the risk judge's decision JSON.

    The exported report only keeps the judge's narrative, but the structured decision
    is embedded in it. Re-attaching it lets the UI render the decision contract, and
    keeps the decision trace's action consistent with the judge's actual verdict.
    """
    match = DECISION_JSON_RE.search(str(reports.get("final_trade_decision", "")))
    if not match:
        return
    try:
        structured = json.loads(match.group(1))
    except json.JSONDecodeError:
        return
    reports.setdefault("final_trade_decision_structured", structured)
    action = str(structured.get("action") or "").upper()
    trace_decision = (reports.get("decision_trace") or {}).get("decision")
    if action and isinstance(trace_decision, dict):
        trace_decision["action"] = action


def build_replay(markdown_path: Path) -> dict[str, Any]:
    markdown = markdown_path.read_text(encoding="utf-8")
    sections = parse_sections(markdown)
    reports: dict[str, Any] = {}
    for title, key in SECTION_KEYS.items():
        if title in sections and key not in reports:
            reports[key] = decode_body(sections[title])
    attach_structured_decision(reports)
    snapshot = parse_run_snapshot(markdown)
    return {
        "replay_id": markdown_path.stem,
        "ticker": snapshot.get("Ticker", ""),
        "analysis_date": snapshot.get("Analysis date", ""),
        "time_horizon": snapshot.get("Time horizon", ""),
        "source": f"demos/example-reports/{markdown_path.name}",
        "description": "Recorded VerumTrade run replayed without any API keys.",
        "events": build_events(reports, snapshot),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("markdown_path", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    replay = build_replay(args.markdown_path)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(replay, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(replay['events'])} events to {args.output_path}")


if __name__ == "__main__":
    main()
