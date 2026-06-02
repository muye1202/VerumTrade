from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from opentrace.agents.analysts.workbench import (
    AnalystHypothesis,
    AnalystLedger,
    AnalystQuestion,
    CoverageGap,
    normalize_ledger,
)


DISCOVERY_ORIGINS = {
    "anomaly_generated",
    "cross_domain_signal",
    "critic_generated",
    "memory_retrieved",
}


def calculate_question_priority(question: Dict[str, Any]) -> float:
    try:
        relevance = float(question.get("decision_relevance", 0.0) or 0.0)
        information_gain = float(question.get("expected_information_gain", 0.0) or 0.0)
        surprise = float(question.get("evidence_surprise", 0.0) or 0.0)
        cost = float(question.get("estimated_tool_cost", 1.0) or 1.0)
    except Exception:
        return 0.0
    return (relevance * information_gain * surprise) / max(cost, 0.25)


def filter_hypotheses_for_caps(
    hypotheses: Iterable[Dict[str, Any]],
    *,
    max_active: int = 4,
    max_default_prior: int = 2,
    max_anomaly_generated: int = 2,
    max_cross_domain_signal: int = 1,
) -> list[AnalystHypothesis]:
    selected: list[AnalystHypothesis] = []
    counts = {"default_prior": 0, "anomaly_generated": 0, "cross_domain_signal": 0}

    ranked = sorted(
        list(hypotheses or []),
        key=lambda item: float(item.get("confidence", 0.0) or 0.0),
        reverse=True,
    )

    for raw in ranked:
        origin = str(raw.get("origin") or "default_prior")
        if origin == "default_prior" and counts["default_prior"] >= max_default_prior:
            continue
        if origin == "anomaly_generated" and counts["anomaly_generated"] >= max_anomaly_generated:
            continue
        if origin == "cross_domain_signal" and counts["cross_domain_signal"] >= max_cross_domain_signal:
            continue
        selected.append(raw)  # type: ignore[arg-type]
        if origin in counts:
            counts[origin] += 1
        if len(selected) >= max_active:
            break

    return selected


def run_coverage_critic(ledger: AnalystLedger) -> list[CoverageGap]:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    gaps: list[CoverageGap] = []
    active_support_and_against: set[str] = set()
    for hypothesis in normalized["active_hypotheses"]:
        active_support_and_against.update(str(item) for item in hypothesis.get("support", []))
        active_support_and_against.update(str(item) for item in hypothesis.get("against", []))

    for obs in normalized["observations"]:
        surprise = float(obs.get("surprise_score", 0.0) or 0.0)
        if surprise < 0.65:
            continue
        if obs.get("status") not in {"unexplained", "contradictory", "stale", "low_quality"}:
            continue
        obs_id = str(obs.get("id") or "")
        if obs_id in active_support_and_against:
            continue
        gaps.append(
            {
                "id": f"gap_{len(gaps) + 1:03d}",
                "gap": f"High-surprise observation not explained by active hypotheses: {obs.get('claim', '')}",
                "related_observations": [obs_id] if obs_id else [],
                "why_it_matters": str(obs.get("why_it_matters") or ""),
                "severity": "high" if surprise >= 0.8 else "medium",
                "suggested_next_question": None,
            }
        )
    return gaps


