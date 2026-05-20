from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, TypedDict

from tradingagents.agents.analysts.workbench import normalize_ledger
from tradingagents.agents.utils.agent_runtime.context_budget import cap_section, get_budget_settings


EvidenceDomain = Literal["market", "sentiment", "news", "fundamentals", "catalyst"]
EvidenceAudience = Literal["bull", "bear", "research_manager", "trader", "risk"]


class EvidenceFact(TypedDict, total=False):
    id: str
    domain: str
    claim: str
    text: str
    source: str
    section: str
    as_of: str
    confidence: float
    quality: str
    source_type: str
    source_ids: List[str]


class EvidenceInference(TypedDict, total=False):
    id: str
    domain: str
    analyst: str
    claim: str
    depends_on: List[str]
    support_fact_ids: List[str]
    counter_fact_ids: List[str]
    source_observation_ids: List[str]
    confidence: float
    falsifier: str
    source_hypothesis_id: str
    stance: str


class EvidenceConflict(TypedDict, total=False):
    claim_a: str
    claim_b: str
    reason: str
    inference_ids: List[str]
    fact_ids: List[str]
    confidence: float


class EvidenceAuditIssue(TypedDict, total=False):
    code: str
    severity: str
    message: str
    domain: str
    node_id: str


class EvidenceGraph(TypedDict, total=False):
    facts: List[EvidenceFact]
    inferences: List[EvidenceInference]
    conflicts: List[EvidenceConflict]
    audit_issues: List[EvidenceAuditIssue]
    generated_from: str


class DecisionTrace(TypedDict, total=False):
    decision: Dict[str, Any]
    thesis: Dict[str, Any]
    inference_ids: List[str]
    fact_ids: List[str]
    source_labels: List[str]
    audit_issues: List[EvidenceAuditIssue]


DOMAINS = ["catalyst", "market", "sentiment", "news", "fundamentals"]
LOW_QUALITY = {"stale", "low_quality", "contradictory", "missing"}
BULLISH_TERMS = {
    "above",
    "accelerat",
    "breakout",
    "bull",
    "buy",
    "continue",
    "expanding",
    "follow-through",
    "growth",
    "momentum",
    "positive",
    "support",
    "upside",
    "valid",
}
BEARISH_TERMS = {
    "bear",
    "below",
    "chasing",
    "contradict",
    "downside",
    "elevated",
    "exhaust",
    "fade",
    "fatigue",
    "overextended",
    "pullback",
    "risk",
    "sell",
    "stale",
    "unverified",
    "weak",
}


def _empty_graph() -> EvidenceGraph:
    return {
        "facts": [],
        "inferences": [],
        "conflicts": [],
        "audit_issues": [],
        "generated_from": "vendor_facts_plus_analyst_inferences",
    }


def _clean_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(0.0, min(1.0, number))


def _normalize_fact(raw: Any) -> EvidenceFact | None:
    if not isinstance(raw, dict):
        return None
    fact_id = str(raw.get("id") or "").strip()
    claim = str(raw.get("claim") or raw.get("text") or "").strip()
    if not fact_id or not claim:
        return None
    source = str(raw.get("source") or raw.get("section") or "unknown").strip()
    fact: EvidenceFact = {
        "id": fact_id,
        "domain": str(raw.get("domain") or "market").strip().lower(),
        "claim": claim,
        "text": str(raw.get("text") or claim).strip(),
        "source": source,
        "section": str(raw.get("section") or source).strip(),
        "as_of": str(raw.get("as_of") or raw.get("date") or "").strip(),
        "confidence": _clamp_confidence(raw.get("confidence"), 0.75),
        "quality": str(raw.get("quality") or "normal").strip().lower(),
        "source_type": str(raw.get("source_type") or "vendor").strip().lower(),
    }
    if raw.get("source_ids"):
        fact["source_ids"] = [str(item) for item in raw.get("source_ids") or [] if str(item)]
    return fact


