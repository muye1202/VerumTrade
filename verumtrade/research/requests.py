"""Research-request edge between the research manager and the research controller.

The manager may ask for bounded research instead of re-arguing a contested premise.
The controller's results come back as a section listing each task's outcome and the
canonical E-* evidence IDs that now back it, so a revised thesis can cite them.
"""

from __future__ import annotations

import json
from typing import Any

from verumtrade.research.tasks import TERMINAL_STATUSES, ResearchTask, dedupe_tasks, tasks_from_manager_requests

REQUEST_MARKER = "RESEARCH_REQUEST_JSON"


def research_request_prompt_block(reopens_left: int) -> str:
    if reopens_left <= 0:
        return (
            "\nResearch budget: no further research can be requested. Where evidence is missing, narrow "
            "the claim and list it under unresolved_uncertainties with its decision_effect."
        )
    return f"""
Optional research request ({reopens_left} request round(s) left): if a material premise of your thesis is
unresolved and a specific lookup could settle it, append a block named {REQUEST_MARKER}. Do not request
research just to add detail; each request must have at least two candidate answers that would lead to
different decisions. Allowed source_scope values: sec_filings, sec_financials, news, market, fundamentals.
{REQUEST_MARKER}
{{"requests": [{{"question": "Does the latest 8-K describe binding HBM orders or only customer qualification?",
  "decision_relevance": "C-002 depends on it: supports entering before earnings",
  "candidate_answers": [{{"answer": "binding orders disclosed", "decision_effect": "execution_mode=act_now"}},
                        {{"answer": "qualification only", "decision_effect": "execution_mode=wait_for_trigger"}}],
  "evidence_target": "EX-99.1 of the most recent earnings 8-K",
  "source_scope": ["sec_filings"],
  "resolution_test": "A filing passage states whether purchase commitments exist"}}]}}
Still include THESIS_LEDGER_JSON with your current best thesis; it will be revised after the research returns."""


def extract_research_requests(text: Any) -> list[dict[str, Any]]:
    content = str(text or "")
    idx = content.upper().rfind(REQUEST_MARKER)
    if idx < 0:
        return []
    tail = content[idx + len(REQUEST_MARKER):]
    start = tail.find("{")
    if start < 0:
        return []
    depth, end = 0, -1
    for pos, char in enumerate(tail[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end < 0:
        return []
    try:
        parsed = json.loads(tail[start:end])
    except json.JSONDecodeError:
        return []
    requests = parsed.get("requests") if isinstance(parsed, dict) else parsed
    return [item for item in requests or [] if isinstance(item, dict)]


def apply_manager_requests(
    state: dict[str, Any],
    content: Any,
    *,
    max_reopens: int,
    max_tool_calls_per_task: int,
) -> dict[str, Any]:
    """Turn the manager's requests into post-manager tasks and decide whether to reopen research."""
    tasks: list[ResearchTask] = [dict(t) for t in state.get("research_tasks") or []]  # type: ignore[misc]
    reopens = int(state.get("research_manager_reopens") or 0)
    requested = tasks_from_manager_requests(
        extract_research_requests(content),
        start_index=len(tasks) + 1,
        max_tool_calls=max_tool_calls_per_task,
    )
    requested = dedupe_tasks(requested, tasks)
    if requested and reopens >= max_reopens:
        for task in requested:
            if task["status"] == "open":
                task["status"] = "budget_exhausted"
                task["status_reason"] = f"manager research reopen limit ({max_reopens}) reached"
    tasks.extend(requested)
    reopen = any(t["status"] == "open" for t in requested)
    return {
        "research_tasks": tasks,
        "research_phase": "post_manager" if reopen else "final",
        "research_manager_reopens": reopens + (1 if reopen else 0),
    }


def _fact_to_evidence_ids(state: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("source_ref")): str(item.get("evidence_id"))
        for item in state.get("evidence_ledger") or []
        if isinstance(item, dict) and item.get("source_ref") and item.get("evidence_id")
    }


def format_research_results(state: dict[str, Any]) -> str:
    """Outcome of every finished research task, with the E-* IDs a revised thesis may cite."""
    tasks = [t for t in state.get("research_tasks") or [] if t.get("status") in TERMINAL_STATUSES]
    if not tasks:
        return ""
    mapping = _fact_to_evidence_ids(state)
    lines = [
        "RESEARCH RESULTS (from the research controller; revise the thesis where they bear on it):",
    ]
    known = set(mapping.values())
    rejected = {
        str(item.get("evidence_id")): str(item.get("reason") or "rejected")
        for item in (state.get("admissibility_report") or {}).get("rejected_evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for task in tasks:
        evidence = [mapping.get(fid, fid) for fid in task.get("resolution_fact_ids") or []]
        # Only IDs that exist in the ledger and passed admissibility may be offered for citation.
        citable = [eid for eid in evidence if eid in known and eid not in rejected]
        uncitable = [eid for eid in evidence if eid not in citable]
        line = f"- [{task['task_id']}] {task['status'].upper()}: {task['question']}"
        if task.get("answer"):
            line += f" -> {task['answer']}"
        if citable and task["status"] == "resolved":
            line += f" (evidence: {', '.join(citable)})"
        if uncitable and task["status"] == "resolved":
            reasons = "; ".join(f"{eid}: {rejected.get(eid, 'not in the evidence ledger')}" for eid in uncitable)
            line += f" (NOT citable: {reasons})"
        if task.get("status_reason"):
            line += f" [{task['status_reason']}]"
        lines.append(line)
    lines.append(
        "Resolved tasks may be cited by their evidence IDs. Unanswerable, rejected or budget-exhausted tasks "
        "must narrow the affected claims or appear under unresolved_uncertainties; do not treat them as settled."
    )
    return "\n".join(lines)

