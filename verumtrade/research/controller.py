"""Research controller: one lead investigator working bounded research tasks.

For each selected task the investigator gets only the evidence relevant to the
question, the tools its source scope permits, and a tool-call budget. It must end
with a TASK_RESOLUTION_JSON block. A resolution only counts when it cites evidence
acquired while working *this* task; restating an assumption, or citing only
model-written summaries, is recorded as a rejected resolution. Unanswerable is a
legitimate outcome that narrows the conclusion instead of prolonging the search.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from verumtrade.agents.utils.agent_runtime.evidence_graph import (
    extract_evidence_facts_from_messages,
    merge_evidence_facts,
)
from verumtrade.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from verumtrade.dataflows.config import get_config
from verumtrade.graph.debate_schema import frame_contested_issues
from verumtrade.graph.evidence_ledger_schema import (
    build_evidence_ledger,
    rank_critical_evidence,
    validate_admissible_evidence,
)
from verumtrade.research.document_reader import _terms
from verumtrade.research.envelope import is_model_synthesis
from verumtrade.research.tasks import (
    ResearchTask,
    dedupe_tasks,
    select_tasks,
    tasks_from_analyst_ledgers,
)

TOOL_OUTPUT_CHARS = 6000
RESOLUTION_MARKER = "TASK_RESOLUTION_JSON"

INVESTIGATOR_PROMPT = """You are the lead research investigator for a swing-trade thesis on {ticker}, as of {as_of}.
Work exactly one research task. Use the permitted tools to find the evidence that answers it, then stop.

Rules:
- Evidence must be available on or before {as_of}; tools are pinned to that date.
- Read sources, not summaries, when a filing or dataset can answer the question.
- A question is resolved only when evidence you obtained in this task answers it. Cite those evidence fact IDs (shown as "EVIDENCE FACT IDS" after each tool result).
- If the permitted sources cannot answer it, say so: "unanswerable" is a valid, useful outcome.
- Do not repeat the same lookup. Stop as soon as the resolution test is met.

When done (or when tools are no longer offered), reply with no tool calls and end with:
{marker}
{{"status": "resolved" | "unanswerable", "answer": "one sentence", "matched_candidate": <index of the candidate answer or null>, "evidence_fact_ids": ["fact_..."], "rationale": "why the cited evidence settles it, or why it cannot be settled"}}"""


def default_tools_by_scope() -> dict[str, list[Any]]:
    from verumtrade.agents.utils.agent_runtime.agent_utils import (
        get_company_news_window,
        get_fundamentals,
        get_global_news,
    )
    from verumtrade.agents.utils.market_data.price_action_tools import get_price_action_summary
    from verumtrade.agents.utils.market_data.sec_research_tools import (
        get_sec_financial_facts,
        list_sec_filings,
        read_sec_filing,
    )

    return {
        "sec_filings": [list_sec_filings, read_sec_filing],
        "sec_financials": [get_sec_financial_facts],
        "news": [get_company_news_window, get_global_news],
        "market": [get_price_action_summary],
        "fundamentals": [get_fundamentals],
    }


def relevant_facts(facts: list[dict[str, Any]], question: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Keep the context small: the existing facts that share the most terms with the question."""
    terms = set(_terms(question))
    scored = []
    for idx, fact in enumerate(facts or []):
        overlap = len(terms & set(_terms(f"{fact.get('claim', '')} {fact.get('section', '')}")))
        if overlap:
            scored.append((overlap, -idx, fact))
    scored.sort(reverse=True)
    return [fact for _, _, fact in scored[:top_k]]