def _facts_from_catalyst_bundle(packet: dict[str, Any]) -> list[EvidenceFact]:
    if not isinstance(packet, dict):
        return []
    if packet.get("bundle") != "CatalystEventBundle" and not (
        "recent_events" in packet or "upcoming_events" in packet or "recent_filings" in packet
    ):
        return []

    facts: list[EvidenceFact] = []
    ticker = str(packet.get("ticker") or packet.get("symbol") or "").strip()
    as_of = str(packet.get("as_of") or packet.get("date") or "").strip()

    for raw_event in list(packet.get("recent_events") or []) + list(packet.get("upcoming_events") or []):
        if not isinstance(raw_event, dict):
            continue
        event_id = str(raw_event.get("event_id") or raw_event.get("source_event_id") or "").strip()
        claim = str(raw_event.get("title") or raw_event.get("summary") or "").strip()
        if not event_id or not claim:
            continue
        fact = _normalize_fact(
            {
                "id": event_id,
                "domain": "catalyst",
                "claim": claim,
                "text": str(raw_event.get("summary") or claim).strip(),
                "source": raw_event.get("source") or "catalyst_event_bundle",
                "section": raw_event.get("event_type") or "event",
                "as_of": raw_event.get("event_time") or raw_event.get("detected_at") or as_of,
                "confidence": raw_event.get("confidence", 0.75),
                "quality": "normal",
                "source_type": "vendor",
                "source_ids": [ticker] if ticker else [],
            }
        )
        if fact:
            facts.append(fact)

    for raw_filing in packet.get("recent_filings") or []:
        if not isinstance(raw_filing, dict):
            continue
        filing_id = str(raw_filing.get("accession_number") or "").strip()
        form = str(raw_filing.get("form_type") or "SEC filing").strip()
        summary = str(raw_filing.get("filing_summary") or f"{form} filed").strip()
        if not filing_id:
            continue
        fact = _normalize_fact(
            {
                "id": filing_id,
                "domain": "catalyst",
                "claim": summary,
                "text": summary,
                "source": raw_filing.get("primary_document_url") or "recent_sec_filings",
                "section": form,
                "as_of": raw_filing.get("filing_date") or as_of,
                "confidence": raw_filing.get("materiality_score", 0.75),
                "quality": "normal",
                "source_type": "vendor",
                "source_ids": [ticker] if ticker else [],
            }
        )
        if fact:
            facts.append(fact)

    return facts


def _json_objects_from_text(text: Any) -> list[dict[str, Any]]:
    content = str(text or "").strip()
    if not content:
        return []
    candidates = [content]
    if "{" in content and "}" in content:
        candidates.append(content[content.find("{") : content.rfind("}") + 1])
    out: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def extract_evidence_facts_from_messages(messages: Any) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    for message in messages or []:
        content = getattr(message, "content", message)
        for packet in _json_objects_from_text(content):
            facts.extend(_facts_from_catalyst_bundle(packet))
            packet_facts = packet.get("facts")
            if not isinstance(packet_facts, list):
                continue
            for item in packet_facts:
                fact = _normalize_fact(item)
                if fact:
                    if not fact.get("as_of"):
                        fact["as_of"] = str(packet.get("date") or "")
                    facts.append(fact)
    return facts


def merge_evidence_facts(existing: Any, new: Any) -> list[EvidenceFact]:
    merged: dict[str, EvidenceFact] = {}
    for item in list(existing or []) + list(new or []):
        fact = _normalize_fact(item)
        if fact:
            merged[fact["id"]] = fact
    return list(merged.values())


