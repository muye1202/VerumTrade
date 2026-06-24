from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, TypedDict


Direction = Literal["bullish", "bearish", "neutral", "risk_only"]
Freshness = Literal["fresh", "stale", "unknown"]
DecisionRelevance = Literal["entry", "exit", "sizing", "timing", "risk", "thesis"]

DOMAINS = ("market", "fundamentals", "news", "sentiment", "catalyst")
CATALYST_OVERRIDES = {
    "freeze_new_buys",
    "risk_judge_review",
    "reduce_position",
    "exit_review",
}


class EvidenceClaim(TypedDict):
    claim: str
    direction: Direction
    strength: float
    freshness: Freshness
    decision_relevance: DecisionRelevance
    source_domain: str
    source_ids: list[str]


class CurrentPosition(TypedDict):
    has_position: bool
    shares: int | None
    cost_basis: float | None
    unrealized_pnl_pct: float | None
    portfolio_weight_pct: float | None
    available_buying_power: float | None
    concentration_flags: list[str]


class TraderDecisionBrief(TypedDict):
    ticker: str
    as_of: str
    reference_price: float | None
    time_horizon: str
    current_position: CurrentPosition
    evidence_by_domain: dict[str, list[EvidenceClaim]]
    top_supporting_evidence: list[EvidenceClaim]
    top_opposing_evidence: list[EvidenceClaim]
    missing_or_stale_data: list[str]
    hard_constraints: list[str]


class TradeSetupDiagnosis(TypedDict):
    primary_setup: str
    secondary_setup: str | None
    setup_quality: str
    why_this_setup: list[str]
    what_would_invalidate_setup: list[str]
    setup_requires_trigger: bool
    trigger_type: str
    entry_status: str


class ScenarioCase(TypedDict, total=False):
    path: str
    probability: float
    target_price: float | None
    expected_price_zone: str
    invalidation_price: float | None
    evidence: list[str]


class ScenarioAnalysis(TypedDict):
    bull_case: ScenarioCase
    base_case: ScenarioCase
    bear_case: ScenarioCase
    dominant_risk: str
    asymmetric_payoff: bool
    risk_reward_estimate: float | None


class ExecutionPlanCompiler(TypedDict):
    recommended_decision_version: str
    recommended_execution_intent: str
    recommended_action: str
    order_type: str
    time_in_force: str
    limit_price: float | None
    stop_price: float | None
    trail_percent: float | None
    trail_price: float | None
    stop_loss: float
    take_profit: float
    position_size_pct: float | None
    confidence: str
    compiler_checks: list[str]
    canonical_json_contract: dict[str, Any]


class TraderSelfAudit(TypedDict):
    passed: bool
    violations: list[str]
    repairs_applied: list[str]
    final_action_consistent: bool


class TraderPlanV1(TypedDict, total=False):
    plan_id: str
    action: str
    execution_mode: str
    order_type: str
    position_size_pct: float | None
    entry_condition: str | None
    stop_loss: float | None
    take_profit: float | None
    rationale_links: dict[str, list[str]]


def build_trader_decision_brief(state: dict[str, Any] | None) -> TraderDecisionBrief:
    state = state or {}
    graph = state.get("evidence_graph") if isinstance(state.get("evidence_graph"), dict) else {}
    facts = _facts_by_id(graph.get("facts") if isinstance(graph, dict) else [])
    evidence_by_domain: dict[str, list[EvidenceClaim]] = {domain: [] for domain in DOMAINS}

    for inference in graph.get("inferences", []) if isinstance(graph, dict) else []:
        claim = _claim_from_inference(inference, facts)
        if not claim:
            continue
        evidence_by_domain.setdefault(claim["source_domain"], []).append(claim)

    _add_catalyst_claims(evidence_by_domain, state.get("catalyst_event_report_structured"))

    supporting = _top_claims(evidence_by_domain, {"bullish"})
    opposing = _top_claims(evidence_by_domain, {"bearish", "risk_only"})
    missing = _missing_or_stale_data(state, graph, evidence_by_domain)
    hard_constraints = _hard_constraints(state.get("catalyst_event_report_structured"))

    return {
        "ticker": str(state.get("company_of_interest") or ""),
        "as_of": str(state.get("trade_date") or ""),
        "reference_price": _reference_price(state.get("market_snapshot")),
        "time_horizon": str(state.get("time_horizon") or ""),
        "current_position": _parse_current_position(state.get("portfolio_context")),
        "evidence_by_domain": evidence_by_domain,
        "top_supporting_evidence": supporting,
        "top_opposing_evidence": opposing,
        "missing_or_stale_data": missing,
        "hard_constraints": hard_constraints,
    }


