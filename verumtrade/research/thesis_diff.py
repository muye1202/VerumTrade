"""Cross-run thesis changes: which dependencies moved, and what that did to the thesis.

E-* evidence IDs are renumbered every run, so a thesis version stores the stable
source reference behind each cited ID plus a fingerprint of that evidence. On the
next run, each prior dependency is classified as unchanged, changed, restated or
not refreshed; thesis components that cite a moved dependency are "affected" and
everything else is reported as still supported. A run whose evidence refresh
failed is reported as such rather than as an unchanged thesis.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

THESIS_COMPONENTS = ("what_changed", "mechanism", "expectations", "key_premises", "scenarios", "planning_levels")


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def evidence_fingerprint(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") or {}
    basis = {
        "claim": _norm(item.get("claim")),
        "excerpt": _norm(item.get("raw_excerpt")),
        "content_hash": provenance.get("content_hash"),
        "value": provenance.get("value"),
        "period_end": provenance.get("period_end"),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _ids_in(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"evidence_ids", "evidence_id"}:
                found.update(str(v) for v in (value if isinstance(value, list) else [value]) if v)
            else:
                found |= _ids_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _ids_in(item)
    return found


def cited_source_map(thesis: dict[str, Any], ledger: list[dict[str, Any]]) -> dict[str, str]:
    """E-* IDs cited by the thesis mapped to their stable source references."""
    by_id = {str(item.get("evidence_id")): str(item.get("source_ref") or "") for item in ledger if isinstance(item, dict)}
    return {eid: by_id[eid] for eid in sorted(_ids_in(thesis)) if by_id.get(eid)}


def evidence_snapshot(cited: dict[str, str], ledger: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_ref = {str(item.get("source_ref")): item for item in ledger if isinstance(item, dict)}
    snapshot = {}
    for ref in sorted(set(cited.values())):
        item = by_ref.get(ref) or {}
        provenance = item.get("provenance") or {}
        snapshot[ref] = {
            "claim": str(item.get("claim") or "")[:300],
            "fingerprint": evidence_fingerprint(item),
            "concept": provenance.get("concept"),
            "period_end": provenance.get("period_end"),
        }
    return snapshot


def _component_refs(thesis: dict[str, Any], cited: dict[str, str]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for component in THESIS_COMPONENTS:
        node = thesis.get(component)
        if component in {"key_premises", "scenarios"}:
            for idx, item in enumerate(node or []):
                if not isinstance(item, dict):
                    continue
                label = item.get("scenario_id") or item.get("premise") or f"{component}[{idx}]"
                refs[f"{component}:{label}"] = {cited[e] for e in _ids_in(item) if e in cited}
        elif node is not None:
            refs[component] = {cited[e] for e in _ids_in(node) if e in cited}
    return refs


def assess_dependency_impact(prior: dict[str, Any] | None, ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify each prior dependency against the current run's evidence ledger."""
    if not prior:
        return {"has_prior": False, "refresh_status": "initial", "dependencies": {}, "affected": [], "unaffected": []}
    snapshot = prior.get("evidence_snapshot") or {}
    by_ref = {str(item.get("source_ref")): item for item in ledger if isinstance(item, dict)}
    current_restated = {
        (str((item.get("provenance") or {}).get("concept")), str((item.get("provenance") or {}).get("period_end")))
        for item in ledger
        if (item.get("provenance") or {}).get("restated")
    }
    dependencies: dict[str, str] = {}
    for ref, before in snapshot.items():
        item = by_ref.get(ref)
        key = (str(before.get("concept")), str(before.get("period_end")))
        if before.get("concept") and key in current_restated:
            dependencies[ref] = "restated"
        elif item is None:
            dependencies[ref] = "not_refreshed"
        elif evidence_fingerprint(item) != before.get("fingerprint"):
            dependencies[ref] = "changed"
        else:
            dependencies[ref] = "unchanged"

    moved = {ref for ref, status in dependencies.items() if status in {"changed", "restated"}}
    unverified = {ref for ref, status in dependencies.items() if status == "not_refreshed"}
    components = _component_refs(prior.get("thesis") or {}, prior.get("cited_sources") or {})
    affected = sorted(c for c, refs in components.items() if refs & moved)
    unverified_components = sorted(c for c, refs in components.items() if refs & unverified and c not in affected)
    unaffected = sorted(c for c in components if c not in affected and c not in unverified_components)

    if not ledger:
        refresh = "refresh_failed"
    elif snapshot and len(unverified) > len(snapshot) / 2:
        refresh = "partial_refresh"
    else:
        refresh = "ok"
    return {
        "has_prior": True,
        "prior_version": prior.get("version"),
        "prior_as_of": prior.get("as_of"),
        "refresh_status": refresh,
        "dependencies": dependencies,
        "affected": affected,
        "unverified": unverified_components,
        "unaffected": unaffected,
        "new_source_refs": sorted(set(by_ref) - set(snapshot)),
    }