def build_evidence_graph(state: Dict[str, Any] | None) -> EvidenceGraph:
    state = state or {}
    graph = _empty_graph()
    source_facts = merge_evidence_facts([], state.get("evidence_source_facts") or [])
    graph["facts"] = source_facts
    fact_ids = {fact["id"] for fact in source_facts}
    observation_to_facts: dict[str, list[str]] = {}

    for domain in DOMAINS:
        ledger = normalize_ledger(domain, state.get(f"{domain}_ledger"))
        observations = ledger.get("observations", [])
        hypotheses = ledger.get("active_hypotheses", [])
        if not observations:
            graph["audit_issues"].append(
                {
                    "code": "domain_without_observations",
                    "severity": "medium",
                    "message": f"{domain} ledger has no observations linked to vendor facts.",
                    "domain": domain,
                }
            )
        if not hypotheses:
            graph["audit_issues"].append(
                {
                    "code": "domain_without_inferences",
                    "severity": "medium",
                    "message": f"{domain} ledger has no active hypotheses for graph inferences.",
                    "domain": domain,
                }
            )

        for idx, obs in enumerate(observations, 1):
            obs_id = str(obs.get("id") or f"obs_{domain}_{idx:03d}")
            valid_refs = [
                str(ref)
                for ref in obs.get("source_fact_ids", []) or []
                if str(ref) in fact_ids
            ]
            missing_refs = [
                str(ref)
                for ref in obs.get("source_fact_ids", []) or []
                if str(ref) and str(ref) not in fact_ids
            ]
            for missing in missing_refs:
                graph["audit_issues"].append(
                    {
                        "code": "missing_source_fact_id",
                        "severity": "high",
                        "message": f"{obs_id} cites missing vendor fact {missing}.",
                        "domain": domain,
                        "node_id": obs_id,
                    }
                )
            if not valid_refs:
                recovery_id = f"fact_{domain}_{_clean_id(obs_id)}_recovery"
                if recovery_id not in fact_ids:
                    graph["facts"].append(
                        {
                            "id": recovery_id,
                            "domain": domain,
                            "claim": str(obs.get("claim") or "").strip(),
                            "text": str(obs.get("claim") or "").strip(),
                            "source": f"ledger:{domain}:observation_recovery",
                            "section": "ledger_recovery",
                            "as_of": str(state.get("trade_date") or ""),
                            "confidence": 0.35,
                            "quality": "recovery",
                            "source_type": "ledger_recovery",
                            "source_ids": [obs_id],
                        }
                    )
                    fact_ids.add(recovery_id)
                valid_refs = [recovery_id]
                graph["audit_issues"].append(
                    {
                        "code": "unsupported_ledger_observation",
                        "severity": "high",
                        "message": f"{obs_id} had no valid vendor fact references; recovery fact created.",
                        "domain": domain,
                        "node_id": obs_id,
                    }
                )
            observation_to_facts[obs_id] = valid_refs

        for idx, hypothesis in enumerate(hypotheses, 1):
            hypothesis_id = str(hypothesis.get("id") or f"h_{domain}_{idx:03d}")
            support_fact_ids, support_obs = _resolve_hypothesis_refs(
                graph,
                fact_ids,
                observation_to_facts,
                domain,
                hypothesis_id,
                hypothesis.get("support", []),
                "support",
                str(state.get("trade_date") or ""),
            )
            counter_fact_ids, counter_obs = _resolve_hypothesis_refs(
                graph,
                fact_ids,
                observation_to_facts,
                domain,
                hypothesis_id,
                hypothesis.get("against", []),
                "against",
                str(state.get("trade_date") or ""),
            )
            depends_on = [*support_fact_ids, *counter_fact_ids]
            inference: EvidenceInference = {
                "id": f"inf_{domain}_{idx:03d}",
                "domain": domain,
                "analyst": domain,
                "claim": str(hypothesis.get("claim") or "").strip(),
                "depends_on": depends_on,
                "support_fact_ids": support_fact_ids,
                "counter_fact_ids": counter_fact_ids,
                "source_observation_ids": sorted(set([*support_obs, *counter_obs])),
                "confidence": _clamp_confidence(hypothesis.get("confidence"), 0.0),
                "falsifier": str(hypothesis.get("falsifier") or "").strip(),
                "source_hypothesis_id": hypothesis_id,
                "stance": _infer_stance(
                    hypothesis.get("claim"),
                    list(hypothesis.get("support", []) or []),
                    list(hypothesis.get("against", []) or []),
                ),
            }
            graph["inferences"].append(inference)
            if not depends_on:
                graph["audit_issues"].append(
                    {
                        "code": "inference_without_vendor_fact_links",
                        "severity": "high",
                        "message": f"{inference['id']} has no vendor fact dependencies.",
                        "domain": domain,
                        "node_id": inference["id"],
                    }
                )

    graph["conflicts"] = _detect_conflicts(graph)
    return graph