def select_allowed_question(
    ledger: AnalystLedger,
    exposed_tool_names: Iterable[str] | None = None,
) -> AnalystQuestion | None:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    critic_flags = list(dict(ledger).get("critic_flags") or []) if isinstance(ledger, dict) else []
    resolved = set(normalized.get("resolved_questions", []))
    do_not_fetch_again = set(normalized.get("do_not_fetch_again", []))
    open_ids = set(normalized.get("open_questions", []))
    exposed = set(exposed_tool_names or [])

    candidates: list[AnalystQuestion] = []
    for question in normalized.get("question_backlog", []):
        qid = str(question.get("id") or "")
        tool_name = str(question.get("cheapest_tool") or "")
        if not str(question.get("question") or "").strip():
            critic_flags.append(f"{qid}:blank_question")
        if exposed and tool_name and tool_name not in exposed:
            critic_flags.append(f"{qid}:unknown_cheapest_tool:{tool_name}")
        if not qid or qid in resolved:
            continue
        if open_ids and qid not in open_ids:
            continue
        if tool_name and tool_name in do_not_fetch_again:
            continue
        if exposed and tool_name and tool_name not in exposed:
            continue
        if not question.get("triggered_by") or not question.get("stop_condition"):
            continue
        candidates.append(question)

    if isinstance(ledger, dict):
        ledger["critic_flags"] = list(dict.fromkeys(critic_flags))
    if not candidates:
        return None
    return max(candidates, key=calculate_question_priority)


def select_question_gated_tools(
    state: Dict[str, Any],
    analyst_key: str,
    fallback_tools: list[Any],
    *,
    rounds_used: int,
) -> Tuple[list[Any], AnalystQuestion | None]:
    if int(rounds_used or 0) <= 0:
        return list(fallback_tools), None

    ledger = state.get(f"{analyst_key}_ledger") or {}
    exposed_names = [getattr(tool, "name", "") for tool in fallback_tools]
    question = select_allowed_question(ledger, exposed_names)
    if not question:
        return [], None

    cheapest_tool = str(question.get("cheapest_tool") or "")
    if not cheapest_tool:
        return [], None
    return [tool for tool in fallback_tools if getattr(tool, "name", "") == cheapest_tool], question


def count_blocked_tool_call(state: Dict[str, Any], analyst_key: str, reason: str) -> dict[str, Any]:
    counts = dict(state.get("analyst_tool_call_blocked_counts") or {})
    key = f"{analyst_key}:{reason}"
    counts[key] = int(counts.get(key, 0) or 0) + 1
    return {"analyst_tool_call_blocked_counts": counts}


def record_tool_call_links(
    state: Dict[str, Any],
    analyst_key: str,
    tool_name: str,
    question: AnalystQuestion | None,
    *,
    tool_calls_count: int,
) -> dict[str, Any]:
    if not question or tool_calls_count <= 0:
        return {"analyst_tool_call_links": dict(state.get("analyst_tool_call_links") or {})}

    links = dict(state.get("analyst_tool_call_links") or {})
    analyst_links = list(links.get(analyst_key) or [])
    analyst_links.append(
        {
            "question_id": question.get("id", ""),
            "tool_name": tool_name,
            "reason": question.get("question", ""),
            "expected_information_gain": question.get("expected_information_gain", 0.0),
            "estimated_tool_cost": question.get("estimated_tool_cost", 1.0),
            "tool_calls_count": int(tool_calls_count),
        }
    )
    links[analyst_key] = analyst_links
    return {"analyst_tool_call_links": links}


def merge_workbench_metrics(
    state: Dict[str, Any],
    analyst_key: str,
    metrics: Dict[str, Any],
) -> dict[str, Any]:
    all_metrics = dict(state.get("analyst_workbench_metrics") or {})
    all_metrics[analyst_key] = dict(metrics)

    blocked = state.get("analyst_tool_call_blocked_counts") or {}
    links = state.get("analyst_tool_call_links") or {}
    duplicate_blocks = sum(
        int(v or 0)
        for k, v in dict(blocked).items()
        if str(k).startswith(f"{analyst_key}:")
    )
    resolved = max(1, int(metrics.get("resolved_question_count", 0) or 0))
    linked_tool_calls = sum(
        int(item.get("tool_calls_count", 0) or 0)
        for item in list(dict(links).get(analyst_key) or [])
    )
    all_metrics[analyst_key]["duplicate_or_unlinked_tool_call_blocks"] = duplicate_blocks
    all_metrics[analyst_key]["tool_calls_per_resolved_question"] = linked_tool_calls / resolved
    return {"analyst_workbench_metrics": all_metrics}
