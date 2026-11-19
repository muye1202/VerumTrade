"""Scenarios that separate what may happen from how the price may react.

Each scenario states an observable event/business outcome, how it reads against
the available expectation baseline, a conditional price-reaction range with its
basis (the stock's own holding-period return distribution, or a declared
assumption), when it resolves, and what would make it stop applying.

Probabilities are optional. A number must be labeled ``subjective``; it is never
derived from model agreement or evidence counts. Price levels are computed here,
deterministically, from identified operands.
"""

from __future__ import annotations

import re
from typing import Any

from verumtrade.research.calculations import (
    QUANTILES,
    Calculation,
    operand,
    price_at_return,
    reward_risk,
)

SCENARIO_KINDS = {"favorable", "adverse", "base", "delayed", "ambiguous"}
PROBABILITY_SUM_TOLERANCE = 0.05
ASYMMETRY_THRESHOLD = 1.5


def _range_bounds(reaction: dict[str, Any], price_context: dict[str, Any]) -> tuple[Any, Any, str]:
    """Return (low, high, basis) as fractions; quantile names resolve against the price context."""
    basis = str(reaction.get("basis") or "")
    if basis == "historical_distribution":
        quantiles = price_context.get("quantiles") or {}
        return quantiles.get(str(reaction.get("low"))), quantiles.get(str(reaction.get("high"))), basis
    if basis == "assumption":
        return reaction.get("low_pct"), reaction.get("high_pct"), basis
    return None, None, basis