def build_scenario_analysis(
    brief: TraderDecisionBrief | dict[str, Any],
    setup_diagnosis: TradeSetupDiagnosis | dict[str, Any],
) -> ScenarioAnalysis:
    reference_price = _float_or_none(brief.get("reference_price")) or 100.0
    supporting = [
        claim
        for claim in brief.get("top_supporting_evidence", []) or []
        if isinstance(claim, dict)
    ]
    opposing = [
        claim
        for claim in brief.get("top_opposing_evidence", []) or []
        if isinstance(claim, dict)
    ]
    support_score = sum(float(claim.get("strength", 0.0) or 0.0) for claim in supporting)
    oppose_score = sum(float(claim.get("strength", 0.0) or 0.0) for claim in opposing)
    setup_quality = str(setup_diagnosis.get("setup_quality") or "C").upper()
    entry_status = str(setup_diagnosis.get("entry_status") or "unclear").lower()

    bull_prob = _scenario_probability(0.42 + support_score * 0.06 - oppose_score * 0.03)
    if setup_quality in {"C", "D"} or entry_status in {"early", "overextended", "unclear"}:
        bull_prob = min(bull_prob, 0.45)
    bear_prob = _scenario_probability(0.28 + oppose_score * 0.05)
    base_prob = max(0.10, round(1.0 - bull_prob - bear_prob, 2))
    if bull_prob + bear_prob + base_prob != 1.0:
        base_prob = round(1.0 - bull_prob - bear_prob, 2)

    target_pct = _target_pct(setup_diagnosis, support_score, oppose_score)
    invalidation_pct = _invalidation_pct(setup_diagnosis, oppose_score)
    target_price = _round_price(reference_price * (1.0 + target_pct))
    invalidation_price = _round_price(reference_price * (1.0 - invalidation_pct))
    risk = max(reference_price - invalidation_price, 0.0)
    reward = max(target_price - reference_price, 0.0)
    risk_reward = round(reward / risk, 2) if risk > 0 else None

    dominant_risk = _dominant_risk(opposing)
    primary_setup = str(setup_diagnosis.get("primary_setup") or "NO_TRADE")

    return {
        "bull_case": {
            "path": f"{primary_setup} confirms and price works toward the next reward zone.",
            "probability": bull_prob,
            "target_price": target_price,
            "evidence": _evidence_ids(supporting),
        },
        "base_case": {
            "path": "Setup remains unresolved; preserve optionality until trigger quality improves.",
            "probability": base_prob,
            "expected_price_zone": f"{_round_price(reference_price * 0.98)}-{_round_price(reference_price * 1.04)}",
            "evidence": _evidence_ids([*supporting[:2], *opposing[:2]]),
        },
        "bear_case": {
            "path": "Setup fails or catalyst risk dominates before confirmation.",
            "probability": bear_prob,
            "invalidation_price": invalidation_price,
            "evidence": _evidence_ids(opposing),
        },
        "dominant_risk": dominant_risk,
        "asymmetric_payoff": bool(risk_reward is not None and risk_reward >= 1.5),
        "risk_reward_estimate": risk_reward,
    }


def build_execution_plan_compiler(
    *,
    decision_brief: TraderDecisionBrief | dict[str, Any],
    setup_diagnosis: TradeSetupDiagnosis | dict[str, Any],
    scenario_analysis: ScenarioAnalysis | dict[str, Any],
    market_snapshot: dict[str, Any] | None = None,
    market_session_context: str = "",
    portfolio_context: str = "",
    catalyst_event_report_structured: dict[str, Any] | None = None,
) -> ExecutionPlanCompiler:
    reference_price = (
        _reference_price(market_snapshot)
        or _float_or_none(decision_brief.get("reference_price"))
        or 100.0
    )
    stop_loss = _round_price(
        ((scenario_analysis.get("bear_case") or {}).get("invalidation_price"))
        or reference_price * 0.94
    )
    take_profit = _round_price(
        ((scenario_analysis.get("bull_case") or {}).get("target_price"))
        or reference_price * 1.10
    )
    current_position = decision_brief.get("current_position") or {}
    has_position = bool(current_position.get("has_position"))
    shares = current_position.get("shares")
    shares = shares if isinstance(shares, int) else None
    setup_quality = str(setup_diagnosis.get("setup_quality") or "C").upper()
    entry_status = str(setup_diagnosis.get("entry_status") or "unclear").lower()
    primary_setup = str(setup_diagnosis.get("primary_setup") or "NO_TRADE")
    hard_constraints = [str(item) for item in decision_brief.get("hard_constraints") or []]
    catalyst_action = str((catalyst_event_report_structured or {}).get("recommended_action") or "").strip()

    checks: list[str] = []
    actionable = setup_quality in {"A", "B"} and entry_status == "confirmed"
    if setup_quality in {"C", "D"}:
        checks.append(f"setup quality {setup_quality} prefers HOLD/WAIT_FOR_TRIGGER")
        actionable = False
    if entry_status in {"early", "overextended", "invalid", "unclear"}:
        checks.append(f"entry_status {entry_status} is not immediate-actionable")
        actionable = False
    if catalyst_action in CATALYST_OVERRIDES:
        checks.append(f"catalyst override {catalyst_action} applied")
        actionable = False
    if primary_setup == "NO_TRADE":
        checks.append("NO_TRADE setup forces HOLD")
        actionable = False

    if actionable:
        action = "BUY"
        execution_intent = "ACT_NOW"
        decision_version = "v1"
        order_type = _session_safe_order_type(market_session_context)
        limit_price = _round_price(reference_price) if order_type == "LIMIT" else None
        position_size_pct = _position_size_pct(setup_quality, scenario_analysis)
    else:
        action = "HOLD"
        execution_intent = "WAIT_FOR_TRIGGER"
        decision_version = "v2"
        order_type = "MARKET"
        limit_price = None
        position_size_pct = None

    if primary_setup == "DEFENSIVE_EXIT" and has_position and shares:
        action = "SELL"
        execution_intent = "ACT_NOW" if actionable else "WAIT_FOR_TRIGGER"
        decision_version = "v1" if execution_intent == "ACT_NOW" else "v2"
        position_size_pct = None
        checks.append("existing position allows defensive sell/reduce path")
    elif action == "SELL" and not has_position:
        action = "HOLD"
        execution_intent = "WAIT_FOR_TRIGGER"
        decision_version = "v2"
        checks.append("SELL prohibited because current_position has zero shares")

    confidence = _compiler_confidence(setup_quality, entry_status, hard_constraints)
    compiler = {
        "recommended_decision_version": decision_version,
        "recommended_execution_intent": execution_intent,
        "recommended_action": action,
        "order_type": order_type,
        "time_in_force": "DAY",
        "limit_price": limit_price,
        "stop_price": None,
        "trail_percent": None,
        "trail_price": None,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size_pct": position_size_pct,
        "confidence": confidence,
        "compiler_checks": list(dict.fromkeys(checks)),
        "canonical_json_contract": {},
    }
    compiler["canonical_json_contract"] = _canonical_contract(
        compiler,
        ticker=str(decision_brief.get("ticker") or ""),
        time_horizon=str(decision_brief.get("time_horizon") or ""),
        shares=shares,
        scenario_analysis=scenario_analysis,
    )
    return compiler