def _resolve_hypothesis_refs(
    graph: EvidenceGraph,
    fact_ids: set[str],
    observation_to_facts: dict[str, list[str]],
    domain: str,
    hypothesis_id: str,
    refs: Any,
    kind: str,
    as_of: str,
) -> tuple[list[str], list[str]]:
    fact_refs: list[str] = []
    obs_refs: list[str] = []
    for idx, raw_ref in enumerate(refs or [], 1):
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        if ref in observation_to_facts:
            obs_refs.append(ref)
            fact_refs.extend(observation_to_facts[ref])
        elif ref in fact_ids:
            fact_refs.append(ref)
        else:
            recovery_id = f"fact_{domain}_{_clean_id(hypothesis_id)}_{kind}_{idx:03d}_recovery"
            if recovery_id not in fact_ids:
                graph["facts"].append(
                    {
                        "id": recovery_id,
                        "domain": domain,
                        "claim": ref,
                        "text": ref,
                        "source": f"ledger:{domain}:{hypothesis_id}:{kind}_recovery",
                        "section": "ledger_recovery",
                        "as_of": as_of,
                        "confidence": 0.35,
                        "quality": "recovery",
                        "source_type": "ledger_recovery",
                        "source_ids": [hypothesis_id],
                    }
                )
                fact_ids.add(recovery_id)
                graph["audit_issues"].append(
                    {
                        "code": "unsupported_hypothesis_evidence",
                        "severity": "high",
                        "message": f"{hypothesis_id} {kind} entry lacked an observation or vendor fact ID.",
                        "domain": domain,
                        "node_id": hypothesis_id,
                    }
                )
            fact_refs.append(recovery_id)
    return list(dict.fromkeys(fact_refs)), obs_refs


def _infer_stance(claim: Any, support: list[str] | None = None, against: list[str] | None = None) -> str:
    text = " ".join([str(claim or ""), *(support or []), *(against or [])]).lower()
    bull_score = sum(1 for term in BULLISH_TERMS if term in text)
    bear_score = sum(1 for term in BEARISH_TERMS if term in text)
    if bull_score > bear_score:
        return "bullish"
    if bear_score > bull_score:
        return "bearish"
    return "mixed"


def _detect_conflicts(graph: EvidenceGraph) -> list[EvidenceConflict]:
    conflicts: list[EvidenceConflict] = []
    fact_lookup = {fact["id"]: fact for fact in graph.get("facts", [])}
    for inference in graph.get("inferences", []):
        weak_facts = [
            fact_lookup[fact_id]
            for fact_id in inference.get("depends_on", [])
            if fact_id in fact_lookup
            and (
                str(fact_lookup[fact_id].get("quality") or "").lower() in LOW_QUALITY
                or str(fact_lookup[fact_id].get("source_type") or "") == "ledger_recovery"
            )
        ]
        if _clamp_confidence(inference.get("confidence"), 0.0) >= 0.70 and weak_facts:
            conflicts.append(
                {
                    "claim_a": str(inference.get("claim") or ""),
                    "claim_b": "; ".join(str(fact.get("claim") or "") for fact in weak_facts[:3]),
                    "reason": "high-confidence inference depends on weak or recovery facts",
                    "inference_ids": [str(inference.get("id") or "")],
                    "fact_ids": [str(fact.get("id") or "") for fact in weak_facts],
                    "confidence": _clamp_confidence(inference.get("confidence"), 0.0),
                }
            )
    return conflicts


