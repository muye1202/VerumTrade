from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


EvidencePolarity = Literal["bullish", "bearish", "neutral", "mixed"]
DecisionAction = Literal["BUY", "SELL", "HOLD"]
ExecutionMode = Literal["act_now", "wait_for_trigger"]
OrderType = Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"]
PatchType = Literal["modify", "add", "remove"]
PatchMateriality = Literal["low", "medium", "high"]


class EvidenceItem(StrictModel):
    evidence_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    source_agent: str = Field(min_length=1)
    source_tool: str | None = None
    source_ref: str | None = None
    observed_at: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    polarity: EvidencePolarity
    time_horizon: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    materiality: float = Field(ge=0.0, le=1.0)
    supports: list[str]
    contradicts: list[str]
    raw_excerpt: str | None = None
    numeric_values: dict[str, float] = Field(default_factory=dict)


class EvidenceAdmissibilityDecision(StrictModel):
    evidence_id: str
    reason: str


class EvidenceAdmissibilityReport(StrictModel):
    accepted_evidence_ids: list[str]
    downgraded_evidence: list[EvidenceAdmissibilityDecision]
    rejected_evidence: list[EvidenceAdmissibilityDecision]


class PlanImplication(StrictModel):
    field: str
    proposed_value: Any
    rationale: str | None = None


class ContestedIssue(StrictModel):
    issue_id: str
    question: str
    candidate_evidence: list[str]
    decision_fields_at_risk: list[str]


class ResearchDebateTurn(StrictModel):
    turn_id: str
    speaker: str
    issue_id: str
    position: str
    claim: str
    evidence_ids: list[str] = Field(min_length=1)
    rebuttal_to: str | None = None
    plan_implication: PlanImplication
    falsification_condition: str
    confidence: float = Field(ge=0.0, le=1.0)


class ThesisClaim(StrictModel):
    claim_id: str
    claim: str | None = None
    evidence_ids: list[str] = Field(min_length=1)
    effect: str


class RejectedClaim(StrictModel):
    claim_id: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class UnresolvedUncertainty(StrictModel):
    uncertainty: str
    decision_effect: str


class ThesisLedger(StrictModel):
    winning_thesis: str
    accepted_claims: list[ThesisClaim] = Field(min_length=1)
    rejected_claims: list[RejectedClaim] = Field(default_factory=list)
    unresolved_uncertainties: list[UnresolvedUncertainty] = Field(default_factory=list)
    recommended_plan_constraints: dict[str, Any]


class TraderPlan(StrictModel):
    plan_id: Literal["trader_plan_v1"]
    action: DecisionAction
    execution_mode: ExecutionMode
    order_type: OrderType
    position_size_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    entry_condition: str
    stop_loss: float | str
    take_profit: float | str
    rationale_links: dict[str, list[str]]


class PlanPatch(StrictModel):
    patch_id: str
    author: str
    target_plan_version: Literal["trader_plan_v1"]
    patch_type: PatchType
    field: str
    old_value: Any
    new_value: Any
    evidence_ids: list[str] = Field(min_length=1)
    reason: str
    expected_effect: str
    materiality: PatchMateriality


class DecisionDiff(StrictModel):
    from_trader_plan: dict[str, Any]
    to_final_decision: dict[str, Any]


class RejectedPatch(StrictModel):
    patch_id: str
    reason: str


class FinalDecisionTrace(StrictModel):
    final_decision_id: Literal["final_trade_decision"]
    decision_diff: DecisionDiff | None
    accepted_patches: list[str]
    rejected_patches: list[RejectedPatch]
    no_material_change_reason: str | None


def schema_for_structured_output(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    _close_object_schemas(schema)
    return schema


def _close_object_schemas(schema: Any) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            _close_object_schemas(value)
    elif isinstance(schema, list):
        for item in schema:
            _close_object_schemas(item)
