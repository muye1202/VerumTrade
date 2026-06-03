from __future__ import annotations

import json
from typing import Any, TypedDict


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


def require_valid_research_turns(
    text: Any,
    *,
    stage: str,
    evidence_ids: list[str] | set[str],
    active_issue_ids: list[str] | set[str],
    evidence_aliases: dict[str, list[str]] | None = None,
    active_issues: list[dict[str, Any]] | None = None,
) -> ResearchDebateValidation:
    turns = extract_research_debate_turns_from_text(text)
    if not turns:
        raise DebateWorkflowHardFault(
            stage,
            "missing parseable RESEARCH_DEBATE_TURN_JSON block",
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
        raise DebateWorkflowHardFault(
            stage,
            "invalid RESEARCH_DEBATE_TURN_JSON",
            details=validation["rejected_turns"],
        )
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
        else:
            accepted.append(turn)
    return {"accepted_turns": accepted, "rejected_turns": rejected}


def validate_thesis_ledger(
    thesis_ledger: dict[str, Any] | None,
    *,
    evidence_ids: list[str] | set[str],
) -> ThesisLedgerValidation:
    if not isinstance(thesis_ledger, dict) or not thesis_ledger:
        return {"valid": False, "violations": ["thesis_ledger must be a non-empty object"]}
    valid_evidence = {str(item) for item in evidence_ids}
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
            missing = [ref for ref in refs if ref not in valid_evidence and not ref.startswith(("inf_", "C-", "I-"))]
            if missing:
                violations.append(f"accepted_claims[{idx}] has unknown evidence_ids: {', '.join(missing)}")
            if not str(claim.get("effect") or "").strip():
                violations.append(f"accepted_claims[{idx}] missing effect")
    constraints = thesis_ledger.get("recommended_plan_constraints")
    if not isinstance(constraints, dict) or not constraints:
        violations.append("recommended_plan_constraints must be a non-empty object")
    return {"valid": not violations, "violations": violations}


def require_valid_thesis_ledger(
    thesis_ledger: dict[str, Any] | None,
    *,
    stage: str,
    evidence_ids: list[str] | set[str],
) -> ThesisLedgerValidation:
    validation = validate_thesis_ledger(thesis_ledger, evidence_ids=evidence_ids)
    if not validation["valid"]:
        raise DebateWorkflowHardFault(
            stage,
            "invalid THESIS_LEDGER_JSON",
            details=validation["violations"],
        )
    return validation


def require_risk_response_contract(text: Any, *, stage: str) -> None:
    content = str(text or "")
    has_plan_patch = "PLAN_PATCH" in content
    has_reject_patch = "REJECT_PATCH" in content
    has_no_change = "NO_MATERIAL_CHANGE" in content
    if not (has_plan_patch or has_reject_patch or has_no_change):
        raise DebateWorkflowHardFault(
            stage,
            "missing risk response contract marker PLAN_PATCH, REJECT_PATCH, or NO_MATERIAL_CHANGE",
        )
    if has_plan_patch:
        from opentrace.graph.plan_patch_schema import extract_plan_patches_from_text

        if not extract_plan_patches_from_text(content):
            raise DebateWorkflowHardFault(
                stage,
                "PLAN_PATCH marker present but no parseable patch JSON found",
            )


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

    return {"valid": not violations, "violations": violations}


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