def create_capture_evidence_facts_node(domain: str):
    def capture_evidence_facts_node(state) -> dict:
        new_facts = extract_evidence_facts_from_messages(state.get("messages", []))
        merged = merge_evidence_facts(state.get("evidence_source_facts", []), new_facts)
        graph: EvidenceGraph = {
            "facts": merged,
            "inferences": [],
            "conflicts": [],
            "audit_issues": [],
            "generated_from": "vendor_facts",
        }
        return {
            "evidence_source_facts": merged,
            "evidence_graph": graph,
            "evidence_graph_audit": graph["audit_issues"],
        }

    return capture_evidence_facts_node


def create_evidence_graph_node():
    def evidence_graph_node(state) -> dict:
        graph = build_evidence_graph(state)
        return {
            "evidence_graph": graph,
            "evidence_graph_audit": graph.get("audit_issues", []),
        }

    return evidence_graph_node


def _graph_from_state(state: Dict[str, Any] | None) -> EvidenceGraph:
    state = state or {}
    graph = state.get("evidence_graph")
    if isinstance(graph, dict) and isinstance(graph.get("facts"), list):
        return graph
    return build_evidence_graph(state)


def format_evidence_projection(
    state: Dict[str, Any] | None,
    audience: EvidenceAudience,
    *,
    max_chars: int | None = None,
) -> str:
    graph = _graph_from_state(state)
    settings = get_budget_settings()
    limit = int(max_chars or settings["section_max_chars_report"] * 2)
    title = str(audience).replace("_", " ").title()
    lines = [f"# Evidence Graph Projection: {title}"]
    _append_catalyst_risk_snapshot(lines, state or {})
    if audience == "bull":
        lines.append("Bullish inferences:")
        _append_inferences(lines, graph, stance="bullish")
        lines.append("Counter-evidence and unresolved conflicts:")
        _append_counter_evidence(lines, graph)
    elif audience == "bear":
        lines.append("Bearish/risk inferences:")
        _append_inferences(lines, graph, stance="bearish")
        lines.append("Weak assumptions:")
        _append_weak_assumptions(lines, graph)
    elif audience == "research_manager":
        lines.append("Balanced thesis evidence:")
        _append_inferences(lines, graph)
        lines.append("Decision-relevant conflicts:")
        _append_conflicts(lines, graph)
        lines.append("Instruction: cite evidence inference IDs when selecting the thesis.")
    elif audience == "trader":
        lines.append("Chosen thesis evidence:")
        _append_inferences(lines, graph, max_items=5)
        lines.append("Levels, invalidation, and sizing constraints:")
        _append_falsifiers(lines, graph)
    else:
        lines.append("Evidence quality and weakest assumptions:")
        _append_weak_assumptions(lines, graph)
        lines.append("Risk asymmetry and traceable facts:")
        _append_conflicts(lines, graph)
        _append_top_facts(lines, graph)
    return cap_section(f"evidence_projection_{audience}", "\n".join(lines), limit)


