"""Research tasks: bounded, decision-linked questions the controller can investigate.

A task is answerable and consequential when it names the question, what the
decision depends on, at least two candidate answers with different decision
effects, where the answer could be found, a resolution test, and a budget.
"Research HBM demand" is not a task; "Does the latest 8-K describe binding HBM
orders or only customer qualification?" is.

Task selection follows an explicit, auditable policy (not a value-of-information
estimate): manager requests and contradiction repairs first, then cross-domain
handoffs, then analyst questions their own tools could not answer; questions whose
candidate answers all imply the same decision are not investigated.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, TypedDict

TaskOrigin = Literal["manager_request", "contradiction", "cross_domain_handoff", "analyst_unanswerable"]
TaskPhase = Literal["pre_debate", "post_manager"]
TaskStatus = Literal[
    "open",
    "resolved",
    "unanswerable",
    "budget_exhausted",
    "not_decision_relevant",
    "rejected_resolution",
]
TERMINAL_STATUSES = {"resolved", "unanswerable", "budget_exhausted", "not_decision_relevant", "rejected_resolution"}

SOURCE_SCOPES = ("sec_filings", "sec_financials", "news", "market", "fundamentals")
DOMAIN_SCOPES = {
    "fundamentals": ["sec_financials", "sec_filings", "fundamentals"],
    "news": ["sec_filings", "news"],
    "catalyst": ["sec_filings", "news"],
    "market": ["market"],
    "sentiment": ["news"],
}
ORIGIN_WEIGHT = {
    "manager_request": 3.0,
    "contradiction": 2.5,
    "cross_domain_handoff": 2.0,
    "analyst_unanswerable": 1.5,
}


class CandidateAnswer(TypedDict):
    answer: str
    decision_effect: str


class ResearchTask(TypedDict, total=False):
    task_id: str
    origin: TaskOrigin
    phase: TaskPhase
    question: str
    decision_relevance: str
    candidate_answers: list[CandidateAnswer]
    evidence_target: str
    source_scope: list[str]
    resolution_test: str
    max_tool_calls: int
    triggered_by: list[str]
    status: TaskStatus
    status_reason: str
    priority: float
    priority_reasons: list[str]
    answer: str
    matched_candidate: int | None
    resolution_fact_ids: list[str]
    rationale: str
    attempts: list[dict[str, Any]]
    tool_calls_used: int
    llm_calls: int


def _text_key(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _candidate_answers(raw: Any) -> list[CandidateAnswer]:
    answers: list[CandidateAnswer] = []
    for item in raw or []:
        if isinstance(item, dict):
            answer = str(item.get("answer") or "").strip()
            effect = str(item.get("decision_effect") or "").strip()
        else:
            answer, effect = str(item or "").strip(), ""
        if answer:
            answers.append({"answer": answer, "decision_effect": effect})
    return answers


def _scope(raw: Any, default: Iterable[str]) -> list[str]:
    scope = [str(item).strip() for item in (raw or []) if str(item).strip() in SOURCE_SCOPES]
    return scope or [s for s in default if s in SOURCE_SCOPES]


def make_task(
    raw: dict[str, Any],
    *,
    task_id: str,
    origin: TaskOrigin,
    phase: TaskPhase,
    default_scope: Iterable[str] = ("sec_filings", "sec_financials", "news"),
    max_tool_calls: int = 3,
) -> ResearchTask | None:
    question = str(raw.get("question") or "").strip()
    if not question:
        return None
    answers = _candidate_answers(raw.get("candidate_answers"))
    task: ResearchTask = {
        "task_id": task_id,
        "origin": origin,
        "phase": phase,
        "question": question,
        "decision_relevance": str(raw.get("decision_relevance") or raw.get("why_needed") or "").strip(),
        "candidate_answers": answers,
        "evidence_target": str(raw.get("evidence_target") or "").strip(),
        "source_scope": _scope(raw.get("source_scope"), default_scope),
        "resolution_test": str(raw.get("resolution_test") or raw.get("stop_condition") or "").strip(),
        "max_tool_calls": int(raw.get("max_tool_calls") or max_tool_calls),
        "triggered_by": [str(item) for item in raw.get("triggered_by") or [] if str(item)],
        "status": "open",
        "status_reason": "",
        "attempts": [],
        "tool_calls_used": 0,
        "llm_calls": 0,
        "resolution_fact_ids": [],
    }
    effects = {_text_key(a["decision_effect"]) for a in answers if a["decision_effect"]}
    if len(answers) >= 2 and len(effects) == 1 and all(a["decision_effect"] for a in answers):
        task["status"] = "not_decision_relevant"
        task["status_reason"] = "every candidate answer implies the same decision effect"
    return task


def tasks_from_manager_requests(
    requests: Iterable[dict[str, Any]],
    *,
    start_index: int,
    max_tool_calls: int,
) -> list[ResearchTask]:
    tasks = []
    for idx, raw in enumerate(requests or [], start_index):
        if not isinstance(raw, dict):
            continue
        task = make_task(
            raw,
            task_id=f"RT-{idx:03d}",
            origin="manager_request",
            phase="post_manager",
            max_tool_calls=max_tool_calls,
        )
        if task is not None:
            tasks.append(task)
    return tasks


def tasks_from_analyst_ledgers(
    state: dict[str, Any],
    *,
    start_index: int,
    max_tool_calls: int,
    min_question_relevance: float = 0.6,
) -> list[ResearchTask]:
    """Pre-debate tasks: cross-domain handoffs and questions an analyst's own tools could not answer."""
    tasks: list[ResearchTask] = []
    idx = start_index
    for domain in ("market", "sentiment", "news", "fundamentals", "catalyst"):
        ledger = state.get(f"{domain}_ledger") or {}
        for handoff in ledger.get("cross_domain_handoffs") or []:
            if not isinstance(handoff, dict):
                continue
            to_domain = str(handoff.get("to_domain") or domain)
            task = make_task(
                {
                    **handoff,
                    "decision_relevance": handoff.get("why_needed") or "",
                    "source_scope": DOMAIN_SCOPES.get(to_domain),
                },
                task_id=f"RT-{idx:03d}",
                origin="cross_domain_handoff",
                phase="pre_debate",
                max_tool_calls=max_tool_calls,
            )
            if task:
                tasks.append(task)
                idx += 1

    research = state.get("analyst_research_state") or {}
    counter_to_domain = {"social": "sentiment"}
    for counter_key, info in research.items():
        domain = counter_to_domain.get(counter_key, counter_key)
        deltas = (info or {}).get("deltas") or []
        if not deltas:
            continue
        unanswerable = set(deltas[-1].get("unanswerable_questions") or [])
        ledger = state.get(f"{domain}_ledger") or {}
        for question in ledger.get("question_backlog") or []:
            if question.get("id") not in unanswerable:
                continue
            if float(question.get("decision_relevance") or 0.0) < min_question_relevance:
                continue
            task = make_task(
                {
                    "question": question.get("question"),
                    "decision_relevance": f"{domain} analyst question (relevance {question.get('decision_relevance')})",
                    "resolution_test": question.get("stop_condition"),
                    "triggered_by": [f"{domain}:{question.get('id')}"],
                    "source_scope": DOMAIN_SCOPES.get(domain),
                },
                task_id=f"RT-{idx:03d}",
                origin="analyst_unanswerable",
                phase="pre_debate",
                max_tool_calls=max_tool_calls,
            )
            if task:
                task["priority_reasons"] = [f"analyst decision_relevance {question.get('decision_relevance')}"]
                tasks.append(task)
                idx += 1
    return tasks