def parse_task_resolution(text: Any) -> dict[str, Any] | None:
    content = str(text or "")
    idx = content.upper().rfind(RESOLUTION_MARKER)
    if idx < 0:
        return None
    tail = content[idx + len(RESOLUTION_MARKER):]
    match = re.search(r"\{.*\}", tail, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _call_llm(runnable: Any, messages: list[Any]):
    if hasattr(runnable, "ainvoke"):
        return await runnable.ainvoke(messages)
    return await asyncio.to_thread(runnable.invoke if hasattr(runnable, "invoke") else runnable, messages)


def _facts_from_tool_output(task_id: str, step: int, tool_name: str, args: dict, output: str) -> list[dict[str, Any]]:
    facts = extract_evidence_facts_from_messages([output])
    if facts:
        return facts
    # Plain-text tool output still becomes one citable, hash-identified fact.
    text = str(output or "").strip()
    if not text:
        return []
    synthesis = is_model_synthesis(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return [
        {
            "id": f"fact_research_{task_id.lower().replace('-', '_')}_{step:02d}",
            "domain": "news" if "news" in tool_name else "fundamentals",
            "claim": " ".join(text.split())[:320],
            "text": text[:1500],
            "source": tool_name,
            "section": "research_controller",
            "confidence": 0.4 if synthesis else 0.7,
            "quality": "low_quality" if synthesis else "normal",
            "source_type": "model_synthesis" if synthesis else "vendor",
            "provenance": {"kind": "tool_output", "tool": tool_name, "args": args, "content_hash": digest},
        }
    ]


def _stamp_observed_at(facts: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    """Tools are pinned to ``as_of``, so a fact without its own date was observed as of then.
    Undated facts fail evidence admissibility and would be offered to the manager uncitable."""
    for fact in facts:
        if as_of and not str(fact.get("as_of") or fact.get("observed_at") or "").strip():
            fact["as_of"] = as_of
            fact.setdefault("provenance", {})
            if isinstance(fact["provenance"], dict):
                fact["provenance"].setdefault("observed_at_basis", "tool pinned to run as_of")
    return facts


def _pin_args(tool: Any, args: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Force the as-of date on date-aware tools so research cannot look past it."""
    pinned = dict(args or {})
    accepted = set(getattr(tool, "args", {}) or {})
    if "curr_date" in accepted or "curr_date" in pinned:
        pinned["curr_date"] = as_of
    return pinned


def _usable_for_resolution(fact: dict[str, Any]) -> bool:
    verification = (fact.get("provenance") or {}).get("verification") or {}
    return verification.get("status") != "failed" and fact.get("source_type") != "model_synthesis"


async def investigate_task(
    task: ResearchTask,
    *,
    llm: Any,
    tools_by_scope: dict[str, list[Any]],
    ticker: str,
    as_of: str,
    context_facts: list[dict[str, Any]],
    tool_budget: int,
) -> tuple[ResearchTask, list[dict[str, Any]]]:
    tools = []
    for scope in task.get("source_scope") or []:
        for tool in tools_by_scope.get(scope, []):
            if tool not in tools:
                tools.append(tool)
    tools_by_name = {getattr(tool, "name", ""): tool for tool in tools}
    max_calls = max(0, min(int(task.get("max_tool_calls") or 3), tool_budget))

    brief = {
        "task_id": task["task_id"],
        "question": task["question"],
        "decision_relevance": task.get("decision_relevance"),
        "candidate_answers": task.get("candidate_answers"),
        "evidence_target": task.get("evidence_target"),
        "resolution_test": task.get("resolution_test"),
        "permitted_tools": list(tools_by_name),
        "tool_call_budget": max_calls,
    }
    known = [{"id": f["id"], "claim": f.get("claim"), "source": f.get("source")} for f in context_facts]
    messages: list[Any] = [
        SystemMessage(INVESTIGATOR_PROMPT.format(ticker=ticker, as_of=as_of, marker=RESOLUTION_MARKER)),
        HumanMessage(
            "RESEARCH TASK:\n" + json.dumps(brief, ensure_ascii=False, indent=2)
            + "\n\nEXISTING EVIDENCE RELEVANT TO THIS QUESTION (already known; citing it alone does not resolve the task):\n"
            + json.dumps(known, ensure_ascii=False, indent=2)
        ),
    ]

    acquired: dict[str, dict[str, Any]] = {}
    used = 0
    step = 0
    last = None
    while True:
        can_call = used < max_calls and bool(tools)
        runnable = bind_tools_parallel_safe(llm, tools) if can_call else llm
        last = await _call_llm(runnable, messages)
        task["llm_calls"] = int(task.get("llm_calls") or 0) + 1
        messages.append(last)
        calls = list(getattr(last, "tool_calls", None) or [])
        if not calls or not can_call:
            break
        for call in calls:
            step += 1
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
            tool = tools_by_name.get(name)
            args = _pin_args(tool, call.get("args") if isinstance(call, dict) else getattr(call, "args", {}), as_of)
            if tool is None or used >= max_calls:
                reason = "not permitted for this task" if tool is None else "tool budget exhausted"
                messages.append(ToolMessage(content=f"Tool {name} {reason}.", tool_call_id=call_id))
                continue
            used += 1
            try:
                output = str(await tool.ainvoke(args))
            except Exception as exc:  # a failed lookup is information, not a crash
                output = f"Tool error: {type(exc).__name__}: {exc}"
            new_facts = _stamp_observed_at(_facts_from_tool_output(task["task_id"], step, name, args, output), as_of)
            for fact in new_facts:
                acquired[fact["id"]] = fact
            task["attempts"].append({"tool": name, "args": args, "fact_ids": [f["id"] for f in new_facts]})
            ids = ", ".join(f["id"] for f in new_facts) or "(none)"
            messages.append(
                ToolMessage(
                    content=f"EVIDENCE FACT IDS: {ids}\n{output[:TOOL_OUTPUT_CHARS]}",
                    tool_call_id=call_id,
                )
            )
    task["tool_calls_used"] = used
    _apply_resolution(task, parse_task_resolution(getattr(last, "content", "")), acquired, max_calls)
    return task, list(acquired.values())


def _apply_resolution(
    task: ResearchTask,
    resolution: dict[str, Any] | None,
    acquired: dict[str, dict[str, Any]],
    max_calls: int,
) -> None:
    if resolution is None:
        exhausted = task.get("tool_calls_used", 0) >= max_calls and max_calls > 0
        task["status"] = "budget_exhausted" if exhausted else "unanswerable"
        task["status_reason"] = "investigator emitted no TASK_RESOLUTION_JSON"
        return
    task["answer"] = str(resolution.get("answer") or "").strip()
    task["rationale"] = str(resolution.get("rationale") or "").strip()
    matched = resolution.get("matched_candidate")
    task["matched_candidate"] = matched if isinstance(matched, int) else None
    status = str(resolution.get("status") or "").strip().lower()
    if status != "resolved":
        task["status"] = "unanswerable"
        task["status_reason"] = task["rationale"] or "investigator judged the question unanswerable with permitted sources"
        return
    cited = [str(item) for item in resolution.get("evidence_fact_ids") or []]
    new_evidence = [fid for fid in cited if fid in acquired]
    usable = [fid for fid in new_evidence if _usable_for_resolution(acquired[fid])]
    if not usable:
        task["status"] = "rejected_resolution"
        task["status_reason"] = (
            "resolution cites no evidence acquired in this task"
            if not new_evidence
            else "cited evidence is model synthesis or failed verification"
        )
        task["resolution_fact_ids"] = new_evidence
        return
    task["status"] = "resolved"
    task["status_reason"] = f"resolved with {len(usable)} newly acquired fact(s)"
    task["resolution_fact_ids"] = usable


def _append_facts_to_evidence(state: dict[str, Any], new_facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Add facts after the manager has cited E-* IDs, without renumbering existing evidence."""
    graph = dict(state.get("evidence_graph") or {})
    existing_ids = {f.get("id") for f in graph.get("facts") or []}
    appended = [f for f in merge_evidence_facts([], new_facts) if f["id"] not in existing_ids]
    graph["facts"] = list(graph.get("facts") or []) + appended
    ledger = build_evidence_ledger({**state, "evidence_graph": graph})
    admissibility = validate_admissible_evidence(
        ledger,
        time_horizon=str(state.get("time_horizon") or ""),
        as_of_date=str(state.get("trade_date") or "") or None,
    )
    return {
        "evidence_graph": graph,
        "evidence_ledger": ledger,
        "admissibility_report": admissibility,
        "critical_evidence_ids": [item["evidence_id"] for item in rank_critical_evidence(ledger)],
        "contested_issues": state.get("contested_issues") or frame_contested_issues(ledger, admissibility),
    }


def research_controller_enabled(config: dict[str, Any] | None = None) -> bool:
    settings = (config or get_config()).get("research_controller") or {}
    return bool(settings.get("enabled", False))


def controller_settings() -> dict[str, int]:
    config = get_config().get("research_controller") or {}
    return {
        "max_tasks_pre_debate": int(config.get("max_tasks_pre_debate", 2)),
        "max_tasks_post_manager": int(config.get("max_tasks_post_manager", 2)),
        "max_tool_calls_per_task": int(config.get("max_tool_calls_per_task", 3)),
        "max_tool_calls_per_phase": int(config.get("max_tool_calls_per_phase", 6)),
        "max_manager_reopens": int(config.get("max_manager_reopens", 1)),
    }


def create_research_controller(llm: Any, tools_by_scope: dict[str, list[Any]] | Callable[[], dict] | None = None):
    async def research_controller_node(state: dict[str, Any]) -> dict[str, Any]:
        settings = controller_settings()
        scopes = tools_by_scope() if callable(tools_by_scope) else (tools_by_scope or default_tools_by_scope())
        phase = "post_manager" if state.get("research_phase") == "post_manager" else "pre_debate"
        tasks: list[ResearchTask] = [dict(t) for t in state.get("research_tasks") or []]  # type: ignore[misc]

        if phase == "pre_debate":
            fresh = tasks_from_analyst_ledgers(
                state,
                start_index=len(tasks) + 1,
                max_tool_calls=settings["max_tool_calls_per_task"],
            )
            tasks.extend(dedupe_tasks(fresh, tasks))
            max_tasks = settings["max_tasks_pre_debate"]
        else:
            max_tasks = settings["max_tasks_post_manager"]

        pending = [t for t in tasks if t.get("phase") == phase and t.get("status") == "open"]
        selected, _deferred = select_tasks(pending, max_tasks)

        known_facts = list(state.get("evidence_source_facts") or [])
        if phase == "post_manager":
            known_facts = list((state.get("evidence_graph") or {}).get("facts") or known_facts)
        acquired_all: list[dict[str, Any]] = []
        budget = settings["max_tool_calls_per_phase"]
        for task in selected:
            if budget <= 0:
                task["status"] = "budget_exhausted"
                task["status_reason"] = "controller tool budget for this phase is spent"
                continue
            _, facts = await investigate_task(
                task,
                llm=llm,
                tools_by_scope=scopes,
                ticker=str(state.get("company_of_interest") or ""),
                as_of=str(state.get("trade_date") or ""),
                context_facts=relevant_facts(known_facts + acquired_all, task["question"]),
                tool_budget=budget,
            )
            budget -= int(task.get("tool_calls_used") or 0)
            acquired_all.extend(facts)

        update: dict[str, Any] = {
            "research_tasks": tasks,
            "evidence_source_facts": merge_evidence_facts(state.get("evidence_source_facts") or [], acquired_all),
            "research_phase": "debate" if phase == "pre_debate" else "manager_revision",
            "research_controller_log": list(state.get("research_controller_log") or [])
            + [
                {
                    "phase": phase,
                    "selected": [t["task_id"] for t in selected],
                    "statuses": {t["task_id"]: t["status"] for t in tasks if t.get("phase") == phase},
                    "tool_calls": sum(int(t.get("tool_calls_used") or 0) for t in selected),
                    "llm_calls": sum(int(t.get("llm_calls") or 0) for t in selected),
                    "facts_acquired": len(acquired_all),
                }
            ],
        }
        if phase == "post_manager":
            update.update(_append_facts_to_evidence(state, acquired_all))
        return update

    return research_controller_node


def research_metrics(state: dict[str, Any]) -> dict[str, Any]:
    tasks = state.get("research_tasks") or []
    by_status: dict[str, int] = {}
    for task in tasks:
        by_status[task.get("status", "open")] = by_status.get(task.get("status", "open"), 0) + 1
    log = state.get("research_controller_log") or []
    return {
        "research_tasks_total": len(tasks),
        "research_tasks_by_status": by_status,
        "research_tool_calls": sum(int(entry.get("tool_calls") or 0) for entry in log),
        "research_llm_calls": sum(int(entry.get("llm_calls") or 0) for entry in log),
        "research_manager_reopens": int(state.get("research_manager_reopens") or 0),
    }