def _append_catalyst_risk_snapshot(lines: list[str], state: Dict[str, Any]) -> None:
    report = state.get("catalyst_event_report_structured") or {}
    bundle = state.get("catalyst_event_bundle") or {}
    if not isinstance(report, dict) or not report:
        return
    lines.append("CATALYST RISK SNAPSHOT")
    lines.append(f"- rating: {report.get('event_risk_rating', 'UNKNOWN')}")
    lines.append(f"- action: {report.get('recommended_action', 'continue_analysis')}")

    events = [event for event in bundle.get("recent_events", []) or [] if isinstance(event, dict)]
    if events:
        lines.append("- top accepted events:")
        for event in sorted(
            events,
            key=lambda item: float(item.get("materiality_score", 0.0) or 0.0),
            reverse=True,
        )[:3]:
            lines.append(
                "- {title}, relevance {relevance:.2f}, materiality {materiality:.2f}".format(
                    title=str(event.get("title") or event.get("summary") or "accepted catalyst")[:140],
                    relevance=float(event.get("relevance_score", 0.0) or 0.0),
                    materiality=float(event.get("materiality_score", 0.0) or 0.0),
                )
            )

    upcoming = report.get("near_term_catalysts") or []
    if upcoming:
        lines.append("- upcoming catalysts:")
        for item in upcoming[:3]:
            lines.append(f"- {str(item)[:160]}")

    breaking = report.get("thesis_breaking_events") or []
    if breaking:
        lines.append("- thesis-breaking events:")
        for item in breaking[:3]:
            lines.append(f"- {str(item)[:160]}")

    source_quality = bundle.get("source_quality") or {}
    data_notes = list(report.get("data_quality_notes") or [])
    for name, item in source_quality.items():
        if isinstance(item, dict) and item.get("status") in {"degraded", "contaminated", "missing", "sparse", "failed"}:
            data_notes.append(
                f"{name} {item.get('status')}, score {float(item.get('contamination_score', 0.0) or 0.0):.2f}"
            )
    if data_notes:
        lines.append("- data quality:")
        for item in data_notes[:5]:
            lines.append(f"- {str(item)[:180]}")

    controls = report.get("risk_controls") or []
    if controls:
        lines.append("- risk controls:")
        for item in controls[:4]:
            lines.append(f"- {str(item)[:180]}")


def _append_inferences(lines: list[str], graph: EvidenceGraph, *, stance: str | None = None, max_items: int = 6) -> None:
    items = list(graph.get("inferences", []))
    if stance:
        items = [item for item in items if item.get("stance") == stance]
    items = sorted(items, key=lambda item: float(item.get("confidence", 0.0) or 0.0), reverse=True)
    if not items:
        lines.append("- None identified.")
        return
    for inference in items[:max_items]:
        lines.append(
            "- {id} [{domain}/{stance}] {claim} "
            "(confidence {confidence:.2f}; facts: {facts}; observations: {obs}; falsifier: {falsifier})".format(
                id=inference.get("id", ""),
                domain=inference.get("domain", ""),
                stance=inference.get("stance", "mixed"),
                claim=inference.get("claim", ""),
                confidence=float(inference.get("confidence", 0.0) or 0.0),
                facts=", ".join(inference.get("depends_on", [])[:5]) or "none",
                obs=", ".join(inference.get("source_observation_ids", [])[:5]) or "none",
                falsifier=inference.get("falsifier") or "missing",
            )
        )


def _append_counter_evidence(lines: list[str], graph: EvidenceGraph) -> None:
    fact_lookup = {fact["id"]: fact for fact in graph.get("facts", [])}
    entries: list[str] = []
    for inference in graph.get("inferences", []):
        for fact_id in inference.get("counter_fact_ids", []):
            fact = fact_lookup.get(fact_id)
            if fact:
                entries.append(f"- {fact_id}: {fact.get('claim')} ({fact.get('source_type')})")
    if entries:
        lines.extend(entries[:6])
    else:
        _append_conflicts(lines, graph)


def _append_weak_assumptions(lines: list[str], graph: EvidenceGraph) -> None:
    issues = list(graph.get("audit_issues", []))
    if issues:
        for issue in issues[:8]:
            lines.append(f"- [{issue.get('severity')}] {issue.get('code')}: {issue.get('message')}")
    weak = [item for item in graph.get("inferences", []) if not item.get("depends_on") or float(item.get("confidence", 0.0) or 0.0) < 0.55]
    for inference in weak[:4]:
        lines.append(f"- {inference.get('id')}: thin or low-confidence inference: {inference.get('claim')}")
    if not issues and not weak:
        lines.append("- No deterministic weak assumption flags.")


def _append_conflicts(lines: list[str], graph: EvidenceGraph) -> None:
    conflicts = list(graph.get("conflicts", []))
    if not conflicts:
        lines.append("- None identified.")
        return
    for conflict in conflicts[:6]:
        lines.append(
            f"- {conflict.get('reason')}: {conflict.get('claim_a')} vs {conflict.get('claim_b')} "
            f"(inferences: {', '.join(conflict.get('inference_ids', []))}; facts: {', '.join(conflict.get('fact_ids', []))})"
        )