def apply_trader_self_audit(
    *,
    decision_brief: TraderDecisionBrief | dict[str, Any],
    setup_diagnosis: TradeSetupDiagnosis | dict[str, Any],
    scenario_analysis: ScenarioAnalysis | dict[str, Any],
    execution_plan_compiler: ExecutionPlanCompiler | dict[str, Any],
    market_snapshot: dict[str, Any] | None = None,
    market_session_context: str = "",
    catalyst_event_report_structured: dict[str, Any] | None = None,
) -> tuple[TraderSelfAudit, ExecutionPlanCompiler]:
    repaired: dict[str, Any] = dict(execution_plan_compiler or {})
    violations: list[str] = []
    repairs: list[str] = []
    reference_price = (
        _reference_price(market_snapshot)
        or _float_or_none(decision_brief.get("reference_price"))
        or 100.0
    )
    current_position = decision_brief.get("current_position") or {}
    has_position = bool(current_position.get("has_position"))
    setup_quality = str(setup_diagnosis.get("setup_quality") or "C").upper()
    entry_status = str(setup_diagnosis.get("entry_status") or "unclear").lower()
    catalyst_action = str((catalyst_event_report_structured or {}).get("recommended_action") or "").strip()
    action = str(repaired.get("recommended_action") or "HOLD").upper()
    intent = str(repaired.get("recommended_execution_intent") or "").upper()

    if action == "BUY" and not (setup_quality in {"A", "B"} and entry_status == "confirmed"):
        violations.append("BUY blocked by setup diagnosis: requires A/B quality and confirmed entry status.")
        _repair_to_wait_hold(repaired)
        repairs.append("set action to HOLD and execution intent to WAIT_FOR_TRIGGER")
    if action == "BUY" and catalyst_action in {"freeze_new_buys", "risk_judge_review"}:
        violations.append(f"BUY blocked by catalyst override: {catalyst_action}.")
        _repair_to_wait_hold(repaired)
        repairs.append(f"catalyst override blocked BUY: {catalyst_action}")
    if action == "SELL" and not has_position:
        violations.append("SELL blocked because current_position.has_position is false.")
        _repair_to_wait_hold(repaired)
        repairs.append("set SELL to HOLD because no position exists")

    action = str(repaired.get("recommended_action") or "HOLD").upper()
    intent = str(repaired.get("recommended_execution_intent") or "").upper()
    if (
        intent == "ACT_NOW"
        and str(setup_diagnosis.get("setup_requires_trigger")).lower() == "true"
    ):
        violations.append("ACT_NOW blocked because setup requires trigger.")
        _repair_to_wait_hold(repaired)
        repairs.append("set execution intent to WAIT_FOR_TRIGGER because setup requires trigger")

    if action in {"BUY", "SELL"} and _session_safe_order_type(market_session_context) == "LIMIT":
        if str(repaired.get("order_type") or "").upper() == "MARKET":
            violations.append("MARKET order blocked because market session is closed.")
            repaired["order_type"] = "LIMIT"
            repaired["limit_price"] = _round_price(reference_price)
            repairs.append("replaced MARKET with LIMIT at reference price for closed session")

    stop_loss = _float_or_none(repaired.get("stop_loss"))
    take_profit = _float_or_none(repaired.get("take_profit"))
    if stop_loss is None:
        violations.append("STOP_LOSS missing or non-numeric.")
        repaired["stop_loss"] = _round_price((scenario_analysis.get("bear_case") or {}).get("invalidation_price") or reference_price * 0.94)
        repairs.append("filled STOP_LOSS from scenario bear-case invalidation")
    if take_profit is None:
        violations.append("TAKE_PROFIT missing or non-numeric.")
        repaired["take_profit"] = _round_price((scenario_analysis.get("bull_case") or {}).get("target_price") or reference_price * 1.10)
        repairs.append("filled TAKE_PROFIT from scenario bull-case target")

    for field in ("limit_price", "stop_price", "stop_loss", "take_profit"):
        value = _float_or_none(repaired.get(field))
        if value is not None and not _within_reference_band(value, reference_price):
            violations.append(f"{field} is outside allowed range of market_snapshot.reference_price.")
            if field == "limit_price":
                repaired[field] = None if action == "HOLD" else _round_price(reference_price)
                repairs.append(f"repaired {field} to reference-compatible value")

    if action == "HOLD":
        if str(repaired.get("order_type") or "").upper() != "MARKET":
            repaired["order_type"] = "MARKET"
            repairs.append("set HOLD order_type to MARKET sentinel")
        for key in ("limit_price", "stop_price", "trail_percent", "trail_price", "position_size_pct"):
            if repaired.get(key) is not None:
                repaired[key] = None
                repairs.append(f"cleared {key} for HOLD")

    repaired["compiler_checks"] = list(
        dict.fromkeys([*(repaired.get("compiler_checks") or []), *violations])
    )
    repaired["canonical_json_contract"] = _canonical_contract(
        repaired,
        ticker=str(decision_brief.get("ticker") or ""),
        time_horizon=str(decision_brief.get("time_horizon") or ""),
        shares=(current_position.get("shares") if isinstance(current_position.get("shares"), int) else None),
        scenario_analysis=scenario_analysis,
    )
    final_action = str(repaired.get("recommended_action") or "HOLD").upper()
    final_intent = str(repaired.get("recommended_execution_intent") or "").upper()
    final_action_consistent = _final_action_consistent(final_action, final_intent, setup_diagnosis, decision_brief)
    audit: TraderSelfAudit = {
        "passed": not violations,
        "violations": list(dict.fromkeys(violations)),
        "repairs_applied": list(dict.fromkeys(repairs)),
        "final_action_consistent": final_action_consistent,
    }
    return audit, repaired  # type: ignore[return-value]


