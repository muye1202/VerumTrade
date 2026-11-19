"""Swing thesis: a falsifiable, evidence-linked explanation compiled after research.

The thesis answers five questions with typed fields: what changed, how it reaches
the company (mechanism), what expectations are observable (baseline), what may
happen within the holding period (catalyst and scenarios), and what would
distinguish it from its strongest alternative. A model writes the thesis; this
module validates it deterministically and computes every number from
identified operands. Narrative wording never sets a status, probability or level.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Callable

from verumtrade.execution.decision_guard import (
    apply_verified_close_fallback,
    build_market_snapshot,
    verified_close_from_state,
)
from verumtrade.research.calculations import (
    FORMULAS,
    CalculationError,
    holding_return_distribution,
    operand,
    parse_closes_csv,
)
from verumtrade.research.expectations import BASELINE_TYPES, build_expectation_snapshot
from verumtrade.research.scenarios import (
    compute_planning_levels,
    scenario_analysis_from_thesis,
    validate_scenarios,
    value_supported_by_evidence,
)

logger = logging.getLogger(__name__)

THESIS_MARKER = "SWING_THESIS_JSON"
ACTIONS = {"BUY", "SELL", "HOLD", "NO_TRADE"}
EXECUTION_MODES = {"act_now", "wait_for_trigger"}
_PRICED_IN = re.compile(r"\b(?:not\s+)?priced[\s-]+in\b", re.IGNORECASE)


# --- Extraction -----------------------------------------------------------


def extract_swing_thesis(text: Any) -> dict[str, Any]:
    content = str(text or "")
    idx = content.upper().rfind(THESIS_MARKER)
    if idx < 0:
        return {}
    tail = content[idx + len(THESIS_MARKER):]
    start = tail.find("{")
    if start < 0:
        return {}
    depth, end, in_string, escape = 0, -1, False, False
    for pos, char in enumerate(tail[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end < 0:
        return {}
    try:
        parsed = json.loads(tail[start:end])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --- Evidence and inputs --------------------------------------------------


def build_evidence_index(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Citable ledger items by E-* ID (rejected evidence is excluded)."""
    from verumtrade.graph.debate_schema import citable_evidence_ids_from_state

    ledger = [item for item in state.get("evidence_ledger") or [] if isinstance(item, dict)]
    citable = set(citable_evidence_ids_from_state(state, ledger))
    return {str(item["evidence_id"]): item for item in ledger if item.get("evidence_id") in citable}


def load_price_context(
    ticker: str,
    as_of: str,
    horizon_days: int,
    *,
    fetch: Callable[[str, str, str], str] | None = None,
    lookback_years: int = 3,
) -> dict[str, Any]:
    """Holding-period return distribution from daily closes up to ``as_of``."""
    try:
        end = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return {"status": "incomplete", "missing": ["valid as-of date"], "quantiles": {}}
    start = end - timedelta(days=365 * lookback_years)
    try:
        if fetch is None:
            from verumtrade.dataflows.interface import route_to_vendor_raw

            raw = route_to_vendor_raw("get_stock_data", ticker, start.isoformat(), end.isoformat())
        else:
            raw = fetch(ticker, start.isoformat(), end.isoformat())
    except Exception as exc:
        return {"status": "incomplete", "missing": [f"price history ({type(exc).__name__})"], "quantiles": {}}
    if "[TRUNCATED" in str(raw):
        return {"status": "incomplete", "missing": ["complete price history (vendor output was truncated)"], "quantiles": {}}
    dates, closes = parse_closes_csv(str(raw))
    keep = [(d, c) for d, c in zip(dates, closes) if not d or d <= end.isoformat()]
    gap = _largest_gap_days([d for d, _ in keep])
    if gap is not None and gap > MAX_PRICE_GAP_DAYS:
        return {
            "status": "incomplete",
            "missing": [f"continuous price history (a {gap}-day gap between closes)"],
            "quantiles": {},
        }
    context = holding_return_distribution([c for _, c in keep], horizon_days, dates=[d for d, _ in keep])
    context["source"] = "get_stock_data"
    if keep:
        # Point-in-time anchor: the last close on or before as_of, from the same verified series.
        context["last_close"] = keep[-1][1]
        context["last_close_date"] = keep[-1][0]
    return context


