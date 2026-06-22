from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypedDict

from pydantic import ValidationError

from opentrace.graph.structured_schemas import ResearchDebateTurn, ThesisLedger, TraderPlan


logger = logging.getLogger(__name__)


ALLOWED_DECISION_FIELDS = {
    "action",
    "execution_mode",
    "order_type",
    "entry_price",
    "entry_condition",
    "stop_loss",
    "take_profit",
    "position_size_pct",
    "max_loss_pct",
    "trigger_condition",
    "time_horizon",
    "invalidation_condition",
}

EXECUTABLE_TRADER_FIELDS = {
    "action",
    "execution_mode",
    "order_type",
    "position_size_pct",
    "entry_condition",
    "stop_loss",
    "take_profit",
}


class DebateWorkflowHardFault(RuntimeError):
    def __init__(self, stage: str, reason: str, *, details: Any | None = None) -> None:
        self.stage = stage
        self.reason = reason
        self.details = details
        detail_text = f" Details: {details}" if details else ""
        super().__init__(f"{stage} debate workflow hard fault: {reason}.{detail_text}")


def intermediate_gates_hard(config: Any) -> bool:
    """Whether INTERMEDIATE debate gates should abort (hard) vs. degrade-and-continue (soft).

    Driven by ``debate_soft_intermediate_gates`` (default soft). The final canonical
    decision/order gate ignores this and is always hard.
    """
    try:
        return not bool((config or {}).get("debate_soft_intermediate_gates", True))
    except Exception:  # pragma: no cover - defensive
        return False


def format_contract_violation(violation: Optional[tuple[str, Any]]) -> Optional[str]:
    """Render a ``(reason, detail)`` violation as a single human-readable string."""
    if not violation:
        return None
    reason, detail = violation
    return f"{reason}. Details: {detail}" if detail else reason


def degrade_or_raise(
    stage: str,
    reason: str,
    detail: Any,
    *,
    hard: bool,
) -> dict[str, Any]:
    """Resolve a contract violation per gate policy.

    When ``hard`` is True, raise ``DebateWorkflowHardFault`` (legacy behavior). When
    False, log the degradation and return a telemetry record so the run can continue
    with best-effort artifacts. Returns ``{}`` when there is no violation.
    """
    if not reason:
        return {}
    if hard:
        raise DebateWorkflowHardFault(stage, reason, details=detail)
    logger.warning(
        "%s: debate contract not satisfied; soft gate enabled, degrading and continuing. "
        "Reason: %s. Detail: %s",
        stage,
        reason,
        detail,
    )
    return {"degraded": True, "stage": stage, "reason": reason, "detail": detail}


def _build_contract_repair_prompt(
    original_prompt: str,
    prior_response: str,
    error: Any,
    *,
    max_prior_chars: int = 6000,
) -> str:
    """Append a focused repair appendix to the original prompt.

    The original prompt already carries all the data/context the node needs, so we
    re-send it and add (a) the exact validation error and (b) the rejected response,
    asking the model to re-emit a corrected version.
    """
    prior = str(prior_response or "")
    if len(prior) > max_prior_chars:
        prior = prior[-max_prior_chars:]
    return (
        f"{original_prompt}\n\n"
        "=== CONTRACT REPAIR REQUEST ===\n"
        "Your previous response was REJECTED by the structured-output validator and "
        "cannot be used. Fix ONLY what is needed to satisfy the contract.\n"
        f"VALIDATION ERROR(S):\n{error}\n\n"
        "Re-emit your COMPLETE response now:\n"
        "- Keep your narrative, but make the required structured block (JSON / contract "
        "marker) pass validation.\n"
        "- Emit valid JSON only: no markdown code fences, no comments, no trailing commas, "
        "and never use 'N/A'/'NA'/'-'/'NONE' placeholders (use null instead).\n"
        "- Include EVERY required field with a concrete value. If a list field is required, "
        "it must be non-empty.\n"
        "- Do not describe or apologize for the error; just produce the corrected response.\n\n"
        "Your previous (rejected) response, for reference:\n"
        f"{prior}\n"
        "=== END CONTRACT REPAIR REQUEST ==="
    )