def build_trader_plan_v1(state: dict[str, Any] | None) -> TraderPlanV1:
    state = state or {}
    compiler = state.get("execution_plan_compiler") if isinstance(state.get("execution_plan_compiler"), dict) else {}
    contract = compiler.get("canonical_json_contract") if isinstance(compiler.get("canonical_json_contract"), dict) else {}
    evidence_ids = _ledger_evidence_ids_by_source(state)
    opposing = _claim_source_ids((state.get("trader_decision_brief") or {}).get("top_opposing_evidence"))
    supporting = _claim_source_ids((state.get("trader_decision_brief") or {}).get("top_supporting_evidence"))
    all_refs = _map_source_ids_to_evidence([*supporting, *opposing], evidence_ids)
    if not all_refs:
        all_refs = ["execution_plan_compiler"]

    action = str(
        compiler.get("recommended_action")
        or contract.get("action")
        or "HOLD"
    ).strip().upper()
    execution_mode = _execution_mode(
        compiler.get("recommended_execution_intent")
        or contract.get("execution_intent")
    )
    order_type = str(compiler.get("order_type") or _contract_order_type(contract) or "MARKET").strip().upper()
    entry_condition = _entry_condition(contract, execution_mode)
    position_size_pct = _float_or_none(compiler.get("position_size_pct"))
    stop_loss = _float_or_none(compiler.get("stop_loss") or _contract_action_template_value(contract, "stop_loss"))
    take_profit = _float_or_none(compiler.get("take_profit") or _contract_action_template_value(contract, "take_profit"))

    links: dict[str, list[str]] = {
        "action": all_refs,
        "execution_mode": all_refs,
        "order_type": ["execution_plan_compiler", *all_refs[:2]],
        "position_size_pct": ["recommended_plan_constraints", *all_refs],
        "entry_condition": all_refs,
        "stop_loss": all_refs,
        "take_profit": all_refs,
    }
    return {
        "plan_id": "trader_plan_v1",
        "action": action,
        "execution_mode": execution_mode,
        "order_type": order_type,
        "position_size_pct": position_size_pct,
        "entry_condition": entry_condition,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rationale_links": {key: list(dict.fromkeys(value)) for key, value in links.items()},
    }


def build_trade_setup_diagnosis(brief: TraderDecisionBrief | dict[str, Any]) -> TradeSetupDiagnosis:
    evidence_by_domain = brief.get("evidence_by_domain") or {}
    all_claims = [
        claim
        for domain_claims in evidence_by_domain.values()
        for claim in (domain_claims or [])
        if isinstance(claim, dict)
    ]
    text = " ".join(str(claim.get("claim") or "") for claim in all_claims).lower()
    supporting = [claim for claim in all_claims if claim.get("direction") == "bullish"]
    opposing = [claim for claim in all_claims if claim.get("direction") in {"bearish", "risk_only"}]
    support_score = sum(float(claim.get("strength", 0.0) or 0.0) for claim in supporting)
    oppose_score = sum(float(claim.get("strength", 0.0) or 0.0) for claim in opposing)
    hard_constraints = [str(item) for item in brief.get("hard_constraints") or []]
    has_position = bool((brief.get("current_position") or {}).get("has_position"))

    primary, secondary = _setup_class(text, support_score, oppose_score, has_position)
    entry_status = _entry_status(text, support_score, oppose_score)
    trigger_type = _trigger_type(text, hard_constraints)
    requires_trigger = entry_status in {"early", "overextended", "unclear"} or trigger_type != "none"

    quality = _quality(primary, entry_status, support_score, oppose_score, hard_constraints)
    invalidators = _invalidators(all_claims, primary)
    reasons = _setup_reasons(primary, entry_status, supporting, opposing, hard_constraints)

    return {
        "primary_setup": primary,
        "secondary_setup": secondary,
        "setup_quality": quality,
        "why_this_setup": reasons,
        "what_would_invalidate_setup": invalidators,
        "setup_requires_trigger": requires_trigger,
        "trigger_type": trigger_type,
        "entry_status": entry_status,
    }


