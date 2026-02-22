"""
Smart Plan Evaluator — the orchestrator for Tier 1 + Tier 2 evaluation.

Drop-in enhancement for the original `evaluate_decision_plan()`. Combines:
  - Rule-based pre-filter (Tier 0, existing logic, every tick)
  - Stateful condition tracking (Tier 1, consecutive closes, time stops)
  - Thesis state machine (Tier 1, phase-aware evaluation)
  - LLM evaluation (Tier 2, near-trigger situations only)

Usage in PositionMonitor:

    evaluator = SmartPlanEvaluator(
        state_db_path="./journal/condition_state.db",
        llm_client=AnthropicLLMClient(),     # or None to disable Tier 2
    )

    result = evaluator.evaluate(
        thesis=thesis,
        snapshot=snapshot,
        market_session="market_hours",
        volume_ratio=1.2,
    )

    if result and result["matched"]:
        # result["action_template"] is an executable order
        ...
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from tradingagents.agents.journal.evaluation.condition_tracker import (
    ConditionStateStore,
    ConditionTracker,
)
from tradingagents.agents.journal.evaluation.thesis_state_machine import (
    ThesisPhase,
    ThesisStateMachine,
)
from tradingagents.agents.journal.evaluation.llm_evaluator import (
    BranchProximity,
    LLMClient,
    LLMEvalResult,
    LLMEvaluator,
    SkipUntilCache,
    ThesisDigestCache,
    score_branch_proximity,
)
from tradingagents.agents.journal.evaluation.event_compiler import (
    CheckerSpec,
    EventCompiler,
    execute_checker_spec,
)
from tradingagents.agents.journal.core.models import PositionSnapshot, TradeThesis

logger = logging.getLogger(__name__)

# Proximity threshold for promoting to NEAR_TRIGGER / invoking LLM
DEFAULT_PROXIMITY_THRESHOLD = 0.60

# Proximity above which a clean rule-based execution is allowed (no LLM needed)
CLEAN_TRIGGER_THRESHOLD = 0.95


class SmartPlanEvaluator:
    """
    Orchestrates the full evaluation pipeline:

    1. Load/init per-thesis condition tracker and state machine
    2. Ingest current tick into tracker
    3. Score proximity for all branches
    4. Auto-advance state machine phase
    5. If WATCHING and no branch close → return None (no cost)
    6. If branch cleanly triggers (all rules pass) → return action (no LLM)
    7. If NEAR_TRIGGER → invoke LLM evaluator → return structured result
    """

    def __init__(
        self,
        state_db_path: str | Path = "./journal/condition_state.db",
        llm_client: Optional[LLMClient] = None,
        proximity_threshold: float = DEFAULT_PROXIMITY_THRESHOLD,
    ):
        Path(state_db_path).parent.mkdir(parents=True, exist_ok=True)
        self._state_store = ConditionStateStore(state_db_path)
        self._digest_cache = ThesisDigestCache()
        self._skip_cache = SkipUntilCache()

        self._llm_evaluator: Optional[LLMEvaluator] = None
        if llm_client:
            self._llm_evaluator = LLMEvaluator(
                llm_client=llm_client,
                digest_cache=self._digest_cache,
                skip_cache=self._skip_cache,
            )

        self._proximity_threshold = proximity_threshold

        # Event compiler: translates event_conditions into deterministic
        # checker specs at import time (one LLM call per event, ever)
        self._event_compiler = EventCompiler(llm_client=llm_client)

        # Cache of compiled checker specs per thesis: {thesis_id: {branch_id: [CheckerSpec]}}
        self._compiled_specs: Dict[str, Dict[str, List[CheckerSpec]]] = {}

        # Per-thesis instances (lazy-loaded, cached for session)
        self._trackers: Dict[str, ConditionTracker] = {}
        self._machines: Dict[str, ThesisStateMachine] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        thesis: TradeThesis,
        snapshot: PositionSnapshot,
        market_session: str,
        volume_ratio: Optional[float] = None,
        event_confirmations: Optional[Dict[str, Any]] = None,
        has_position: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluate a thesis against current tick context.

        Returns a normalized trigger payload when action is warranted, else None.
        Compatible with the existing evaluate_decision_plan() return format.
        """
        # ── Parse plan ───────────────────────────────────────────
        plan = self._load_plan(thesis)
        if plan is None:
            return None

        execution_plan = plan.get("execution_plan") or []
        if not isinstance(execution_plan, list) or not execution_plan:
            return None

        thesis_id = str(getattr(thesis, "id", "") or thesis.ticker)
        price = _safe_float(snapshot.current_price)
        trade_day = self._extract_trade_day(snapshot.timestamp)
        session_norm = _normalize_session(market_session)

        # ── Step 1: Ingest tick into condition tracker ───────────
        tracker = self._get_tracker(thesis_id)
        tracker.ingest_tick(
            price=price,
            volume_ratio=volume_ratio,
            market_session=session_norm,
            timestamp=snapshot.timestamp,
        )

        # ── Step 2: Score proximity for all branches ─────────────
        proximities: List[BranchProximity] = []
        for branch in execution_plan:
            if not isinstance(branch, dict):
                continue
            bp = score_branch_proximity(
                branch,
                price=price,
                volume_ratio=volume_ratio,
                trade_day=trade_day,
                market_session=session_norm,
                tracker_state=tracker.state,
            )
            proximities.append(bp)

        max_proximity = max((bp.score for bp in proximities), default=0.0)

        # ── Step 3: Check stateful conditions (Tier 1) ───────────
        event_results = self._evaluate_stateful_events(
            branches=execution_plan,
            tracker=tracker,
            confirmations=event_confirmations or {},
            thesis_id=thesis_id,
            plan=plan,
        )

        # ── Step 4: Auto-advance state machine ───────────────────
        in_window = self._is_in_schedule_window(execution_plan, trade_day)
        machine = self._get_machine(thesis_id)
        machine.auto_advance(
            in_schedule_window=in_window,
            has_position=has_position,
            max_proximity=max_proximity,
            proximity_threshold=self._proximity_threshold,
        )

        if not machine.should_evaluate:
            return None

        # ── Step 5: Try clean rule-based triggers ────────────────
        # If a branch has proximity >= CLEAN_TRIGGER_THRESHOLD and all
        # stateful conditions also pass, execute without LLM.
        for bp in sorted(proximities, key=lambda x: x.score, reverse=True):
            if bp.score < CLEAN_TRIGGER_THRESHOLD:
                break

            branch_id = bp.branch_id
            if not event_results.get(branch_id, True):
                continue  # stateful event check failed

            branch = bp.branch_data
            template = branch.get("action_template")
            if not isinstance(template, dict):
                continue

            # Clean trigger — execute directly
            machine.transition(ThesisPhase.TRIGGERED, f"clean_trigger:{branch_id}")
            logger.info("Clean rule trigger: thesis=%s branch=%s", thesis_id, branch_id)

            return {
                "matched": True,
                "branch_id": branch_id,
                "action_template": template,
                "reason_code": "rule_trigger",
                "proximity": bp.score,
                "evaluation_method": "rule_based",
                "phase": machine.phase.value,
                "summary": {
                    "plan_mode": plan.get("plan_mode"),
                    "matched_branch_id": branch_id,
                    "market_session": session_norm,
                    "proximity_score": bp.score,
                },
            }

        # ── Step 6: LLM evaluation for near-trigger situations ───
        near_branches = [
            bp for bp in proximities
            if bp.score >= self._proximity_threshold
        ]

        if near_branches and self._llm_evaluator and machine.should_use_llm:
            llm_result = self._invoke_llm(
                thesis=thesis,
                plan=plan,
                thesis_id=thesis_id,
                near_branches=near_branches,
                tracker=tracker,
                price=price,
                volume_ratio=volume_ratio,
                market_session=session_norm,
                trade_day=trade_day,
            )

            if llm_result and llm_result.is_executable:
                machine.transition(
                    ThesisPhase.TRIGGERED,
                    f"llm_trigger:{llm_result.branch_id}",
                )
                return {
                    "matched": True,
                    "branch_id": llm_result.branch_id or near_branches[0].branch_id,
                    "action_template": llm_result.order,
                    "reason_code": "llm_trigger",
                    "evaluation_method": "llm",
                    "llm_reasoning": llm_result.reasoning,
                    "llm_confidence": llm_result.confidence,
                    "phase": machine.phase.value,
                    "summary": {
                        "plan_mode": plan.get("plan_mode"),
                        "matched_branch_id": llm_result.branch_id,
                        "market_session": session_norm,
                        "llm_action": llm_result.action,
                    },
                }

            if llm_result and llm_result.action == "CLOSE":
                # LLM recommends closing — time stop or invalidation
                machine.transition(ThesisPhase.CLOSED, f"llm_close:{llm_result.reasoning[:80]}")
                # Return a close signal using a synthetic template
                return {
                    "matched": True,
                    "branch_id": "__time_stop__",
                    "action_template": {
                        "action": "CLOSE",
                        "order_type": "MARKET",
                        "rationale": llm_result.reasoning,
                    },
                    "reason_code": "llm_close",
                    "evaluation_method": "llm",
                    "llm_reasoning": llm_result.reasoning,
                    "phase": machine.phase.value,
                    "summary": {
                        "plan_mode": plan.get("plan_mode"),
                        "market_session": session_norm,
                        "llm_action": "CLOSE",
                    },
                }

        return None

    def get_thesis_status(self, thesis_id: str) -> Dict[str, Any]:
        """Get full status for a thesis (for CLI dashboard)."""
        tracker = self._get_tracker(thesis_id)
        machine = self._get_machine(thesis_id)

        return {
            "thesis_id": thesis_id,
            "phase": machine.phase.value,
            "phase_history": machine.get_history(),
            "days_active": tracker.days_active(),
            "tick_count": tracker.state.get("tick_count", 0),
            "last_price": tracker.state.get("last_price"),
            "price_high": tracker.state.get("price_high"),
            "price_low": tracker.state.get("price_low"),
            "eod_series": tracker.get_eod_series(5),
            "llm_calls": self._llm_evaluator.call_count if self._llm_evaluator else 0,
        }

    def reset_thesis(self, thesis_id: str):
        """Reset all state for a thesis (useful for re-import)."""
        self._state_store.save(thesis_id, {})
        self._trackers.pop(thesis_id, None)
        self._machines.pop(thesis_id, None)
        self._compiled_specs.pop(thesis_id, None)
        self._digest_cache.invalidate(thesis_id)
        if self._llm_evaluator:
            self._llm_evaluator.clear_skip(thesis_id)

    def compile_on_import(self, thesis: TradeThesis) -> Dict[str, List[Dict[str, Any]]]:
        """
        Pre-compile all event_conditions for a thesis at import time.

        Call this from report_import after saving the thesis.
        Returns the compiled specs as serializable dicts (for logging/storage).
        """
        plan = self._load_plan(thesis)
        if plan is None:
            return {}
        thesis_id = str(getattr(thesis, "id", "") or thesis.ticker)
        raw = self._event_compiler.compile_plan(plan)
        specs: Dict[str, List[CheckerSpec]] = {}
        serialized: Dict[str, List[Dict[str, Any]]] = {}
        for branch_id, spec_dicts in raw.items():
            checker_specs = [CheckerSpec.from_dict(d) for d in spec_dicts]
            specs[branch_id] = checker_specs
            serialized[branch_id] = spec_dicts
        self._compiled_specs[thesis_id] = specs
        return serialized

    # ------------------------------------------------------------------
    # Tier 1: Compiled event condition evaluation
    # ------------------------------------------------------------------

    def _evaluate_stateful_events(
        self,
        branches: List[Dict[str, Any]],
        tracker: ConditionTracker,
        confirmations: Dict[str, Any],
        thesis_id: str,
        plan: Dict[str, Any],
    ) -> Dict[str, bool]:
        """
        Evaluate event_conditions using pre-compiled CheckerSpecs.

        On first call for a thesis, compiles all events (pattern match + LLM).
        On subsequent calls, executes the cached specs deterministically.
        """
        # Compile on first encounter
        if thesis_id not in self._compiled_specs:
            self._compiled_specs[thesis_id] = self._compile_thesis_events(plan)

        compiled = self._compiled_specs.get(thesis_id, {})
        results: Dict[str, bool] = {}

        for branch in branches:
            if not isinstance(branch, dict):
                continue
            branch_id = str(branch.get("branch_id") or "")
            specs = compiled.get(branch_id, [])

            events = branch.get("event_conditions") or []
            conditions = branch.get("conditions") or {}
            events = events or conditions.get("event_conditions") or []

            if not events:
                results[branch_id] = True
                continue

            if not specs:
                # No compiled specs — fall back to external confirmations
                results[branch_id] = self._check_external_confirmations(
                    events, confirmations
                )
                continue

            # Execute all compiled specs (AND logic)
            all_met = True
            for spec in specs:
                if not execute_checker_spec(spec, tracker):
                    all_met = False
                    break

            results[branch_id] = all_met

        return results

    def _compile_thesis_events(
        self, plan: Dict[str, Any]
    ) -> Dict[str, List[CheckerSpec]]:
        """
        Compile all event_conditions in a plan.
        Called once per thesis, at first evaluation.
        """
        raw = self._event_compiler.compile_plan(plan)
        result: Dict[str, List[CheckerSpec]] = {}
        for branch_id, spec_dicts in raw.items():
            result[branch_id] = [CheckerSpec.from_dict(d) for d in spec_dicts]
        return result

    @staticmethod
    def _check_external_confirmations(
        events: List[Dict[str, Any]],
        confirmations: Dict[str, Any],
    ) -> bool:
        """Fallback: check event_conditions against external confirmations dict."""
        for event in events:
            if not isinstance(event, dict):
                return False
            key = str(event.get("event_key") or "").strip()
            if not key:
                return False
            requires = bool(event.get("requires_confirmation", True))
            expected = event.get("expected_value")
            actual = confirmations.get(key)
            if requires and actual is None:
                return False
            if expected is not None:
                if actual is None:
                    return False
                if str(actual).strip().lower() != str(expected).strip().lower():
                    return False
        return True

    # ------------------------------------------------------------------
    # Tier 2: LLM invocation
    # ------------------------------------------------------------------

    def _invoke_llm(
        self,
        *,
        thesis: TradeThesis,
        plan: Dict[str, Any],
        thesis_id: str,
        near_branches: List[BranchProximity],
        tracker: ConditionTracker,
        price: Optional[float],
        volume_ratio: Optional[float],
        market_session: str,
        trade_day: Optional[str],
    ) -> Optional[LLMEvalResult]:
        """Delegate to the LLM evaluator with full context."""
        if not self._llm_evaluator:
            return None

        decision_text = getattr(thesis, "final_decision_text", None)
        plan_json = plan

        return self._llm_evaluator.evaluate(
            thesis_id=thesis_id,
            decision_text=decision_text,
            plan_json=plan_json,
            near_branches=near_branches,
            price=price,
            volume_ratio=volume_ratio,
            market_session=market_session,
            trade_day=trade_day,
            days_active=tracker.days_active(),
            eod_series=tracker.get_eod_series(5),
            tracker_state=tracker.state,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_tracker(self, thesis_id: str) -> ConditionTracker:
        if thesis_id not in self._trackers:
            self._trackers[thesis_id] = ConditionTracker(thesis_id, self._state_store)
        return self._trackers[thesis_id]

    def _get_machine(self, thesis_id: str) -> ThesisStateMachine:
        if thesis_id not in self._machines:
            self._machines[thesis_id] = ThesisStateMachine(
                thesis_id, self._state_store, initial_phase=ThesisPhase.PENDING
            )
        return self._machines[thesis_id]

    @staticmethod
    def _load_plan(thesis: TradeThesis) -> Optional[Dict[str, Any]]:
        raw = getattr(thesis, "decision_plan_json", None)
        if not raw:
            return None
        try:
            plan = json.loads(str(raw))
        except Exception:
            return None
        if str(plan.get("decision_version", "")).lower() != "v2":
            return None
        return plan

    @staticmethod
    def _is_in_schedule_window(branches: List[Dict[str, Any]], trade_day: Optional[str]) -> bool:
        """True if today falls within any branch's valid_from/valid_to window."""
        if not trade_day:
            return True  # can't determine, assume yes
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            schedule = (branch.get("conditions") or {}).get("schedule") or {}
            valid_from = str(schedule.get("valid_from") or "").strip()
            valid_to = str(schedule.get("valid_to") or "").strip()
            if valid_from and trade_day < valid_from:
                continue
            if valid_to and trade_day > valid_to:
                continue
            return True
        return False

    @staticmethod
    def _extract_trade_day(ts: Optional[str]) -> Optional[str]:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return dt.date().isoformat()
        except Exception:
            return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_session(value: Any) -> str:
    s = str(value or "any").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"premarket", "market_hours", "afterhours", "overnight", "weekend", "any"}:
        return s
    return "any"