def invoke_with_contract_repair(
    prompt: str,
    *,
    stage: str,
    invoke: Callable[[str], Any],
    check: Callable[[str], Any],
    max_repair_attempts: int = 2,
) -> Any:
    """Invoke an LLM with automatic re-prompting when the debate contract fails.

    ``invoke`` runs the LLM on a prompt and returns a response object exposing
    ``.content``. ``check`` inspects ``response.content`` and returns a truthy error
    detail when the contract is violated, or a falsy value when it passes.

    On violation, the helper re-prompts with the exact validation error (up to
    ``max_repair_attempts`` extra calls), then returns the best response obtained.
    The caller's hard gate runs afterward and still raises if even the repaired
    output is invalid, so execution safety is unchanged — this only removes spurious
    aborts caused by recoverable formatting slips.
    """
    response = invoke(prompt)
    error = _safe_check(check, response)
    attempt = 0
    while error and attempt < max_repair_attempts:
        attempt += 1
        logger.warning(
            "%s: debate contract violation on attempt %d/%d; re-prompting for repair. Detail: %s",
            stage,
            attempt,
            max_repair_attempts + 1,
            error,
        )
        repair_prompt = _build_contract_repair_prompt(prompt, getattr(response, "content", ""), error)
        response = invoke(repair_prompt)
        error = _safe_check(check, response)
    if error:
        logger.error(
            "%s: debate contract still violated after %d repair attempt(s); "
            "deferring to hard gate. Detail: %s",
            stage,
            max_repair_attempts,
            error,
        )
    return response


def _safe_check(check: Callable[[str], Any], response: Any) -> Any:
    """Run a contract check without letting checker errors mask the repair loop."""
    try:
        return check(getattr(response, "content", ""))
    except Exception as exc:  # pragma: no cover - defensive
        return str(exc)


class ResearchDebateValidation(TypedDict):
    accepted_turns: list[dict[str, Any]]
    rejected_turns: list[dict[str, Any]]


class TraderPlanValidation(TypedDict):
    valid: bool
    violations: list[str]


class ThesisLedgerValidation(TypedDict):
    valid: bool
    violations: list[str]


def frame_contested_issues(
    evidence_ledger: list[dict[str, Any]] | None,
    admissibility_report: dict[str, Any] | None = None,
    *,
    max_issues: int = 3,
) -> list[dict[str, Any]]:
    accepted = set((admissibility_report or {}).get("accepted_evidence_ids") or [])
    candidates = [
        item
        for item in evidence_ledger or []
        if isinstance(item, dict)
        and (not accepted or item.get("evidence_id") in accepted)
        and (item.get("supports") or item.get("contradicts"))
    ]
    candidates = sorted(
        candidates,
        key=lambda item: float(item.get("criticality", 0.0) or 0.0),
        reverse=True,
    )
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        key = _issue_bucket(item)
        buckets.setdefault(key, []).append(item)

    issues: list[dict[str, Any]] = []
    for idx, (bucket, items) in enumerate(buckets.items(), 1):
        if idx > max_issues:
            break
        issues.append(
            {
                "issue_id": f"I-{idx:03d}",
                "question": _issue_question(bucket),
                "candidate_evidence": [
                    str(item.get("evidence_id"))
                    for item in items[:5]
                    if item.get("evidence_id")
                ],
                "decision_fields_at_risk": _decision_fields_for_bucket(bucket, items),
            }
        )
    return issues


def extract_research_debate_turns_from_text(text: Any) -> list[dict[str, Any]]:
    content = str(text or "")
    turns: list[dict[str, Any]] = []
    for obj in _json_objects(content):
        if _looks_like_research_turn(obj):
            turns.append(obj)
    return turns


def evaluate_research_turns(
    text: Any,
    *,
    evidence_ids: list[str] | set[str],
    active_issue_ids: list[str] | set[str],
    evidence_aliases: dict[str, list[str]] | None = None,
    active_issues: list[dict[str, Any]] | None = None,
) -> tuple[ResearchDebateValidation, Optional[tuple[str, Any]]]:
    """Non-raising research-turn validation.

    Returns ``(validation, violation)`` where ``validation`` always carries
    ``accepted_turns``/``rejected_turns`` (accepted may be empty) and ``violation`` is
    ``(reason, detail)`` when the contract is not fully satisfied, else ``None``.
    """
    turns = extract_research_debate_turns_from_text(text)
    if not turns:
        return (
            {"accepted_turns": [], "rejected_turns": []},
            ("missing parseable RESEARCH_DEBATE_TURN_JSON block", None),
        )
    turns = [
        _normalize_research_turn(
            turn,
            evidence_aliases=evidence_aliases or {},
            active_issues=active_issues or [],
        )
        for turn in turns
    ]
    validation = validate_research_debate_turns(
        turns,
        evidence_ids=evidence_ids,
        active_issue_ids=active_issue_ids,
    )
    if validation["rejected_turns"]:
        return validation, ("invalid RESEARCH_DEBATE_TURN_JSON", validation["rejected_turns"])
    return validation, None