def _facts_by_id(raw_facts: Any) -> dict[str, dict[str, Any]]:
    return {
        str(fact.get("id")): fact
        for fact in raw_facts or []
        if isinstance(fact, dict) and str(fact.get("id") or "").strip()
    }


def _ledger_evidence_ids_by_source(state: dict[str, Any]) -> dict[str, str]:
    ledger = state.get("evidence_ledger")
    if not isinstance(ledger, list) or not ledger:
        from verumtrade.graph.evidence_ledger_schema import build_evidence_ledger

        ledger = build_evidence_ledger(state)
    out: dict[str, str] = {}
    for item in ledger:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        for key in ("source_ref", "source_node_id"):
            source = str(item.get(key) or "").strip()
            if source:
                out[source] = evidence_id
    return out


def _claim_source_ids(claims: Any) -> list[str]:
    out: list[str] = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        out.extend(str(item) for item in claim.get("source_ids") or [] if str(item))
    return list(dict.fromkeys(out))


def _map_source_ids_to_evidence(source_ids: list[str], evidence_by_source: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(evidence_by_source[item] for item in source_ids if item in evidence_by_source))


def _execution_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text == "act_now":
        return "act_now"
    return "wait_for_trigger"


def _contract_order_type(contract: dict[str, Any]) -> str | None:
    if contract.get("order_type"):
        return str(contract.get("order_type"))
    plan = contract.get("execution_plan")
    if isinstance(plan, list) and plan:
        template = plan[0].get("action_template") if isinstance(plan[0], dict) else None
        if isinstance(template, dict) and template.get("order_type"):
            return str(template.get("order_type"))
    return None


def _contract_action_template_value(contract: dict[str, Any], key: str) -> Any:
    if key in contract:
        return contract.get(key)
    plan = contract.get("execution_plan")
    if isinstance(plan, list) and plan:
        template = plan[0].get("action_template") if isinstance(plan[0], dict) else None
        if isinstance(template, dict):
            return template.get(key)
    return None


def _entry_condition(contract: dict[str, Any], execution_mode: str) -> str | None:
    if execution_mode == "act_now":
        return "act_now"
    plan = contract.get("execution_plan")
    if isinstance(plan, list) and plan:
        branch = plan[0] if isinstance(plan[0], dict) else {}
        branch_id = str(branch.get("branch_id") or contract.get("default_action") or "").strip()
        if branch_id:
            return branch_id
    return str(contract.get("default_action") or "wait_for_trigger").strip()


def _claim_from_inference(inference: Any, facts: dict[str, dict[str, Any]]) -> EvidenceClaim | None:
    if not isinstance(inference, dict):
        return None
    claim_text = str(inference.get("claim") or "").strip()
    if not claim_text:
        return None
    domain = str(inference.get("domain") or "market").strip().lower()
    if domain not in DOMAINS:
        domain = "market"
    fact_ids = [str(item) for item in inference.get("depends_on", []) or [] if str(item)]
    source_ids = [str(inference.get("id") or f"inf_{domain}"), *fact_ids]
    related_facts = [facts[fact_id] for fact_id in fact_ids if fact_id in facts]
    return {
        "claim": claim_text,
        "direction": _direction(inference.get("stance"), claim_text),
        "strength": _clamp01(inference.get("confidence"), 0.5),
        "freshness": _freshness(related_facts),
        "decision_relevance": _decision_relevance(claim_text),
        "source_domain": domain,
        "source_ids": source_ids,
    }


def _add_catalyst_claims(
    evidence_by_domain: dict[str, list[EvidenceClaim]],
    catalyst_report: Any,
) -> None:
    if not isinstance(catalyst_report, dict) or not catalyst_report:
        return
    rating = str(catalyst_report.get("event_risk_rating") or "UNKNOWN").strip().upper()
    action = str(catalyst_report.get("recommended_action") or "continue_analysis").strip()
    if rating in {"HIGH", "CRITICAL"} or action in CATALYST_OVERRIDES:
        evidence_by_domain.setdefault("catalyst", []).append(
            {
                "claim": f"Catalyst/event risk is {rating}; recommended action is {action}.",
                "direction": "risk_only",
                "strength": 0.95 if rating == "CRITICAL" else 0.85,
                "freshness": "fresh",
                "decision_relevance": "risk",
                "source_domain": "catalyst",
                "source_ids": ["catalyst_event_report_structured"],
            }
        )
    for idx, item in enumerate(catalyst_report.get("near_term_catalysts") or [], 1):
        text = str(item).strip()
        if text:
            evidence_by_domain.setdefault("catalyst", []).append(
                {
                    "claim": text,
                    "direction": "risk_only",
                    "strength": 0.7,
                    "freshness": "fresh",
                    "decision_relevance": "timing",
                    "source_domain": "catalyst",
                    "source_ids": [f"near_term_catalyst_{idx:03d}"],
                }
            )


def _top_claims(
    evidence_by_domain: dict[str, list[EvidenceClaim]],
    directions: set[str],
    *,
    limit: int = 5,
) -> list[EvidenceClaim]:
    items = [
        claim
        for domain_claims in evidence_by_domain.values()
        for claim in domain_claims
        if claim.get("direction") in directions
    ]
    return sorted(items, key=lambda claim: float(claim.get("strength", 0.0)), reverse=True)[:limit]