def _scenario_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    old = {str(s.get("scenario_id")): s for s in before if isinstance(s, dict)}
    new = {str(s.get("scenario_id")): s for s in after if isinstance(s, dict)}
    changed = {}
    for sid in sorted(set(old) & set(new)):
        fields = [
            f for f in ("kind", "event_outcome", "price_reaction", "probability", "resolves_by", "invalidated_if")
            if json.dumps(old[sid].get(f), sort_keys=True) != json.dumps(new[sid].get(f), sort_keys=True)
        ]
        if fields:
            changed[sid] = fields
    return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)), "changed": changed}


def build_change_record(
    prior: dict[str, Any] | None,
    thesis: dict[str, Any],
    *,
    impact: dict[str, Any],
    levels: dict[str, Any],
    cited: dict[str, str],
) -> dict[str, Any]:
    """Concise record of what changed since the prior version and why the rest still holds."""
    if not prior:
        return {"verdict": "initial", "summary": "First thesis version for this ticker."}
    before = prior.get("thesis") or {}
    old_decision = before.get("conditional_decision") or {}
    new_decision = thesis.get("conditional_decision") or {}
    action_changed = (
        str(old_decision.get("action")) != str(new_decision.get("action"))
        or str(old_decision.get("execution_mode")) != str(new_decision.get("execution_mode"))
    )
    old_premises = {_norm(p.get("premise")): p for p in before.get("key_premises") or [] if isinstance(p, dict)}
    new_premises = {_norm(p.get("premise")): p for p in thesis.get("key_premises") or [] if isinstance(p, dict)}
    scenarios = _scenario_changes(before.get("scenarios") or [], thesis.get("scenarios") or [])
    old_refs, new_refs = set((prior.get("cited_sources") or {}).values()), set(cited.values())
    prior_levels = prior.get("levels") or {}
    level_changes = {
        name: {"before": prior_levels.get(name), "after": levels.get(name)}
        for name in ("target_price", "invalidation_price", "reward_risk")
        if prior_levels.get(name) != levels.get(name)
    }
    old_catalyst, new_catalyst = before.get("catalyst") or {}, thesis.get("catalyst") or {}
    catalyst_changed = {
        f: {"before": old_catalyst.get(f), "after": new_catalyst.get(f)}
        for f in ("event", "expected_window", "within_horizon")
        if old_catalyst.get(f) != new_catalyst.get(f)
    }
    moved_refs = sorted(ref for ref, s in (impact.get("dependencies") or {}).items() if s in {"changed", "restated"})
    supported = [
        p["premise"] for key, p in new_premises.items()
        if key in old_premises and not ({cited.get(e) for e in p.get("evidence_ids") or []} & set(moved_refs))
    ]
    changes_present = bool(
        action_changed
        or set(new_premises) ^ set(old_premises)
        or any(scenarios.values())
        or level_changes
        or catalyst_changed
        or (before.get("expectations") or {}) != (thesis.get("expectations") or {})
    )
    if impact.get("refresh_status") == "refresh_failed":
        verdict = "refresh_failed"
    elif changes_present:
        verdict = "revised"
    else:
        verdict = "unchanged"
    return {
        "verdict": verdict,
        "prior_version": prior.get("version"),
        "prior_as_of": prior.get("as_of"),
        "refresh_status": impact.get("refresh_status"),
        "action_changed": action_changed,
        "decision": {"before": old_decision, "after": new_decision},
        "premises": {
            "added": [new_premises[k]["premise"] for k in sorted(set(new_premises) - set(old_premises))],
            "withdrawn": [old_premises[k]["premise"] for k in sorted(set(old_premises) - set(new_premises))],
            "retained_and_supported": supported,
        },
        "scenarios": scenarios,
        "catalyst": catalyst_changed,
        "levels": level_changes,
        "evidence": {
            "newly_cited": sorted(new_refs - old_refs),
            "no_longer_cited": sorted(old_refs - new_refs),
            "changed_or_restated": moved_refs,
        },
        "affected_components": impact.get("affected") or [],
        "unverified_components": impact.get("unverified") or [],
    }