def require_valid_research_turns(
    text: Any,
    *,
    stage: str,
    evidence_ids: list[str] | set[str],
    active_issue_ids: list[str] | set[str],
    evidence_aliases: dict[str, list[str]] | None = None,
    active_issues: list[dict[str, Any]] | None = None,
) -> ResearchDebateValidation:
    validation, violation = evaluate_research_turns(
        text,
        evidence_ids=evidence_ids,
        active_issue_ids=active_issue_ids,
        evidence_aliases=evidence_aliases,
        active_issues=active_issues,
    )
    if violation:
        reason, detail = violation
        raise DebateWorkflowHardFault(stage, reason, details=detail)
    return validation


def debate_context_ids_from_state(state: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    context = debate_validation_context_from_state(state)
    return context["evidence_ids"], context["issue_ids"]


def debate_validation_context_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    ledger = state.get("evidence_ledger")
    if not isinstance(ledger, list) or not ledger:
        from opentrace.graph.evidence_ledger_schema import (
            build_evidence_ledger,
            validate_admissible_evidence,
        )

        ledger = build_evidence_ledger(state)
        admissibility = validate_admissible_evidence(
            ledger,
            time_horizon=str(state.get("time_horizon") or ""),
        )
    else:
        admissibility = state.get("admissibility_report")
        if not isinstance(admissibility, dict):
            from opentrace.graph.evidence_ledger_schema import validate_admissible_evidence

            admissibility = validate_admissible_evidence(
                ledger,
                time_horizon=str(state.get("time_horizon") or ""),
            )
    issues = state.get("contested_issues")
    if not isinstance(issues, list) or not issues:
        issues = frame_contested_issues(ledger, admissibility)
    evidence_ids = [
            str(item.get("evidence_id"))
            for item in ledger
            if isinstance(item, dict) and item.get("evidence_id")
    ]
    issue_ids = [
            str(item.get("issue_id"))
            for item in issues
            if isinstance(item, dict) and item.get("issue_id")
    ]
    return {
        "evidence_ids": evidence_ids,
        "issue_ids": issue_ids,
        "issues": issues,
        "evidence_aliases": _evidence_aliases_from_state(state, ledger),
    }


def validate_research_debate_turns(
    turns: list[dict[str, Any]] | None,
    *,
    evidence_ids: list[str] | set[str],
    active_issue_ids: list[str] | set[str],
) -> ResearchDebateValidation:
    valid_evidence = {str(item) for item in evidence_ids}
    valid_issues = {str(item) for item in active_issue_ids}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for raw in turns or []:
        turn = dict(raw or {})
        reason = _research_turn_rejection_reason(turn, valid_evidence, valid_issues)
        if reason:
            rejected.append({"turn": turn, "reason": reason})
        elif schema_reason := _schema_validation_reason(ResearchDebateTurn, _research_turn_schema_payload(turn)):
            rejected.append({"turn": turn, "reason": schema_reason})
        else:
            accepted.append(turn)
    return {"accepted_turns": accepted, "rejected_turns": rejected}


def validate_thesis_ledger(
    thesis_ledger: dict[str, Any] | None,
    *,
    evidence_ids: list[str] | set[str],
    evidence_aliases: dict[str, list[str]] | None = None,
) -> ThesisLedgerValidation:
    if not isinstance(thesis_ledger, dict) or not thesis_ledger:
        return {"valid": False, "violations": ["thesis_ledger must be a non-empty object"]}
    valid_evidence = {str(item) for item in evidence_ids}
    aliases = evidence_aliases or {}
    violations: list[str] = []
    if not str(thesis_ledger.get("winning_thesis") or "").strip():
        violations.append("winning_thesis is required")
    accepted_claims = thesis_ledger.get("accepted_claims")
    if not isinstance(accepted_claims, list) or not accepted_claims:
        violations.append("accepted_claims must be a non-empty list")
    else:
        for idx, claim in enumerate(accepted_claims):
            if not isinstance(claim, dict):
                violations.append(f"accepted_claims[{idx}] must be an object")
                continue
            claim_id = str(claim.get("claim_id") or "").strip()
            if not claim_id:
                violations.append(f"accepted_claims[{idx}] missing claim_id")
            refs = [str(item) for item in claim.get("evidence_ids") or [] if str(item)]
            if not refs:
                violations.append(f"accepted_claims[{idx}] missing evidence_ids")
            missing = [
                ref
                for ref in refs
                if not _is_valid_thesis_evidence_ref(ref, valid_evidence, aliases)
            ]
            if missing:
                violations.append(f"accepted_claims[{idx}] has unknown evidence_ids: {', '.join(missing)}")
            if not str(claim.get("effect") or "").strip():
                violations.append(f"accepted_claims[{idx}] missing effect")
    constraints = thesis_ledger.get("recommended_plan_constraints")
    if not isinstance(constraints, dict) or not constraints:
        violations.append("recommended_plan_constraints must be a non-empty object")
    if not violations:
        schema_reason = _schema_validation_reason(ThesisLedger, thesis_ledger)
        if schema_reason and not _is_advisory_only_schema_failure(thesis_ledger):
            violations.append(schema_reason)
    return {"valid": not violations, "violations": violations}


# Advisory fields are narrative-only: they do not feed recommended_plan_constraints
# or the trade decision, so a shape problem confined to them must not abort the run.
_ADVISORY_THESIS_FIELDS = ("unresolved_uncertainties", "rejected_claims")


def _is_advisory_only_schema_failure(thesis_ledger: dict[str, Any]) -> bool:
    """True when the ledger validates once advisory-only fields are dropped."""
    core = {
        key: value
        for key, value in thesis_ledger.items()
        if key not in _ADVISORY_THESIS_FIELDS
    }
    return not _schema_validation_reason(ThesisLedger, core)


def require_valid_thesis_ledger(
    thesis_ledger: dict[str, Any] | None,
    *,
    stage: str,
    evidence_ids: list[str] | set[str],
    evidence_aliases: dict[str, list[str]] | None = None,
) -> ThesisLedgerValidation:
    validation = validate_thesis_ledger(
        thesis_ledger,
        evidence_ids=evidence_ids,
        evidence_aliases=evidence_aliases,
    )
    if not validation["valid"]:
        raise DebateWorkflowHardFault(
            stage,
            "invalid THESIS_LEDGER_JSON",
            details=validation["violations"],
        )
    return validation


def _is_valid_thesis_evidence_ref(
    ref: str,
    valid_evidence: set[str],
    evidence_aliases: dict[str, list[str]],
) -> bool:
    if ref in valid_evidence or ref.startswith(("inf_", "C-", "I-")):
        return True
    mapped = evidence_aliases.get(ref) or []
    return any(str(item) in valid_evidence for item in mapped)


def evaluate_risk_response_contract(
    text: Any, *, stage: str
) -> tuple[str, Optional[tuple[str, Any]]]:
    """Non-raising risk-response contract check.

    Returns ``(content, violation)`` where ``content`` is the possibly marker-normalized
    text and ``violation`` is ``(reason, detail)`` when the contract is not satisfied,
    else ``None``.
    """
    from opentrace.graph.plan_patch_schema import extract_plan_patches_from_text

    content = str(text or "")
    terminal_marker = _terminal_risk_contract_marker(content)
    patches = extract_plan_patches_from_text(content)

    if terminal_marker == "PLAN_PATCH":
        if patches:
            return content, None
        return content, ("PLAN_PATCH marker present but no parseable patch JSON", None)

    if terminal_marker in {"REJECT_PATCH", "NO_MATERIAL_CHANGE"}:
        return content, None

    # No explicit marker. Recover an unlabeled patch if one was actually emitted...
    if patches:
        logger.warning(
            "%s: parseable plan patch found without a PLAN_PATCH marker; "
            "treating as PLAN_PATCH.",
            stage,
        )
        return content + "\n\nPLAN_PATCH", None

    if "REJECT_PATCH" in content or "NO_MATERIAL_CHANGE" in content or _has_no_change_phrase(content):
        return content, None

    return content, (
        "missing risk response contract marker: expected PLAN_PATCH, REJECT_PATCH, or NO_MATERIAL_CHANGE",
        None,
    )


def require_risk_response_contract(text: Any, *, stage: str) -> str:
    """Enforce the risk-response contract before persisting debate history."""
    content, violation = evaluate_risk_response_contract(text, stage=stage)
    if violation:
        reason, detail = violation
        raise DebateWorkflowHardFault(stage, reason, details=detail)
    return content


_NO_CHANGE_PHRASES = (
    "no material change",
    "no change to the plan",
    "no changes to the plan",
    "leave the plan unchanged",
)


def _terminal_risk_contract_marker(content: str) -> str:
    for line in reversed(str(content or "").splitlines()):
        marker = line.strip()
        if marker:
            return marker if marker in {"PLAN_PATCH", "REJECT_PATCH", "NO_MATERIAL_CHANGE"} else ""
    return ""


def _has_no_change_phrase(content: str) -> bool:
    lowered = content.lower()
    return any(phrase in lowered for phrase in _NO_CHANGE_PHRASES)


def validate_trader_plan(
    plan: dict[str, Any] | None,
    *,
    evidence_ids: list[str] | set[str],
    thesis_ids: list[str] | set[str],
) -> TraderPlanValidation:
    if not isinstance(plan, dict):
        return {"valid": False, "violations": ["plan must be an object"]}

    valid_refs = {str(item) for item in evidence_ids} | {str(item) for item in thesis_ids}
    valid_refs.update({"recommended_plan_constraints", "execution_plan_compiler", "trader_self_audit"})
    links = plan.get("rationale_links")
    violations: list[str] = []
    if not isinstance(links, dict):
        return {"valid": False, "violations": ["rationale_links must be an object"]}

    execution_mode = str(plan.get("execution_mode") or "").strip()
    if execution_mode not in {"act_now", "wait_for_trigger"}:
        violations.append("execution_mode must be act_now or wait_for_trigger")

    for field in sorted(EXECUTABLE_TRADER_FIELDS):
        if field not in plan:
            continue
        refs = links.get(field)
        if not isinstance(refs, list) or not refs:
            violations.append(f"{field} missing rationale_links")
            continue
        invalid = [str(ref) for ref in refs if str(ref) not in valid_refs and not str(ref).startswith(("C-", "I-"))]
        if invalid:
            violations.append(f"{field} has invalid rationale links: {', '.join(invalid)}")

    if not violations:
        schema_reason = _schema_validation_reason(TraderPlan, plan)
        if schema_reason:
            violations.append(schema_reason)

    return {"valid": not violations, "violations": violations}


def _schema_validation_reason(model: Any, payload: dict[str, Any]) -> str:
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        msg = str(first.get("msg") or "invalid contract schema")
        return f"schema validation failed: {loc} {msg}".strip()
    return ""


def _research_turn_schema_payload(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        **turn,
        "turn_id": str(turn.get("turn_id") or ""),
        "speaker": str(turn.get("speaker") or ""),
        "issue_id": str(turn.get("issue_id") or ""),
        "position": str(turn.get("position") or ""),
        "claim": str(turn.get("claim") or ""),
        "evidence_ids": [str(item) for item in turn.get("evidence_ids") or []],
        "rebuttal_to": (
            str(turn.get("rebuttal_to"))
            if turn.get("rebuttal_to") is not None
            else None
        ),
        "falsification_condition": str(turn.get("falsification_condition") or ""),
    }


def _research_turn_rejection_reason(
    turn: dict[str, Any],
    valid_evidence: set[str],
    valid_issues: set[str],
) -> str:
    evidence_ids = [str(item) for item in turn.get("evidence_ids") or [] if str(item)]
    if not evidence_ids:
        return "missing evidence_ids"
    missing = [item for item in evidence_ids if item not in valid_evidence]
    if missing:
        return f"unknown evidence_ids: {', '.join(missing)}"
    issue_id = str(turn.get("issue_id") or "").strip()
    if issue_id not in valid_issues:
        return "unknown issue_id"
    implication = turn.get("plan_implication")
    if not isinstance(implication, dict):
        return "missing plan_implication"
    field = str(implication.get("field") or "").strip()
    if field not in ALLOWED_DECISION_FIELDS:
        return "invalid plan_implication.field"
    if "proposed_value" not in implication:
        return "missing plan_implication.proposed_value"
    if not str(turn.get("claim") or "").strip():
        return "missing claim"
    return ""


def _normalize_research_turn(
    turn: dict[str, Any],
    *,
    evidence_aliases: dict[str, list[str]],
    active_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(turn)
    evidence_ids: list[str] = []
    for raw_ref in turn.get("evidence_ids") or []:
        ref = str(raw_ref)
        mapped = evidence_aliases.get(ref, [ref])
        evidence_ids.extend(mapped)
    normalized["evidence_ids"] = list(dict.fromkeys(evidence_ids))

    issue_id = str(normalized.get("issue_id") or "").strip()
    active_issue_ids = {
        str(issue.get("issue_id"))
        for issue in active_issues
        if isinstance(issue, dict) and issue.get("issue_id")
    }
    if issue_id not in active_issue_ids:
        normalized_issue = _issue_for_evidence(normalized["evidence_ids"], active_issues)
        if normalized_issue:
            normalized["issue_id"] = normalized_issue
    return normalized


def _issue_for_evidence(evidence_ids: list[str], active_issues: list[dict[str, Any]]) -> str:
    cited = set(evidence_ids)
    best_issue = ""
    best_overlap = 0
    for issue in active_issues:
        if not isinstance(issue, dict):
            continue
        candidate = set(str(item) for item in issue.get("candidate_evidence") or [])
        overlap = len(cited & candidate)
        if overlap > best_overlap:
            best_overlap = overlap
            best_issue = str(issue.get("issue_id") or "")
    if best_issue:
        return best_issue
    if len(active_issues) == 1 and isinstance(active_issues[0], dict):
        return str(active_issues[0].get("issue_id") or "")
    return ""


def _evidence_aliases_from_state(
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for item in ledger:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        for key in ("evidence_id", "source_ref", "source_node_id"):
            alias = str(item.get(key) or "").strip()
            if alias:
                aliases.setdefault(alias, []).append(evidence_id)

    graph = state.get("evidence_graph") if isinstance(state.get("evidence_graph"), dict) else {}
    for fact in graph.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("id") or "").strip()
        mapped = aliases.get(fact_id, [])
        if not mapped:
            continue
        for source_id in fact.get("source_ids") or []:
            alias = str(source_id or "").strip()
            if alias:
                aliases.setdefault(alias, []).extend(mapped)

    for inference in graph.get("inferences") or []:
        if not isinstance(inference, dict):
            continue
        inference_id = str(inference.get("id") or "").strip()
        mapped: list[str] = []
        for fact_id in inference.get("depends_on") or []:
            mapped.extend(aliases.get(str(fact_id), []))
        if inference_id and mapped:
            aliases.setdefault(inference_id, []).extend(mapped)

    return {
        key: list(dict.fromkeys(value))
        for key, value in aliases.items()
        if key and value
    }


def _issue_bucket(item: dict[str, Any]) -> str:
    supports = {str(value) for value in item.get("supports") or []}
    contradicts = {str(value) for value in item.get("contradicts") or []}
    claim = str(item.get("claim") or "").lower()
    if "prefer_wait_for_trigger" in supports or "act_now_market_buy" in contradicts:
        return "timing"
    if "reduce_entry_aggression" in supports or "full_size_entry" in contradicts:
        return "sizing"
    if "tighten_invalidation" in supports or "stop" in claim:
        return "risk_controls"
    if "supports_long_bias" in supports:
        return "direction"
    return "execution"


def _issue_question(bucket: str) -> str:
    questions = {
        "timing": "Is the setup strong enough for ACT_NOW, or should execution wait for confirmation?",
        "sizing": "Does the evidence justify full intended size, or should position_size_pct be reduced?",
        "risk_controls": "Do the cited risks require tighter stop_loss or invalidation conditions?",
        "direction": "Does the admissible evidence support a long bias, or should action remain HOLD?",
        "execution": "Which executable trading-plan fields should change based on admissible evidence?",
    }
    return questions.get(bucket, questions["execution"])


def _decision_fields_for_bucket(bucket: str, items: list[dict[str, Any]]) -> list[str]:
    fields = {
        "timing": ["execution_mode", "entry_condition", "trigger_condition", "order_type"],
        "sizing": ["position_size_pct", "max_loss_pct"],
        "risk_controls": ["stop_loss", "invalidation_condition", "take_profit"],
        "direction": ["action", "execution_mode"],
        "execution": ["action", "execution_mode", "order_type"],
    }.get(bucket, ["action", "execution_mode"])
    if any("reduce_entry_aggression" in (item.get("supports") or []) for item in items):
        fields = [*fields, "position_size_pct"]
    return list(dict.fromkeys(fields))


def _looks_like_research_turn(obj: dict[str, Any]) -> bool:
    return bool(
        str(obj.get("turn_id") or "").strip()
        and str(obj.get("speaker") or "").strip()
        and str(obj.get("issue_id") or "").strip()
        and "plan_implication" in obj
    )


def _json_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        for end, inner in enumerate(text[start:], start=start):
            if inner == "{":
                depth += 1
            elif inner == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : end + 1])
                    except Exception:
                        break
                    if isinstance(parsed, dict):
                        objects.append(parsed)
                    break
    return objects