def _missing_or_stale_data(
    state: dict[str, Any],
    graph: dict[str, Any],
    evidence_by_domain: dict[str, list[EvidenceClaim]],
) -> list[str]:
    missing: list[str] = []
    for issue in graph.get("audit_issues", []) if isinstance(graph, dict) else []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "").strip()
        domain = str(issue.get("domain") or "").strip()
        if code:
            missing.append(f"{domain}: {code}" if domain else code)
    catalyst_report = state.get("catalyst_event_report_structured")
    if isinstance(catalyst_report, dict):
        missing.extend(str(item) for item in catalyst_report.get("data_quality_notes") or [] if str(item))
    for domain, claims in evidence_by_domain.items():
        if not claims:
            missing.append(f"{domain}: no decision-ready evidence claims")
        for claim in claims:
            if claim.get("freshness") == "stale":
                missing.append(f"{domain}: stale evidence - {claim.get('claim')}")
    return list(dict.fromkeys(item for item in missing if item))


def _hard_constraints(catalyst_report: Any) -> list[str]:
    if not isinstance(catalyst_report, dict):
        return []
    constraints: list[str] = []
    action = str(catalyst_report.get("recommended_action") or "").strip()
    rating = str(catalyst_report.get("event_risk_rating") or "").strip().upper()
    if action in CATALYST_OVERRIDES:
        constraints.append(f"catalyst_override:{action}")
    if rating in {"HIGH", "CRITICAL"}:
        constraints.append(f"event_risk_rating:{rating}")
    constraints.extend(str(item) for item in catalyst_report.get("risk_controls") or [] if str(item))
    return constraints


def _parse_current_position(portfolio_context: Any) -> CurrentPosition:
    text = str(portfolio_context or "")
    lower = text.lower()
    shares = _first_int(r"(\d+)\s+shares?", text)
    has_position = shares is not None and shares > 0
    if "zero shares" in lower or "0 shares" in lower or "no position" in lower:
        has_position = False
        shares = 0
    return {
        "has_position": has_position,
        "shares": shares,
        "cost_basis": _first_float(r"cost basis\s*\$?([0-9,.]+)", text),
        "unrealized_pnl_pct": _first_float(r"unrealized\s+pnl\s*([+-]?[0-9,.]+)\s*%", text),
        "portfolio_weight_pct": _first_float(r"portfolio weight\s*([0-9,.]+)\s*%", text),
        "available_buying_power": _first_float(r"(?:effective\s+)?buying power\s*\$?([0-9,.]+)", text),
        "concentration_flags": _concentration_flags(text),
    }


def _concentration_flags(text: str) -> list[str]:
    flags: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|;", text):
        if "concentration" in sentence.lower() or "near cap" in sentence.lower():
            clean = sentence.strip(" .")
            if clean:
                flags.append(clean)
    return flags


def _reference_price(snapshot: Any) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    for key in ("reference_price", "last_price", "last", "close"):
        value = _float_or_none(snapshot.get(key))
        if value is not None:
            return value
    return None


def _direction(stance: Any, claim: str) -> Direction:
    stance_text = str(stance or "").lower()
    if stance_text in {"bullish", "bearish", "neutral"}:
        return stance_text  # type: ignore[return-value]
    lower = claim.lower()
    if any(term in lower for term in ("risk", "uncertain", "unconfirmed", "freeze", "overextended")):
        return "risk_only"
    if any(term in lower for term in ("breakout", "upside", "bull", "support", "growth")):
        return "bullish"
    if any(term in lower for term in ("bear", "downside", "weak", "sell", "breaks")):
        return "bearish"
    return "neutral"


def _decision_relevance(claim: str) -> DecisionRelevance:
    lower = claim.lower()
    if any(term in lower for term in ("breakout", "entry", "support", "resistance", "pullback")):
        return "entry"
    if any(term in lower for term in ("exit", "sell", "reduce")):
        return "exit"
    if any(term in lower for term in ("size", "concentration", "capital", "weight")):
        return "sizing"
    if any(term in lower for term in ("timing", "event", "earnings", "trigger", "early", "overextended")):
        return "timing"
    if any(term in lower for term in ("risk", "uncertain", "unconfirmed", "stale")):
        return "risk"
    return "thesis"


def _freshness(facts: list[dict[str, Any]]) -> Freshness:
    if not facts:
        return "unknown"
    if any(str(fact.get("quality") or "").lower() == "stale" for fact in facts):
        return "stale"
    if any(str(fact.get("as_of") or "").strip() for fact in facts):
        return "fresh"
    return "unknown"


def _setup_class(
    text: str,
    support_score: float,
    oppose_score: float,
    has_position: bool,
) -> tuple[str, str | None]:
    if support_score <= 0.0 and oppose_score <= 0.0:
        return "NO_TRADE", None
    if "post-earnings" in text or "post earnings" in text:
        return "POST_EARNINGS_DRIFT", None
    if "squeeze" in text or "flow" in text:
        return "SQUEEZE_OR_FLOW_TRADE", None
    if "pullback" in text:
        return "PULLBACK_ENTRY", None
    if "mean reversion" in text or "bounce" in text:
        return "MEAN_REVERSION_BOUNCE", None
    if "breakout" in text or "resistance" in text:
        return "BREAKOUT_CONFIRMATION", "MOMENTUM_CONTINUATION"
    if "event" in text or "catalyst" in text or "earnings" in text:
        return "EVENT_DRIVEN_CATALYST", None
    if has_position and oppose_score > support_score:
        return "DEFENSIVE_EXIT", "POSITION_MANAGEMENT_ONLY"
    if support_score > oppose_score:
        return "MOMENTUM_CONTINUATION", None
    return "NO_TRADE", None


