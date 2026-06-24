from __future__ import annotations

from typing import Any, TypedDict
import json

from pydantic import ValidationError

from verumtrade.graph.debate_schema import ALLOWED_DECISION_FIELDS
from verumtrade.graph.structured_schemas import PlanPatch


class PatchValidationResult(TypedDict, total=False):
    patch_id: str
    valid: bool
    reason: str
    patch: dict[str, Any]


def validate_plan_patches(
    patches: list[dict[str, Any]] | None,
    *,
    trader_plan: dict[str, Any] | None,
    evidence_ids: list[str] | set[str],
    target_plan_version: str = "trader_plan_v1",
) -> list[PatchValidationResult]:
    valid_evidence = {str(item) for item in evidence_ids}
    plan = trader_plan if isinstance(trader_plan, dict) else {}
    results: list[PatchValidationResult] = []

    for raw in patches or []:
        patch = dict(raw or {})
        patch_id = str(patch.get("patch_id") or "").strip()
        reason = _patch_rejection_reason(
            patch,
            plan=plan,
            valid_evidence=valid_evidence,
            target_plan_version=target_plan_version,
        )
        results.append(
            {
                "patch_id": patch_id,
                "valid": not bool(reason),
                "reason": reason,
                "patch": patch,
            }
        )
    return results


def apply_valid_plan_patches(
    trader_plan: dict[str, Any] | None,
    validation_results: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    patched = dict(trader_plan or {})
    for result in validation_results or []:
        if not isinstance(result, dict) or not result.get("valid"):
            continue
        patch = result.get("patch")
        if not isinstance(patch, dict):
            continue
        field = str(patch.get("field") or "").strip()
        if field not in ALLOWED_DECISION_FIELDS:
            continue
        patch_type = str(patch.get("patch_type") or "").strip()
        if patch_type == "remove":
            patched.pop(field, None)
        else:
            patched[field] = patch.get("new_value")
    return patched


def extract_plan_patches_from_text(text: Any) -> list[dict[str, Any]]:
    content = str(text or "")
    patches: list[dict[str, Any]] = []
    for obj in _json_objects(content):
        if str(obj.get("patch_id") or "").strip() and str(obj.get("field") or "").strip():
            patches.append(obj)
    return patches


def _patch_rejection_reason(
    patch: dict[str, Any],
    *,
    plan: dict[str, Any],
    valid_evidence: set[str],
    target_plan_version: str,
) -> str:
    if str(patch.get("target_plan_version") or "").strip() != target_plan_version:
        return "patch targets stale plan version"
    if str(patch.get("patch_type") or "").strip() not in {"modify", "add", "remove"}:
        return "invalid patch_type"
    field = str(patch.get("field") or "").strip()
    if field not in ALLOWED_DECISION_FIELDS:
        return "invalid patch field"
    evidence_ids = [str(item) for item in patch.get("evidence_ids") or [] if str(item)]
    if not evidence_ids:
        return "missing evidence_ids"
    missing = [item for item in evidence_ids if item not in valid_evidence]
    if missing:
        return f"unknown evidence_ids: {', '.join(missing)}"
    if patch.get("old_value") == patch.get("new_value"):
        return "patch does not change executable field"
    if field in plan and patch.get("old_value") != plan.get(field):
        return "old_value does not match target plan"
    if not str(patch.get("reason") or "").strip():
        return "missing reason"
    schema_reason = _schema_validation_reason(patch)
    if schema_reason:
        return schema_reason
    return ""


def _schema_validation_reason(patch: dict[str, Any]) -> str:
    try:
        PlanPatch.model_validate(patch)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        msg = str(first.get("msg") or "invalid contract schema")
        return f"schema validation failed: {loc} {msg}".strip()
    return ""


def _json_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    starts = [idx for idx, char in enumerate(text) if char == "{"]
    for start in starts:
        depth = 0
        for end, char in enumerate(text[start:], start=start):
            if char == "{":
                depth += 1
            elif char == "}":
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