MAX_PRICE_GAP_DAYS = 10


def _largest_gap_days(dates: list[str]) -> int | None:
    parsed = []
    for value in dates:
        try:
            parsed.append(date.fromisoformat(value[:10]))
        except ValueError:
            continue
    if len(parsed) < 2:
        return None
    return max((b - a).days for a, b in zip(parsed, parsed[1:]))


def catalyst_in_horizon(state: dict[str, Any], horizon_calendar_days: int) -> bool:
    """Deterministic check: does a dated upcoming event fall inside the holding window?"""
    report = state.get("catalyst_event_report_structured") or {}
    try:
        start = date.fromisoformat(str(state.get("trade_date") or "")[:10])
    except ValueError:
        return False
    end = start + timedelta(days=horizon_calendar_days)
    for event in report.get("upcoming_events") or []:
        if not isinstance(event, dict):
            continue
        try:
            when = date.fromisoformat(str(event.get("event_time") or event.get("date") or "")[:10])
        except ValueError:
            continue
        if start <= when <= end:
            return True
    return False


# --- Validation -----------------------------------------------------------


def _collect_evidence_ids(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"evidence_ids", "evidence_id"}:
                values = value if isinstance(value, list) else [value]
                found.extend(str(v) for v in values if v)
            else:
                found.extend(_collect_evidence_ids(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_evidence_ids(item))
    return found


# Unambiguous alternative operand names: a change written as {old, new} is new - old.
OPERAND_ALIASES: dict[str, dict[str, str]] = {
    "difference": {"new": "a", "old": "b"},
}
OPERAND_GUIDE = "; ".join(f"{name}({', '.join(args)})" for name, (_, args) in FORMULAS.items())


def run_thesis_calculations(
    thesis: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Execute model-requested calculations against identified operands only."""
    results, problems = [], []
    for idx, request in enumerate(thesis.get("calculations") or []):
        label = f"calculations[{idx}]"
        if not isinstance(request, dict):
            problems.append(f"{label} must be an object")
            continue
        formula = str(request.get("formula") or "")
        if formula not in FORMULAS:
            problems.append(f"{label}.formula must be one of {sorted(FORMULAS)}")
            continue
        fn, names = FORMULAS[formula]
        raw_operands = request.get("operands") or {}
        if isinstance(raw_operands, dict):
            for alias, name in OPERAND_ALIASES.get(formula, {}).items():
                if name not in raw_operands and alias in raw_operands:
                    raw_operands = {**raw_operands, name: raw_operands[alias]}
        ops = []
        for name in names:
            raw = raw_operands.get(name) if isinstance(raw_operands, dict) else None
            if not isinstance(raw, dict):
                given = sorted(raw_operands) if isinstance(raw_operands, dict) else []
                problems.append(
                    f"{label}.operands.{name} is required ({formula} takes operands {', '.join(names)}; got {', '.join(given) or 'none'})"
                )
                break
            try:
                if raw.get("evidence_id"):
                    evidence = evidence_index.get(str(raw["evidence_id"]))
                    if evidence is None:
                        problems.append(f"{label}.operands.{name} cites uncitable evidence {raw['evidence_id']}")
                        break
                    if not value_supported_by_evidence(raw.get("value"), evidence):
                        problems.append(f"{label}.operands.{name} value {raw.get('value')} not found in {raw['evidence_id']}")
                        break
                    ops.append(operand(name, raw.get("value"), source="evidence", ref=str(raw["evidence_id"])))
                else:
                    ops.append(
                        operand(name, raw.get("value"), source="assumption", assumption=str(raw.get("assumption") or ""))
                    )
            except CalculationError as exc:
                problems.append(f"{label}.operands.{name}: {exc}")
                break
        else:
            calc = fn(str(request.get("calc_id") or f"thesis_calc_{idx + 1}"), *ops)
            calc["purpose"] = str(request.get("purpose") or "")
            results.append(calc)
    return results, problems


def validate_swing_thesis(
    thesis: dict[str, Any],
    *,
    evidence_index: dict[str, dict[str, Any]],
    expectation_snapshot: dict[str, Any],
    price_context: dict[str, Any],
    catalyst_within_horizon: bool,
) -> dict[str, Any]:
    if not thesis:
        return {"valid": False, "violations": [f"missing parseable {THESIS_MARKER} block"], "warnings": []}
    violations: list[str] = []
    warnings: list[str] = []

    what_changed = thesis.get("what_changed") or {}
    if not str(what_changed.get("summary") or "").strip():
        violations.append("what_changed.summary is required")
    mechanism = thesis.get("mechanism") or {}
    if not [step for step in mechanism.get("chain") or [] if str(step).strip()]:
        violations.append("mechanism.chain must list the steps from the change to this security")

    expectations = thesis.get("expectations") or {}
    baseline = str(expectations.get("baseline_type") or "")
    if baseline not in BASELINE_TYPES:
        violations.append(f"expectations.baseline_type must be one of {sorted(BASELINE_TYPES)}")
    if baseline == "consensus_snapshot" and not expectation_snapshot.get("consensus_available"):
        violations.append(
            "expectations.baseline_type is consensus_snapshot but no consensus was recorded on or before the as-of date"
        )
    if baseline in {"management_guidance", "analyst_statement"} and not expectations.get("evidence_ids"):
        violations.append(f"expectations.baseline_type {baseline} must cite the evidence that states it")
    if baseline == "none" and expectations.get("surprise_measurable"):
        violations.append("expectations.surprise_measurable cannot be true without a baseline")
    if baseline == "none" and _PRICED_IN.search(json.dumps(thesis)):
        warnings.append("thesis argues about what is priced in without any expectation baseline")

    catalyst = thesis.get("catalyst") or {}
    if not str(catalyst.get("event") or "").strip():
        violations.append("catalyst.event is required (state 'none identified' explicitly if there is none)")
    within = catalyst_within_horizon or bool(catalyst.get("within_horizon"))
    if catalyst_within_horizon and catalyst.get("within_horizon") is False:
        warnings.append("catalyst report shows a dated event inside the horizon; treating within_horizon as true")

    alternative = thesis.get("strongest_alternative") or {}
    if not str(alternative.get("explanation") or "").strip():
        violations.append("strongest_alternative.explanation is required")
    if not str(alternative.get("distinguishing_evidence") or "").strip():
        violations.append("strongest_alternative.distinguishing_evidence must say what would tell the two apart")

    violations.extend(
        validate_scenarios(thesis.get("scenarios"), price_context=price_context, catalyst_within_horizon=within)
    )

    decision = thesis.get("conditional_decision") or {}
    action = str(decision.get("action") or "").upper()
    if action not in ACTIONS:
        violations.append(f"conditional_decision.action must be one of {sorted(ACTIONS)}")
    if str(decision.get("execution_mode") or "") not in EXECUTION_MODES:
        violations.append(f"conditional_decision.execution_mode must be one of {sorted(EXECUTION_MODES)}")
    premises = [p for p in thesis.get("key_premises") or [] if isinstance(p, dict)]
    if not premises:
        violations.append("key_premises must list the premises the decision depends on")
    flips = [p for p in premises if str(p.get("action_if_false") or "").upper() not in {"", action}]
    if flips and not [c for c in decision.get("conditions") or [] if isinstance(c, dict) and c.get("if")]:
        violations.append(
            "a key premise changes the action if false, so conditional_decision.conditions must state the conditional decision"
        )
    if not [f for f in thesis.get("falsifiers") or [] if str(f).strip()]:
        violations.append("falsifiers must list observations that would disprove the thesis")

    unknown = sorted({eid for eid in _collect_evidence_ids(thesis) if eid not in evidence_index})
    if unknown:
        violations.append(f"cites evidence that is unknown or not citable: {', '.join(unknown)}")
    _, calc_problems = run_thesis_calculations(thesis, evidence_index)
    violations.extend(calc_problems)
    return {"valid": not violations, "violations": violations, "warnings": warnings, "catalyst_within_horizon": within}


# --- Graph node -----------------------------------------------------------

THESIS_PROMPT = """You are compiling the swing-trade thesis for {ticker} as of {as_of} (holding horizon: {horizon}).
The research and debate are finished. Turn them into a falsifiable thesis. Do not add facts that are not in
the evidence below; cite canonical E-* evidence IDs. Numbers you need must come from cited evidence or be
stated as assumptions; this system computes all price levels itself.

Rules:
- Separate what may happen (event/business outcome) from how the price may react.
- Each scenario's price_reaction uses either the stock's own holding-period return quantiles
  (basis "historical_distribution", low/high = quantile names such as "p25", "p75") or a stated assumption
  (basis "assumption", low_pct/high_pct as fractions, plus the assumption text).
- If the catalyst falls inside the horizon, include a "delayed" or "ambiguous" scenario.
- calculations use exactly these formulas and operand names: {operand_guide}.
  difference(a, b) computes a - b. Each operand is {{"evidence_id": "E-...", "value": <number as written in that evidence>}}
  or {{"value": <number>, "assumption": "..."}}.
- probability is null unless you give a subjective estimate for every scenario: {{"value": 0.3, "basis": "subjective"}}.
- expectations.baseline_type is "consensus_snapshot" only if CONSENSUS SNAPSHOT below lists observations.
  If no baseline exists, use "none" and set surprise_measurable to false; do not claim something is or is not priced in.
- planning_levels may be null when no defensible level exists; a thesis can be useful without a target.
- If a key premise would change the action if false, give conditional_decision.conditions.

End your reply with:
{marker}
{{
  "what_changed": {{"summary": "...", "evidence_ids": ["E-..."]}},
  "mechanism": {{"chain": ["change", "transmission", "effect on this company"], "evidence_ids": ["E-..."], "assumptions": ["..."]}},
  "expectations": {{"baseline_type": "management_guidance|consensus_snapshot|analyst_statement|price_implied|framework_forecast|none", "baseline": "...", "evidence_ids": [], "surprise_measurable": false}},
  "catalyst": {{"event": "...", "expected_window": "YYYY-MM-DD..YYYY-MM-DD or unknown", "within_horizon": true, "resolution_source": "..."}},
  "strongest_alternative": {{"explanation": "...", "distinguishing_evidence": "..."}},
  "scenarios": [
    {{"scenario_id": "S1", "kind": "favorable|adverse|base|delayed|ambiguous", "event_outcome": "...",
      "interpretation_vs_expectations": "...", "price_reaction": {{"basis": "historical_distribution", "low": "p50", "high": "p90"}},
      "probability": null, "evidence_ids": ["E-..."], "assumptions": ["..."], "resolves_by": "...", "invalidated_if": "..."}}
  ],
  "key_premises": [{{"premise": "...", "evidence_ids": ["E-..."], "action_if_false": "HOLD"}}],
  "planning_levels": {{"target": {{"basis": "scenario_range", "scenario_id": "S1", "bound": "high"}},
                       "invalidation": {{"basis": "evidence_level", "evidence_id": "E-MKT-003", "value": 0.0}}}},
  "conditional_decision": {{"action": "BUY|SELL|HOLD|NO_TRADE", "execution_mode": "act_now|wait_for_trigger",
                            "conditions": [{{"if": "...", "then_action": "..."}}]}},
  "falsifiers": ["..."],
  "calculations": [{{"calc_id": "C1", "formula": "pct_change", "purpose": "...",
                     "operands": {{"old": {{"evidence_id": "E-FUN-001", "value": 0}}, "new": {{"evidence_id": "E-FUN-002", "value": 0}}}}}}]
}}

REFERENCE PRICE: {reference_price}

HOLDING-PERIOD RETURN DISTRIBUTION (base rate, not a forecast):
{price_context}

CONSENSUS SNAPSHOT:
{expectations}

CATALYST REPORT (structured):
{catalyst}

RESEARCH MANAGER PLAN AND THESIS LEDGER:
{manager_plan}

{research_results}

{revision_context}

CITABLE EVIDENCE:
{evidence}"""


def _evidence_lines(evidence_index: dict[str, dict[str, Any]], limit: int = 60) -> str:
    ranked = sorted(evidence_index.values(), key=lambda item: -float(item.get("criticality") or 0.0))[:limit]
    return "\n".join(
        f"- {item['evidence_id']} [{item.get('source_type') or 'vendor'}] {str(item.get('claim') or '')[:260]}"
        for item in ranked
    ) or "(none)"


def compile_thesis_outputs(
    thesis: dict[str, Any],
    validation: dict[str, Any],
    *,
    evidence_index: dict[str, dict[str, Any]],
    price_context: dict[str, Any],
    reference_price: float | None,
    reference_ref: str = "market_snapshot.reference_price",
) -> dict[str, Any]:
    """Deterministic levels, calculations and scenario projection for a validated thesis."""
    if not validation.get("valid"):
        return {"thesis_levels": {}, "thesis_calculations": [], "thesis_scenario_analysis": {}}
    levels = compute_planning_levels(
        thesis,
        reference_price=reference_price,
        reference_ref=reference_ref,
        price_context=price_context,
        evidence_index=evidence_index,
    )
    calculations, _ = run_thesis_calculations(thesis, evidence_index)
    action = str((thesis.get("conditional_decision") or {}).get("action") or "").upper()
    target, stop = levels.get("target_price"), levels.get("invalidation_price")
    if action == "BUY" and reference_price and target is not None and stop is not None:
        if not stop < reference_price < target:
            levels["missing"].append("planning levels are inconsistent with a long position (need stop < price < target)")
            levels["target_price"] = levels["invalidation_price"] = levels["reward_risk"] = None
    return {
        "thesis_levels": levels,
        "thesis_calculations": calculations,
        "thesis_scenario_analysis": scenario_analysis_from_thesis(thesis, levels),
    }


def create_thesis_compiler(
    llm: Any,
    *,
    price_fetch: Callable[[str, str, str], str] | None = None,
    expectation_loader: Callable[[str, str], dict[str, Any]] | None = None,
    revision_store: Any = None,
):
    def thesis_compiler_node(state: dict[str, Any]) -> dict[str, Any]:
        from verumtrade.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
        from verumtrade.agents.utils.llm.llm_rate_limit import invoke_with_backoff
        from verumtrade.graph.debate_schema import degrade_or_raise, intermediate_gates_hard, invoke_with_contract_repair
        from verumtrade.dataflows.config import get_config
        from verumtrade.research.requests import format_research_results
        from verumtrade.research.revision import (
            format_revision_context,
            get_thesis_store,
            prepare_revision_context,
            thesis_store_enabled,
            validate_forecast_resolutions,
        )
        from verumtrade.research.thesis_diff import build_change_record, cited_source_map

        config = get_config()
        ticker = str(state.get("company_of_interest") or "")
        as_of = str(state.get("trade_date") or "")
        spec = get_time_horizon_spec(state.get("time_horizon"))
        horizon_days = max(1, round(sum(spec.trading_days_range) / 2))
        calendar_days = int(spec.weeks_range[1] * 7)

        snapshot = state.get("market_snapshot") or {}
        reference_price = None
        for key in ("reference_price", "last_price", "last", "close"):
            try:
                value = float(snapshot.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                reference_price = value
                break

        price_context = load_price_context(ticker, as_of, horizon_days, fetch=price_fetch)
        reference_ref = "market_snapshot.reference_price"
        snapshot_update: dict[str, Any] = {}
        verified_close = verified_close_from_state({"price_context": price_context})
        if reference_price is None and verified_close:
            # No live quote: anchor on the last verified close at or before as_of, and publish
            # it in market_snapshot so the trader and risk judge use the same anchor.
            base = snapshot or build_market_snapshot(
                symbol=ticker,
                market_report=str(state.get("market_report") or ""),
                snapshot_source=str(config.get("decision_snapshot_source") or "executor_quote_first"),
            )
            snapshot_update = {"market_snapshot": apply_verified_close_fallback(base, verified_close)}
            reference_price = verified_close["value"]
            reference_ref = f"price_context.last_close@{verified_close['date']}"
        expectation_snapshot = (expectation_loader or build_expectation_snapshot)(ticker, as_of)
        evidence_index = build_evidence_index(state)
        within = catalyst_in_horizon(state, calendar_days)

        store = revision_store or (get_thesis_store() if thesis_store_enabled(config) else None)
        revision_context = None
        if store is not None:
            try:
                revision_context = prepare_revision_context(state, store)
            except Exception as exc:  # history is advisory; a broken store must not stop the run
                logger.warning("Thesis revision context unavailable: %s", exc)

        prompt = THESIS_PROMPT.format(
            ticker=ticker,
            as_of=as_of,
            horizon=spec.label,
            marker=THESIS_MARKER,
            operand_guide=OPERAND_GUIDE,
            reference_price=reference_price if reference_price is not None else "UNAVAILABLE (do not derive price levels)",
            price_context=json.dumps(
                {k: price_context.get(k) for k in ("status", "horizon_trading_days", "sample_size", "quantiles", "selection_rule", "caveats", "missing")},
                ensure_ascii=False,
            ),
            expectations=json.dumps(expectation_snapshot, ensure_ascii=False, default=str),
            catalyst=json.dumps(state.get("catalyst_event_report_structured") or {}, ensure_ascii=False, default=str)[:3000],
            manager_plan=str(state.get("investment_plan") or "")[:6000],
            research_results=format_research_results(state),
            revision_context=format_revision_context(revision_context),
            evidence=_evidence_lines(evidence_index),
        )

        def _invoke(text: str):
            return invoke_with_backoff(llm, text, key="thesis_compiler")

        def _validate(content: str) -> dict[str, Any]:
            candidate = extract_swing_thesis(content)
            result = validate_swing_thesis(
                candidate,
                evidence_index=evidence_index,
                expectation_snapshot=expectation_snapshot,
                price_context=price_context,
                catalyst_within_horizon=within,
            )
            if candidate:
                result["violations"].extend(validate_forecast_resolutions(candidate, revision_context))
                result["valid"] = not result["violations"]
            return result

        def _check(content: str):
            result = _validate(content)
            return None if result["valid"] else result["violations"]

        response = invoke_with_contract_repair(
            prompt,
            stage="thesis_compiler",
            invoke=_invoke,
            check=_check,
            max_repair_attempts=int(config.get("debate_contract_repair_attempts", 2) or 0),
        )
        thesis = extract_swing_thesis(response.content)
        validation = _validate(response.content)
        if not validation["valid"]:
            degraded = degrade_or_raise(
                "thesis_compiler",
                f"invalid {THESIS_MARKER}",
                validation["violations"],
                hard=intermediate_gates_hard(config),
            )
            if degraded:
                validation = {**validation, "gate_degradation": degraded}

        outputs = compile_thesis_outputs(
            thesis,
            validation,
            evidence_index=evidence_index,
            price_context=price_context,
            reference_price=reference_price,
            reference_ref=reference_ref,
        )
        change_record = {}
        if validation.get("valid") and revision_context is not None:
            change_record = build_change_record(
                revision_context.get("prior"),
                thesis,
                impact=revision_context.get("impact") or {},
                levels=outputs.get("thesis_levels") or {},
                cited=cited_source_map(thesis, list(state.get("evidence_ledger") or [])),
            )
        return {
            "swing_thesis": thesis,
            "swing_thesis_validation": validation,
            "price_context": price_context,
            "thesis_reference_price": {"value": reference_price, "source": reference_ref if reference_price else None},
            **snapshot_update,
            "expectation_snapshot": expectation_snapshot,
            "thesis_revision_context": revision_context or {},
            "thesis_change_record": change_record,
            **outputs,
        }

    return thesis_compiler_node


def thesis_compiler_enabled(config: dict[str, Any] | None = None) -> bool:
    from verumtrade.dataflows.config import get_config

    settings = (config or get_config()).get("thesis_compiler") or {}
    return bool(settings.get("enabled", False))


def scenario_mode(config: dict[str, Any] | None = None) -> str:
    from verumtrade.dataflows.config import get_config

    settings = (config or get_config()).get("thesis_compiler") or {}
    return str(settings.get("scenario_mode") or "thesis")