def _entry_status(text: str, support_score: float, oppose_score: float) -> str:
    if "invalid" in text or "broken" in text or "thesis-breaking" in text:
        return "invalid"
    if "overextended" in text or "chasing" in text:
        return "overextended"
    if "early" in text or "unconfirmed" in text or "wait" in text:
        return "early"
    if support_score > 0.0 and support_score >= oppose_score:
        return "confirmed"
    if oppose_score > support_score:
        return "unclear"
    return "unclear"


def _trigger_type(text: str, hard_constraints: list[str]) -> str:
    if any("freeze_new_buys" in constraint or "risk_judge_review" in constraint for constraint in hard_constraints):
        return "event"
    if any(term in text for term in ("earnings", "event", "catalyst")):
        return "event"
    if "volume" in text:
        return "volume"
    if any(term in text for term in ("price", "breakout", "support", "resistance")):
        return "price"
    if any(term in text for term in ("date", "time", "week")):
        return "time"
    return "none"


def _quality(
    primary: str,
    entry_status: str,
    support_score: float,
    oppose_score: float,
    hard_constraints: list[str],
) -> str:
    if primary == "NO_TRADE" or entry_status == "invalid":
        return "D"
    if hard_constraints or entry_status in {"overextended", "early", "unclear"}:
        return "C"
    edge = support_score - oppose_score
    if edge >= 0.75:
        return "A"
    if edge >= 0.25:
        return "B"
    return "C"


def _invalidators(all_claims: list[dict[str, Any]], primary: str) -> list[str]:
    invalidators = [
        str(claim.get("claim") or "").strip()
        for claim in all_claims
        if str(claim.get("decision_relevance") or "") in {"risk", "timing"}
        and claim.get("direction") in {"bearish", "risk_only"}
    ][:4]
    if primary in {"BREAKOUT_CONFIRMATION", "MOMENTUM_CONTINUATION"}:
        invalidators.append("Close back below breakout/support level or loss of volume confirmation.")
    if not invalidators:
        invalidators.append("Evidence edge fails to confirm or fresh contradictory data appears.")
    return list(dict.fromkeys(invalidators))


def _setup_reasons(
    primary: str,
    entry_status: str,
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    hard_constraints: list[str],
) -> list[str]:
    reasons = [f"Classified as {primary} with entry_status={entry_status}."]
    reasons.extend(str(claim.get("claim")) for claim in supporting[:2] if claim.get("claim"))
    reasons.extend(str(claim.get("claim")) for claim in opposing[:2] if claim.get("claim"))
    reasons.extend(hard_constraints[:3])
    return list(dict.fromkeys(item for item in reasons if item))


def _scenario_probability(value: float) -> float:
    return round(max(0.10, min(0.70, value)), 2)


def _target_pct(setup_diagnosis: dict[str, Any], support_score: float, oppose_score: float) -> float:
    setup = str(setup_diagnosis.get("primary_setup") or "").upper()
    entry_status = str(setup_diagnosis.get("entry_status") or "").lower()
    base = 0.10
    if setup in {"BREAKOUT_CONFIRMATION", "MOMENTUM_CONTINUATION", "POST_EARNINGS_DRIFT"}:
        base = 0.125
    elif setup in {"PULLBACK_ENTRY", "MEAN_REVERSION_BOUNCE"}:
        base = 0.09
    elif setup in {"EVENT_DRIVEN_CATALYST", "SQUEEZE_OR_FLOW_TRADE"}:
        base = 0.14
    if entry_status == "confirmed":
        base += 0.015
    return max(0.04, min(0.18, base))


def _invalidation_pct(setup_diagnosis: dict[str, Any], oppose_score: float) -> float:
    setup = str(setup_diagnosis.get("primary_setup") or "").upper()
    base = 0.06
    if setup in {"BREAKOUT_CONFIRMATION", "MOMENTUM_CONTINUATION"}:
        base = 0.06
    elif setup in {"PULLBACK_ENTRY", "MEAN_REVERSION_BOUNCE"}:
        base = 0.045
    elif setup in {"EVENT_DRIVEN_CATALYST", "SQUEEZE_OR_FLOW_TRADE"}:
        base = 0.075
    return max(0.03, min(0.12, base))


def _dominant_risk(opposing: list[dict[str, Any]]) -> str:
    if not opposing:
        return "No dominant opposing risk identified in the brief."
    top = sorted(opposing, key=lambda claim: float(claim.get("strength", 0.0) or 0.0), reverse=True)[0]
    return str(top.get("claim") or "Dominant opposing risk is unspecified.")


