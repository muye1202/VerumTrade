"""Assess-then-act research loop shared by the tool-using analysts.

After every tool result the analyst first produces an assessment (its full
workbench ledger, with tools unbound). That ledger is persisted before the next
action is chosen, so follow-up research is authorized by questions the analyst
actually wrote down rather than by whether the previous message happened to
contain a tool call.

Research status per analyst:
- ``continue``: an open question names an available tool and budget remains.
- ``ready``: no open question would change the conclusion.
- ``unanswerable_with_available_sources``: open questions remain, but none maps
  to an available tool that has not already been tried for it.
- ``budget_exhausted``: an answerable question remains but the tool budget is spent.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Literal, TypedDict

from verumtrade.agents.analysts.discovery_lane import (
    merge_workbench_metrics,
    record_tool_call_links,
    select_allowed_question,
)
from verumtrade.agents.analysts.tooling import build_tooling_state_update
from verumtrade.agents.analysts.workbench import (
    AnalystLedger,
    AnalystQuestion,
    build_ledger_evidence_summary,
    build_no_tools_available_prompt_block,
    build_workbench_prompt_block,
    finalize_analyst_workbench_output,
    normalize_ledger,
)
from verumtrade.agents.utils.agent_runtime.context_budget import build_report_evidence_summary
from verumtrade.agents.utils.llm.tool_binding import bind_tools_parallel_safe
from verumtrade.dataflows.config import get_config


ResearchStatus = Literal[
    "continue",
    "ready",
    "unanswerable_with_available_sources",
    "budget_exhausted",
]
TERMINAL_RESEARCH_STATUSES = {
    "ready",
    "unanswerable_with_available_sources",
    "budget_exhausted",
}


# Native tool-call syntax some providers (e.g. DeepSeek's DSML) emit as plain text when the
# model wants a tool but none is bound. Parsed as prose it becomes bogus "observations".
_LEAKED_TOOL_MARKUP = re.compile(
    r"<[|｜]{1,2}\s*(?:DSML|tool[▁_ ]calls?[▁_ ]begin|tool[▁_ ]call)"
    r"|<tool_call>|<function_calls>|<invoke\s+name=",
    re.IGNORECASE,
)
NO_TOOL_RETRY_BLOCK = (
    "\n\nTOOLS ARE NOT AVAILABLE IN THIS TURN. Do not emit any tool or function call syntax. "
    "Write the required ledger and report from the evidence already gathered; record any further "
    "lookup you want as an open question with its cheapest tool instead of calling it."
)


def has_leaked_tool_markup(content: Any) -> bool:
    return isinstance(content, str) and bool(_LEAKED_TOOL_MARKUP.search(content))


def strip_leaked_tool_markup(content: str) -> str:
    """Drop everything from the first leaked tool-call marker onward."""
    match = _LEAKED_TOOL_MARKUP.search(content or "")
    return content[: match.start()].rstrip() if match else content


class ResearchAttempt(TypedDict, total=False):
    round: int
    question_id: str
    question: str
    tool: str


class AssessmentDelta(TypedDict, total=False):
    round: int
    new_observations: List[str]
    new_hypotheses: List[str]
    withdrawn_hypotheses: List[str]
    discarded_hypotheses: List[str]
    questions_opened: List[str]
    questions_resolved: List[str]
    resolved_without_investigation: List[str]
    unanswerable_questions: List[str]
    status: ResearchStatus
    next_action: Dict[str, str] | None


def _text_key(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _question_texts_by_id(ledger: AnalystLedger) -> dict[str, str]:
    return {
        str(q.get("id") or ""): str(q.get("question") or "")
        for q in ledger.get("question_backlog", [])
        if q.get("id")
    }


def open_question_ids(ledger: AnalystLedger) -> list[str]:
    normalized = normalize_ledger(str(ledger.get("analyst_domain") or "market"), ledger)
    resolved = set(normalized["resolved_questions"])
    return [qid for qid in normalized["open_questions"] if qid not in resolved]


def decide_research_status(
    ledger: AnalystLedger,
    question: AnalystQuestion | None,
    *,
    budget_open: bool,
) -> ResearchStatus:
    if question is not None:
        return "continue" if budget_open else "budget_exhausted"
    if open_question_ids(ledger):
        return "unanswerable_with_available_sources"
    return "ready"


def build_assessment_delta(
    prior: AnalystLedger | None,
    current: AnalystLedger,
    *,
    round_index: int,
    status: ResearchStatus,
    attempts: list[ResearchAttempt],
    next_question: AnalystQuestion | None,
) -> AssessmentDelta:
    domain = str(current.get("analyst_domain") or "market")
    before = normalize_ledger(domain, prior or {})
    after = normalize_ledger(domain, current)

    prior_obs = {_text_key(o.get("claim")) for o in before["observations"]}
    prior_hyp = {_text_key(h.get("claim")) for h in before["active_hypotheses"]}
    current_hyp = {_text_key(h.get("claim")) for h in after["active_hypotheses"]}
    prior_questions = {_text_key(q.get("question")) for q in before["question_backlog"]}
    prior_resolved_texts = {
        _text_key(_question_texts_by_id(before).get(qid, qid)) for qid in before["resolved_questions"]
    }
    attempted_ids = {str(a.get("question_id") or "") for a in attempts}
    attempted_texts = {_text_key(a.get("question")) for a in attempts}
    after_texts = _question_texts_by_id(after)

    resolved_now: list[str] = []
    resolved_without_investigation: list[str] = []
    for qid in after["resolved_questions"]:
        text = after_texts.get(qid, qid)
        if _text_key(text) in prior_resolved_texts:
            continue
        resolved_now.append(qid)
        if qid not in attempted_ids and _text_key(text) not in attempted_texts:
            resolved_without_investigation.append(qid)

    open_ids = open_question_ids(after)
    unanswerable = [] if status == "continue" else [
        qid for qid in open_ids if not next_question or qid != next_question.get("id")
    ]

    return {
        "round": int(round_index),
        "new_observations": [
            str(o.get("claim") or "")
            for o in after["observations"]
            if _text_key(o.get("claim")) not in prior_obs
        ],
        "new_hypotheses": [
            str(h.get("claim") or "")
            for h in after["active_hypotheses"]
            if _text_key(h.get("claim")) not in prior_hyp
        ],
        "withdrawn_hypotheses": [
            str(h.get("claim") or "")
            for h in before["active_hypotheses"]
            if _text_key(h.get("claim")) not in current_hyp
        ],
        "discarded_hypotheses": [str(h.get("claim") or "") for h in after["discarded_hypotheses"]],
        "questions_opened": [
            str(q.get("id") or "")
            for q in after["question_backlog"]
            if _text_key(q.get("question")) not in prior_questions and q.get("id") in open_ids
        ],
        "questions_resolved": resolved_now,
        "resolved_without_investigation": resolved_without_investigation,
        "unanswerable_questions": unanswerable,
        "status": status,
        "next_action": (
            {
                "question_id": str(next_question.get("id") or ""),
                "tool": str(next_question.get("cheapest_tool") or ""),
            }
            if next_question and status == "continue"
            else None
        ),
    }


def build_assessment_prompt_block(
    follow_up_tools: list[Any],
    *,
    prior_ledger: AnalystLedger | None,
    attempts: list[ResearchAttempt],
    budget_open: bool,
) -> str:
    tool_names = [str(getattr(tool, "name", "") or "") for tool in follow_up_tools]
    tool_names = [name for name in tool_names if name]
    lines = [
        "",
        "",
        "RESEARCH ASSESSMENT TURN (tools are not callable in this turn):",
        "Review the tool results above and emit the complete BEGIN_ANALYST_LEDGER_JSON / "
        "END_ANALYST_LEDGER_JSON ledger plus memo as your current assessment.",
        "- List a question id in resolved_questions only when tool output already in this "
        "conversation answers it, and cite the answering fact IDs in the related observations.",
        "- For each remaining open question that a follow-up lookup could answer, set "
        "cheapest_tool to exactly one of: "
        + (", ".join(tool_names) if tool_names else "(none available)")
        + ". Use null when no listed tool can answer it; that question will be reported as "
        "unanswerable with available sources.",
        "- Keep in open_questions only questions whose answer could change your conclusion.",
    ]
    if attempts:
        lines.append("- Already investigated in this run (do not ask them again):")
        for attempt in attempts[-6:]:
            lines.append(
                f"  - [{attempt.get('question_id')}] via {attempt.get('tool')}: {attempt.get('question')}"
            )
    if prior_ledger:
        summary = build_ledger_evidence_summary(
            str(prior_ledger.get("analyst_domain") or "market"), prior_ledger, max_chars=1500
        )
        open_ids = set(open_question_ids(prior_ledger))
        open_questions = [
            {"id": q.get("id"), "question": q.get("question")}
            for q in prior_ledger.get("question_backlog", [])
            if q.get("id") in open_ids
        ]
        lines.append("- Your previous assessment (update it rather than starting over):")
        lines.append(summary)
        if open_questions:
            lines.append("  Previously open questions: " + json.dumps(open_questions, ensure_ascii=False))
    if budget_open:
        lines.append(
            "- If an open question names an available tool, the workbench will run that one lookup "
            "next; otherwise this assessment becomes your final report."
        )
    else:
        lines.append("- The research budget is exhausted: this assessment is your final report.")
    return "\n".join(lines)


def research_budget_open(state: Dict[str, Any], counter_key: str) -> tuple[int, bool]:
    config = get_config()
    tool_round_cap = int(config.get("analyst_tool_round_cap", 4) or 0)
    global_cap = int(config.get("max_tool_calls_total", 50) or 50)
    rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
    rounds_used = int(rounds.get(counter_key, 0) or 0)
    total_used = int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0)
    budget_open = not (
        state.get("force_no_tools_for") == counter_key
        or (tool_round_cap > 0 and rounds_used >= tool_round_cap)
        or (global_cap > 0 and total_used >= global_cap)
    )
    return rounds_used, budget_open


def _tool_call_names(result: Any) -> list[str]:
    names = []
    for tool_call in getattr(result, "tool_calls", None) or []:
        name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
        names.append(str(name or ""))
    return names


def run_analyst_research_turn(
    state: Dict[str, Any],
    llm: Any,
    *,
    counter_key: str,
    domain: str,
    compose_prompt: Callable[[str, list[Any]], Any],
    first_round_tools: list[Any],
    first_round_question: AnalystQuestion | None,
    follow_up_tools: list[Any],
) -> dict[str, Any]:
    """Run one analyst node visit and return its LangGraph state update.

    ``compose_prompt(workbench_block, tools)`` must return a prompt runnable whose
    system message embeds ``workbench_block`` and lists ``tools`` by name.
    """
    rounds_used, budget_open = research_budget_open(state, counter_key)
    research_by_analyst = dict(state.get("analyst_research_state") or {})
    research = dict(research_by_analyst.get(counter_key) or {})
    attempts: list[ResearchAttempt] = list(research.get("attempts") or [])
    deltas: list[AssessmentDelta] = list(research.get("deltas") or [])

    def invoke(workbench_block: str, tools: list[Any]):
        prompt = compose_prompt(workbench_block, tools)
        if tools:
            return (prompt | bind_tools_parallel_safe(llm, tools)).invoke(state["messages"])
        result = (prompt | llm).invoke(state["messages"])
        if has_leaked_tool_markup(getattr(result, "content", None)):
            # Retry once with an explicit no-tools instruction, then strip whatever still leaks.
            retry_prompt = compose_prompt(workbench_block + NO_TOOL_RETRY_BLOCK, tools)
            result = (retry_prompt | llm).invoke(state["messages"])
            if has_leaked_tool_markup(getattr(result, "content", None)):
                result.content = strip_leaked_tool_markup(result.content)
        return result

    def research_update(status: str, question: AnalystQuestion | None = None, tool: str = "") -> dict:
        updated_attempts = list(attempts)
        if question is not None and tool:
            updated_attempts.append(
                {
                    "round": rounds_used,
                    "question_id": str(question.get("id") or ""),
                    "question": str(question.get("question") or ""),
                    "tool": tool,
                }
            )
        research_by_analyst[counter_key] = {
            "status": status,
            "attempts": updated_attempts,
            "deltas": deltas,
        }
        return {"analyst_research_state": research_by_analyst}

    def tool_call_update(result: Any, question: AnalystQuestion | None) -> dict:
        tool_names = _tool_call_names(result)
        link_state = dict(state)
        for name in tool_names:
            link_state.update(
                record_tool_call_links(link_state, domain, name, question, tool_calls_count=1)
            )
        return {
            "messages": [result],
            f"{domain}_report": "",
            f"{domain}_evidence": "",
            "force_no_tools_for": "",
            **build_tooling_state_update(state, counter_key, len(tool_names)),
            "analyst_tool_call_links": link_state.get(
                "analyst_tool_call_links", state.get("analyst_tool_call_links", {})
            ),
            **research_update("continue", question, ",".join(tool_names)),
        }

    def final_update(message: Any, finalized: dict, status: str) -> dict:
        report = finalized["report"]
        return {
            "messages": [message],
            f"{domain}_report": report,
            f"{domain}_evidence": finalized["evidence"]
            or (build_report_evidence_summary(domain, report) if report else ""),
            f"{domain}_ledger": finalized["ledger"],
            "force_no_tools_for": "",
            **build_tooling_state_update(state, counter_key, 0),
            **merge_workbench_metrics(state, domain, finalized["metrics"]),
            **research_update(status),
        }

    if rounds_used <= 0:
        tools = list(first_round_tools) if budget_open else []
        block = build_workbench_prompt_block(domain, first_round_question)
        if not tools:
            block += build_no_tools_available_prompt_block()
        result = invoke(block, tools)
        if getattr(result, "tool_calls", None):
            return tool_call_update(result, first_round_question)
        finalized = finalize_analyst_workbench_output(domain, result.content)
        return final_update(result, finalized, "ready")

    # Assess before acting: persist the ledger implied by the evidence so far.
    prior_ledger = state.get(f"{domain}_ledger") or None
    assessment_block = build_workbench_prompt_block(domain) + build_assessment_prompt_block(
        follow_up_tools,
        prior_ledger=prior_ledger if prior_ledger and prior_ledger.get("question_backlog") else None,
        attempts=attempts,
        budget_open=budget_open,
    )
    assessment = invoke(assessment_block, [])
    finalized = finalize_analyst_workbench_output(domain, assessment.content)
    ledger = finalized["ledger"]

    question = select_allowed_question(
        ledger,
        [getattr(tool, "name", "") for tool in follow_up_tools],
        exclude_question_ids=[a.get("question_id", "") for a in attempts],
        exclude_question_texts=[a.get("question", "") for a in attempts],
    )
    if question is not None and not question.get("cheapest_tool"):
        question = None
    status = decide_research_status(ledger, question, budget_open=budget_open)
    delta = build_assessment_delta(
        prior_ledger,
        ledger,
        round_index=rounds_used,
        status=status,
        attempts=attempts,
        next_question=question,
    )
    deltas.append(delta)
    if delta["resolved_without_investigation"]:
        ledger["critic_flags"] = list(
            dict.fromkeys(
                [
                    *ledger.get("critic_flags", []),
                    *(f"{qid}:resolved_without_investigation" for qid in delta["resolved_without_investigation"]),
                ]
            )
        )

    if status == "continue" and question is not None:
        tool_name = str(question.get("cheapest_tool") or "")
        tool = [t for t in follow_up_tools if getattr(t, "name", "") == tool_name]
        result = invoke(build_workbench_prompt_block(domain, question), tool)
        if getattr(result, "tool_calls", None):
            update = tool_call_update(result, question)
            update[f"{domain}_ledger"] = ledger
            return update
        # The model declined the lookup it proposed; keep the assessment as final.
        status = "ready"
        delta["status"] = status
        delta["next_action"] = None
        ledger["critic_flags"] = list(
            dict.fromkeys([*ledger.get("critic_flags", []), f"{question.get('id')}:follow_up_declined"])
        )

    return final_update(assessment, finalized, status)