def _append_falsifiers(lines: list[str], graph: EvidenceGraph) -> None:
    items = [item for item in graph.get("inferences", []) if item.get("falsifier")]
    if not items:
        lines.append("- No falsifiers provided.")
        return
    for inference in items[:6]:
        lines.append(f"- {inference.get('id')}: {inference.get('falsifier')}")


def _append_top_facts(lines: list[str], graph: EvidenceGraph) -> None:
    facts = sorted(graph.get("facts", []), key=lambda fact: float(fact.get("confidence", 0.0) or 0.0), reverse=True)
    if not facts:
        lines.append("- No traceable vendor facts.")
        return
    for fact in facts[:6]:
        lines.append(
            f"- {fact.get('id')} [{fact.get('source')}] {fact.get('claim')} "
            f"(confidence {float(fact.get('confidence', 0.0) or 0.0):.2f}; source_type {fact.get('source_type')})"
        )


def build_decision_trace(state: Dict[str, Any] | None, final_decision_text: Any | None = None) -> DecisionTrace:
    state = state or {}
    graph = _graph_from_state(state)
    decision_text = str(final_decision_text if final_decision_text is not None else state.get("final_trade_decision", ""))
    structured = state.get("final_trade_decision_structured")
    action = str(structured.get("action") or "").upper() if isinstance(structured, dict) else ""
    if not action:
        match = re.search(r"\b(BUY|SELL|HOLD)\b", decision_text, flags=re.IGNORECASE)
        action = match.group(1).upper() if match else ""
    selected = _select_trace_inferences(graph, decision_text, action)
    inference_ids = [str(item.get("id") or "") for item in selected if item.get("id")]
    fact_ids = sorted({fact_id for inference in selected for fact_id in inference.get("depends_on", []) if fact_id})
    fact_lookup = {fact["id"]: fact for fact in graph.get("facts", [])}
    source_labels = sorted(
        {
            str(fact_lookup[fact_id].get("source") or "")
            for fact_id in fact_ids
            if fact_id in fact_lookup and fact_lookup[fact_id].get("source")
        }
    )
    audit_issues = list(graph.get("audit_issues", []))
    if not inference_ids:
        audit_issues.append({"code": "trace_missing_inference_links", "severity": "high", "message": "Final decision trace does not link to any evidence inferences."})
    if not fact_ids:
        audit_issues.append({"code": "trace_missing_fact_links", "severity": "high", "message": "Final decision trace does not link to any evidence facts."})
    return {
        "decision": {
            "action": action,
            "ticker": state.get("company_of_interest", ""),
            "summary": decision_text[:500],
        },
        "thesis": {
            "claim": selected[0].get("claim", "") if selected else "",
            "inference_ids": inference_ids,
        },
        "inference_ids": inference_ids,
        "fact_ids": fact_ids,
        "source_labels": source_labels,
        "audit_issues": audit_issues,
    }


def _select_trace_inferences(graph: EvidenceGraph, decision_text: str, action: str) -> list[EvidenceInference]:
    inferences = list(graph.get("inferences", []))
    if not inferences:
        return []
    desired = "bullish" if action == "BUY" else "bearish" if action == "SELL" else ""
    text = decision_text.lower()

    def score(inference: EvidenceInference) -> tuple[float, float]:
        claim = str(inference.get("claim") or "").lower()
        words = {word for word in re.split(r"\W+", claim) if len(word) >= 4}
        overlap = sum(1 for word in words if word in text)
        stance_bonus = 2 if desired and inference.get("stance") == desired else 0
        return (overlap + stance_bonus, float(inference.get("confidence", 0.0) or 0.0))

    ranked = sorted(inferences, key=score, reverse=True)
    selected = [item for item in ranked if score(item)[0] > 0][:4]
    return selected or ranked[: min(3, len(ranked))]