def validate_scenarios(
    scenarios: Any,
    *,
    price_context: dict[str, Any],
    catalyst_within_horizon: bool,
) -> list[str]:
    violations: list[str] = []
    if not isinstance(scenarios, list) or len(scenarios) < 2:
        return ["scenarios must list at least two distinct outcomes"]
    seen: set[str] = set()
    numeric_probs: list[float] = []
    null_probs = 0
    kinds = set()
    for idx, scenario in enumerate(scenarios):
        label = f"scenarios[{idx}]"
        if not isinstance(scenario, dict):
            violations.append(f"{label} must be an object")
            continue
        sid = str(scenario.get("scenario_id") or "")
        if not sid or sid in seen:
            violations.append(f"{label} needs a unique scenario_id")
        seen.add(sid)
        kind = str(scenario.get("kind") or "")
        kinds.add(kind)
        if kind not in SCENARIO_KINDS:
            violations.append(f"{label}.kind must be one of {sorted(SCENARIO_KINDS)}")
        for field in ("event_outcome", "resolves_by", "invalidated_if"):
            if not str(scenario.get(field) or "").strip():
                violations.append(f"{label}.{field} is required")

        reaction = scenario.get("price_reaction")
        if not isinstance(reaction, dict):
            violations.append(f"{label}.price_reaction is required (basis historical_distribution or assumption)")
        else:
            low, high, basis = _range_bounds(reaction, price_context)
            if basis == "historical_distribution":
                if price_context.get("status") != "complete":
                    violations.append(f"{label}.price_reaction uses historical_distribution but it is unavailable")
                elif low is None or high is None:
                    violations.append(f"{label}.price_reaction low/high must be quantile names {sorted(QUANTILES)}")
            elif basis == "assumption":
                if not str(reaction.get("assumption") or "").strip():
                    violations.append(f"{label}.price_reaction with basis assumption must state the assumption")
                if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
                    violations.append(f"{label}.price_reaction low_pct/high_pct must be numbers (fractions)")
            else:
                violations.append(f"{label}.price_reaction.basis must be historical_distribution or assumption")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
                violations.append(f"{label}.price_reaction low exceeds high")

        probability = scenario.get("probability")
        if probability is None:
            null_probs += 1
        elif not isinstance(probability, dict) or probability.get("basis") != "subjective":
            violations.append(f"{label}.probability must be null or {{\"value\": x, \"basis\": \"subjective\"}}")
        else:
            value = probability.get("value")
            if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                violations.append(f"{label}.probability.value must be between 0 and 1")
            else:
                numeric_probs.append(float(value))

    if numeric_probs and null_probs:
        violations.append("probabilities must be given for every scenario or for none")
    if numeric_probs and not null_probs and abs(sum(numeric_probs) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        violations.append(f"subjective probabilities sum to {sum(numeric_probs):.2f}, not 1.0")
    if catalyst_within_horizon and not kinds & {"delayed", "ambiguous"}:
        violations.append(
            "the catalyst falls inside the holding horizon, so include a delayed or ambiguous-outcome scenario"
        )
    return violations


def _level_operand(
    name: str,
    spec: dict[str, Any] | None,
    *,
    scenarios_by_id: dict[str, dict[str, Any]],
    price_context: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
    reference: dict[str, Any],
    calc_id: str,
) -> tuple[Calculation | None, list[str]]:
    """Compute one planning level; returns (calculation, problems)."""
    if not isinstance(spec, dict):
        return None, [f"{name} level not provided"]
    basis = str(spec.get("basis") or "")
    if basis == "evidence_level":
        evidence_id = str(spec.get("evidence_id") or "")
        evidence = evidence_index.get(evidence_id)
        if evidence is None:
            return None, [f"{name} cites unknown or uncitable evidence {evidence_id}"]
        value = spec.get("value")
        if not value_supported_by_evidence(value, evidence):
            return None, [f"{name} value {value} does not appear in {evidence_id}"]
        op = operand(name, value, source="evidence", ref=evidence_id, unit="price")
        return (
            {
                "calc_id": calc_id,
                "formula": "identity",
                "expression": f"{op['value']}",
                "operands": [op],
                "result": op["value"],
                "unit": "price",
                "status": "complete",
                "missing": [],
                "notes": [],
                "version": "calc-v1",
            },
            [],
        )
    if basis == "scenario_range":
        scenario = scenarios_by_id.get(str(spec.get("scenario_id") or ""))
        bound = str(spec.get("bound") or "")
        if scenario is None or bound not in {"low", "high"}:
            return None, [f"{name} must name an existing scenario_id and bound low|high"]
        reaction = scenario.get("price_reaction") or {}
        low, high, reaction_basis = _range_bounds(reaction, price_context)
        ret = low if bound == "low" else high
        if reaction_basis == "historical_distribution":
            quantile = str(reaction.get(bound))
            ret_op = operand(
                f"{name}_return", ret, source="price_history",
                ref=f"{price_context.get('calc_id')}:{quantile}", unit="fraction",
            )
        else:
            ret_op = operand(
                f"{name}_return", ret, source="assumption", unit="fraction",
                assumption=str(reaction.get("assumption") or "unstated"),
            )
        calc = price_at_return(calc_id, reference, ret_op)
        problems = [f"{name} is missing {', '.join(calc['missing'])}"] if calc["status"] != "complete" else []
        return calc, problems
    return None, [f"{name}.basis must be scenario_range or evidence_level"]


def compute_planning_levels(
    thesis: dict[str, Any],
    *,
    reference_price: float | None,
    reference_ref: str,
    price_context: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    levels = thesis.get("planning_levels") or {}
    scenarios_by_id = {str(s.get("scenario_id")): s for s in thesis.get("scenarios") or [] if isinstance(s, dict)}
    reference = operand("reference_price", reference_price, source="market_snapshot", ref=reference_ref, unit="price")
    calculations: list[Calculation] = []
    problems: list[str] = []
    results: dict[str, float | None] = {}
    for name, calc_id in (("target", "level_target"), ("invalidation", "level_invalidation")):
        calc, issues = _level_operand(
            name,
            levels.get(name),
            scenarios_by_id=scenarios_by_id,
            price_context=price_context,
            evidence_index=evidence_index,
            reference=reference,
            calc_id=calc_id,
        )
        problems.extend(issues)
        if calc is not None:
            calculations.append(calc)
        results[name] = calc["result"] if calc and calc["status"] == "complete" else None

    out: dict[str, Any] = {
        "reference_price": reference_price,
        "target_price": results["target"],
        "invalidation_price": results["invalidation"],
        "reward_risk": None,
        "calculations": calculations,
        "missing": problems,
        "sensitivity": {},
    }
    target, stop = results["target"], results["invalidation"]
    if reference_price and target is not None and stop is not None:
        entry = operand("entry", reference_price, source="market_snapshot", ref=reference_ref, unit="price")
        rr = reward_risk(
            "reward_risk",
            entry,
            operand("target", target, source="calculation", ref="level_target", unit="price"),
            operand("stop", stop, source="calculation", ref="level_invalidation", unit="price"),
        )
        calculations.append(rr)
        out["reward_risk"] = rr["result"]
        if rr["status"] != "complete":
            problems.append("reward/risk undefined: invalidation level is not below the reference price")
        else:
            halved_target = reference_price + (target - reference_price) / 2
            halved = reward_risk(
                "reward_risk_reaction_halved",
                entry,
                operand("target_halved", halved_target, source="calculation", ref="level_target", unit="price"),
                operand("stop", stop, source="calculation", ref="level_invalidation", unit="price"),
            )
            calculations.append(halved)
            out["sensitivity"] = {
                "reward_risk_if_reaction_halved": halved["result"],
                "asymmetry_survives_smaller_reaction": bool(
                    halved["result"] is not None and halved["result"] >= ASYMMETRY_THRESHOLD
                ),
            }
    return out


def value_supported_by_evidence(value: Any, evidence: dict[str, Any], tolerance: float = 0.005) -> bool:
    """True when ``value`` matches a number stated in the evidence (allowing unit scale words)."""
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    candidates: list[float] = []
    for raw in (evidence.get("numeric_values") or {}).values():
        try:
            candidates.append(float(raw))
        except (TypeError, ValueError):
            continue
    provenance = evidence.get("provenance") or {}
    if isinstance(provenance.get("value"), (int, float)):
        candidates.append(float(provenance["value"]))
    text = f"{evidence.get('claim', '')} {evidence.get('raw_excerpt', '')}"
    for match in re.finditer(r"-?\d[\d,]*\.?\d*", text):
        try:
            candidates.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    for candidate in candidates:
        for scale in (1.0, 1e3, 1e6, 1e9, 0.01):
            scaled = candidate * scale
            if scaled == target or (target != 0 and abs(scaled - target) / abs(target) <= tolerance):
                return True
    return False


def scenario_analysis_from_thesis(thesis: dict[str, Any], levels: dict[str, Any]) -> dict[str, Any]:
    """Project the thesis onto the scenario_analysis shape the execution compiler consumes."""
    scenarios = [s for s in thesis.get("scenarios") or [] if isinstance(s, dict)]

    def group(kinds: set[str]) -> list[dict[str, Any]]:
        return [s for s in scenarios if s.get("kind") in kinds]

    def probability(items: list[dict[str, Any]]) -> float | None:
        values = [(s.get("probability") or {}).get("value") for s in items]
        if not items or any(not isinstance(v, (int, float)) for v in values):
            return None
        return round(sum(float(v) for v in values), 2)

    def evidence(items: list[dict[str, Any]]) -> list[str]:
        return list(dict.fromkeys(e for s in items for e in s.get("evidence_ids") or []))

    favorable, adverse = group({"favorable"}), group({"adverse"})
    middle = group({"base", "delayed", "ambiguous"})
    rr = levels.get("reward_risk")
    missing = list(levels.get("missing") or [])
    if levels.get("reference_price") is None:
        missing.append("reference_price")
    numeric = probability(scenarios) is not None
    return {
        "bull_case": {
            "path": "; ".join(str(s.get("event_outcome")) for s in favorable) or "No favorable scenario stated.",
            "probability": probability(favorable),
            "target_price": levels.get("target_price"),
            "evidence": evidence(favorable),
        },
        "base_case": {
            "path": "; ".join(str(s.get("event_outcome")) for s in middle) or "No base/delayed/ambiguous scenario stated.",
            "probability": probability(middle),
            "expected_price_zone": None,
            "evidence": evidence(middle),
        },
        "bear_case": {
            "path": "; ".join(str(s.get("event_outcome")) for s in adverse) or "No adverse scenario stated.",
            "probability": probability(adverse),
            "invalidation_price": levels.get("invalidation_price"),
            "evidence": evidence(adverse),
        },
        "dominant_risk": str((thesis.get("strongest_alternative") or {}).get("explanation") or ""),
        "asymmetric_payoff": bool(rr is not None and rr >= ASYMMETRY_THRESHOLD),
        "risk_reward_estimate": rr,
        "status": "complete" if levels.get("target_price") is not None and levels.get("invalidation_price") is not None else "incomplete",
        "missing_inputs": list(dict.fromkeys(missing)),
        "probability_basis": "subjective" if numeric else "not_estimated",
        "price_level_basis": "thesis_planning_levels",
        "scenarios": scenarios,
        "calculations": levels.get("calculations") or [],
        "sensitivity": levels.get("sensitivity") or {},
    }