def prioritize(tasks: list[ResearchTask]) -> list[ResearchTask]:
    """Score open tasks with a fixed, documented rule and return them best first."""
    for task in tasks:
        reasons = list(task.get("priority_reasons") or [])
        score = ORIGIN_WEIGHT.get(task.get("origin", ""), 1.0)
        reasons.append(f"origin {task.get('origin')} +{score}")
        effects = {_text_key(a["decision_effect"]) for a in task.get("candidate_answers") or [] if a["decision_effect"]}
        if len(effects) >= 2:
            score += 1.0
            reasons.append("candidate answers imply different decisions +1.0")
        elif not task.get("candidate_answers"):
            score -= 0.5
            reasons.append("no candidate answers stated -0.5")
        if task.get("evidence_target"):
            score += 0.25
            reasons.append("names an evidence target +0.25")
        task["priority"] = round(score, 2)
        task["priority_reasons"] = reasons
    return sorted(tasks, key=lambda t: (-float(t.get("priority", 0)), t["task_id"]))


def dedupe_tasks(tasks: list[ResearchTask], existing: Iterable[ResearchTask] = ()) -> list[ResearchTask]:
    seen = {_text_key(t.get("question")) for t in existing}
    unique = []
    for task in tasks:
        key = _text_key(task.get("question"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(task)
    return unique


def select_tasks(tasks: list[ResearchTask], max_tasks: int) -> tuple[list[ResearchTask], list[ResearchTask]]:
    """Pick up to ``max_tasks`` open tasks by priority; the rest are marked budget-exhausted."""
    ordered = prioritize([t for t in tasks if t.get("status") == "open"])
    selected, deferred = ordered[: max(0, max_tasks)], ordered[max(0, max_tasks):]
    for task in deferred:
        task["status"] = "budget_exhausted"
        task["status_reason"] = f"controller task budget ({max_tasks}) reached"
    return selected, deferred