def _evidence_ids(claims: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for claim in claims:
        ids.extend(str(item) for item in claim.get("source_ids", []) or [] if str(item))
    return list(dict.fromkeys(ids))


def _session_safe_order_type(market_session_context: str) -> str:
    text = str(market_session_context or "").lower()
    closed_terms = ("closed", "pre-market", "premarket", "after-hours", "after hours", "overnight", "weekend")
    return "LIMIT" if any(term in text for term in closed_terms) else "MARKET"


def _position_size_pct(setup_quality: str, scenario_analysis: dict[str, Any]) -> float:
    risk_reward = _float_or_none(scenario_analysis.get("risk_reward_estimate")) or 1.0
    if setup_quality == "A" and risk_reward >= 2.0:
        return 0.12
    if setup_quality in {"A", "B"}:
        return 0.08
    return 0.04


def _compiler_confidence(setup_quality: str, entry_status: str, hard_constraints: list[str]) -> str:
    if hard_constraints or setup_quality in {"C", "D"} or entry_status != "confirmed":
        return "LOW"
    if setup_quality == "A":
        return "HIGH"
    return "MEDIUM"


def _canonical_contract(
    compiler: dict[str, Any],
    *,
    ticker: str,
    time_horizon: str,
    shares: int | None,
    scenario_analysis: dict[str, Any],
) -> dict[str, Any]:
    if compiler["recommended_decision_version"] == "v1":
        quantity = shares if compiler["recommended_action"] == "SELL" else None
        return {
            "decision_version": "v1",
            "execution_intent": "act_now",
            "action": compiler["recommended_action"],
            "ticker": ticker,
            "quantity": quantity,
            "order_type": compiler["order_type"],
            "time_in_force": compiler["time_in_force"],
            "extended_hours": False,
            "limit_price": compiler["limit_price"],
            "stop_price": compiler["stop_price"],
            "trail_percent": compiler["trail_percent"],
            "trail_price": compiler["trail_price"],
            "stop_loss": compiler["stop_loss"],
            "take_profit": compiler["take_profit"],
            "position_size_pct": compiler["position_size_pct"],
            "time_horizon": time_horizon,
            "confidence": compiler["confidence"],
            "rationale": "Compiled from setup diagnosis and scenario reward/risk.",
            "override_reason": None,
        }

    return {
        "decision_version": "v2",
        "ticker": ticker,
        "plan_mode": "conditional",
        "execution_plan": [
            {
                "branch_id": "setup_confirmation",
                "priority": 1,
                "conditions": {
                    "price": {
                        "close_above": (scenario_analysis.get("bull_case") or {}).get("target_price"),
                        "last_price": None,
                        "close_below": None,
                        "tolerance_pct": 0.0,
                    },
                    "schedule": {"session_constraint": "MARKET_HOURS"},
                },
                "event_conditions": [],
                "action_template": {
                    "action": "BUY",
                    "quantity": None,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "extended_hours": False,
                    "limit_price": (scenario_analysis.get("bull_case") or {}).get("target_price"),
                    "stop_price": None,
                    "trail_percent": None,
                    "trail_price": None,
                    "stop_loss": compiler["stop_loss"],
                    "take_profit": compiler["take_profit"],
                    "position_size_pct": 0.06,
                    "time_horizon": time_horizon,
                    "confidence": compiler["confidence"],
                    "rationale": "Conditional entry only after setup confirmation.",
                },
            }
        ],
        "default_action": None,
        "time_horizon": time_horizon,
        "confidence": compiler["confidence"],
        "rationale": "Wait for trigger because current setup is not immediately actionable.",
        "action": "HOLD",
        "execution_intent": "wait_for_trigger",
        "override_reason": None,
    }


def _repair_to_wait_hold(compiler: dict[str, Any]) -> None:
    compiler["recommended_action"] = "HOLD"
    compiler["recommended_execution_intent"] = "WAIT_FOR_TRIGGER"
    compiler["recommended_decision_version"] = "v2"
    compiler["order_type"] = "MARKET"
    compiler["limit_price"] = None
    compiler["stop_price"] = None
    compiler["trail_percent"] = None
    compiler["trail_price"] = None
    compiler["position_size_pct"] = None


def _within_reference_band(value: float, reference_price: float) -> bool:
    if reference_price <= 0:
        return True
    return abs(value - reference_price) / reference_price <= 0.30


def _final_action_consistent(
    action: str,
    intent: str,
    setup_diagnosis: dict[str, Any],
    decision_brief: dict[str, Any],
) -> bool:
    setup_quality = str(setup_diagnosis.get("setup_quality") or "C").upper()
    entry_status = str(setup_diagnosis.get("entry_status") or "unclear").lower()
    has_position = bool((decision_brief.get("current_position") or {}).get("has_position"))
    hard_constraints = [str(item) for item in decision_brief.get("hard_constraints") or []]
    if action == "BUY":
        if not (setup_quality in {"A", "B"} and entry_status == "confirmed"):
            return False
        if any("freeze_new_buys" in item or "risk_judge_review" in item for item in hard_constraints):
            return False
    if action == "SELL" and not has_position:
        return False
    if str(setup_diagnosis.get("setup_requires_trigger")).lower() == "true" and intent == "ACT_NOW":
        return False
    return True


def _round_price(value: Any) -> float:
    parsed = _float_or_none(value)
    if parsed is None:
        return 0.0
    return float(Decimal(str(parsed)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(str(match.group(1)).replace(",", ""))
    except Exception:
        return None


def _first_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _float_or_none(match.group(1))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _clamp01(value: Any, default: float = 0.0) -> float:
    parsed = _float_or_none(value)
    number = default if parsed is None else parsed
    return max(0.0, min(1.0, number))
