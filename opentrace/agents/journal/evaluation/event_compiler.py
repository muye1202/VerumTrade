"""
Event Condition Compiler — LLM-powered translation at import time.

Instead of:
  - Pattern-matching event keys at runtime (_resolve_event_from_tracker)
  - Or calling the LLM on every tick

We call the LLM ONCE when a thesis is imported, to compile each
event_condition into a deterministic checker spec that the condition
tracker can evaluate on every tick with zero API cost.

Flow:
  1. Thesis imported via report_import
  2. EventCompiler.compile() called on each event_condition
  3. LLM reads the event_key + surrounding branch context
  4. LLM outputs a CheckerSpec JSON (which tracker method to call, with what params)
  5. CheckerSpec is stored in the thesis alongside the original event
  6. At runtime, ConditionTracker evaluates the CheckerSpec deterministically

This is "teach a man to fish" vs "fish for him every 15 minutes."
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


# ── Checker spec: the compiled, deterministic representation ─────────


@dataclass
class CheckerSpec:
    """
    A deterministic condition check that the tracker can execute.

    method:  which ConditionTracker method to call
    params:  keyword arguments for that method
    negate:  if True, the check passes when the method returns False
    """

    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    negate: bool = False
    source_event_key: str = ""
    compiled_by: str = "llm"  # "llm" | "pattern" | "manual"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "params": self.params,
            "negate": self.negate,
            "source_event_key": self.source_event_key,
            "compiled_by": self.compiled_by,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CheckerSpec":
        return cls(
            method=d.get("method", ""),
            params=d.get("params", {}),
            negate=d.get("negate", False),
            source_event_key=d.get("source_event_key", ""),
            compiled_by=d.get("compiled_by", "llm"),
        )


# ── Supported tracker methods (the "instruction set") ───────────────


SUPPORTED_METHODS = {
    "check_consecutive_closes": {
        "description": "Check if the last N daily closes satisfy a price threshold with optional volume filter",
        "params": {
            "direction": "str: 'above' or 'below'",
            "threshold": "float: price level",
            "required": "int: number of consecutive closes needed (default 2)",
            "volume_ratio_max": "float or null: max volume ratio for each close",
        },
        "returns": "bool",
    },
    "check_time_stop": {
        "description": "Check if position has been active for too many trading days",
        "params": {
            "max_trading_days": "int: maximum days before forced exit",
            "entry_date": "str or null: YYYY-MM-DD, uses first observed day if null",
        },
        "returns": "bool (True = time stop breached)",
    },
    "check_volume_in_range": {
        "description": "Check if current volume ratio is within bounds",
        "params": {
            "ratio_min": "float or null: minimum volume ratio",
            "ratio_max": "float or null: maximum volume ratio",
        },
        "returns": "bool",
    },
    "days_active": {
        "description": "Get number of trading days with at least one observed tick",
        "params": {},
        "returns": "int",
    },
}


# ── LLM client protocol (same as llm_evaluator.py) ──────────────────


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str: ...


# ── System prompt for compilation (static, cacheable) ────────────────


_COMPILE_SYSTEM = """You compile natural-language trade event conditions into deterministic checker specifications.

Given an event_condition from a trade decision (with its surrounding branch context), output a JSON CheckerSpec that a condition tracker can evaluate on every market tick without any LLM involvement.

AVAILABLE METHODS:
{methods}

OUTPUT FORMAT — respond with ONLY this JSON, no other text:
{{
  "method": "method_name",
  "params": {{"param1": value1, "param2": value2}},
  "negate": false
}}

RULES:
- Pick the single method that best captures the event's intent
- Extract numeric values from the event key AND from the branch context (conditions, action_template)
- If the event describes a condition that should NOT be true, set "negate": true
- If no method can represent this event, respond with: {{"method": "unsupported", "params": {{}}, "negate": false}}
- Be precise with threshold values — use the exact numbers from the branch context
"""


# ── Event Compiler ───────────────────────────────────────────────────


class EventCompiler:
    """
    Compiles event_conditions into CheckerSpecs, using:
    1. Fast pattern matching (no LLM, covers common patterns)
    2. LLM compilation (one-shot, for anything patterns miss)

    Results are cached and stored with the thesis so compilation
    happens exactly once per event, ever.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self._llm = llm_client
        self._compile_count = 0

    @property
    def compile_count(self) -> int:
        return self._compile_count

    def compile_branch_events(
        self,
        branch: Dict[str, Any],
    ) -> List[CheckerSpec]:
        """
        Compile all event_conditions in a branch.
        Returns a list of CheckerSpecs, one per event.
        """
        events = branch.get("event_conditions") or []
        conditions = branch.get("conditions") or {}
        events = events or conditions.get("event_conditions") or []

        if not events or not isinstance(events, list):
            return []

        specs: List[CheckerSpec] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            spec = self._compile_single(event, branch)
            if spec:
                specs.append(spec)

        return specs

    def compile_plan(self, plan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Compile all event_conditions across all branches in a plan.

        Returns {branch_id: [checker_spec_dict, ...]} suitable for
        storing as JSON alongside the thesis.
        """
        execution_plan = plan.get("execution_plan") or []
        result: Dict[str, List[Dict[str, Any]]] = {}

        for branch in execution_plan:
            if not isinstance(branch, dict):
                continue
            branch_id = str(branch.get("branch_id") or "")
            specs = self.compile_branch_events(branch)
            if specs:
                result[branch_id] = [s.to_dict() for s in specs]

        return result

    def _compile_single(
        self,
        event: Dict[str, Any],
        branch: Dict[str, Any],
    ) -> Optional[CheckerSpec]:
        """Compile a single event_condition, pattern-match first, LLM fallback."""
        event_key = str(event.get("event_key") or "").strip()
        if not event_key:
            return None

        # ── Try pattern matching first (free, instant) ───────────
        spec = self._pattern_match(event_key, branch)
        if spec:
            spec.source_event_key = event_key
            spec.compiled_by = "pattern"
            return spec

        # ── LLM compilation (one-shot) ───────────────────────────
        if self._llm:
            spec = self._llm_compile(event, branch)
            if spec:
                spec.source_event_key = event_key
                spec.compiled_by = "llm"
                self._compile_count += 1
                return spec

        logger.warning(
            "Could not compile event '%s' — will require manual confirmation",
            event_key,
        )
        return None

    # ── Pattern matching (covers ~60-70% of cases) ───────────────

    def _pattern_match(
        self,
        event_key: str,
        branch: Dict[str, Any],
    ) -> Optional[CheckerSpec]:
        """
        Fast, deterministic pattern matching for common event patterns.
        """
        key = event_key.lower().replace("-", "_").replace(" ", "_")
        conditions = branch.get("conditions") or {}
        price_cond = conditions.get("price") or {}
        vol_cond = conditions.get("volume") or {}

        # ── Consecutive closes pattern ───────────────────────────
        if "consecutive" in key and "close" in key:
            direction = "above" if "above" in key else "below"
            threshold = _extract_price(key)
            if threshold is None:
                threshold = _sf(
                    price_cond.get("close_above") if direction == "above"
                    else price_cond.get("close_below")
                )
            if threshold is None:
                return None

            count = _extract_count(key) or 2
            vol_max = _sf(vol_cond.get("volume_ratio_max"))

            return CheckerSpec(
                method="check_consecutive_closes",
                params={
                    "direction": direction,
                    "threshold": threshold,
                    "required": count,
                    "volume_ratio_max": vol_max,
                },
            )

        # ── Time stop pattern ────────────────────────────────────
        if "time_stop" in key or "max_days" in key or "trading_days" in key:
            days = _extract_count(key) or 8
            return CheckerSpec(
                method="check_time_stop",
                params={"max_trading_days": days},
            )

        # ── Volume pattern ───────────────────────────────────────
        if "declining_volume" in key or "low_volume" in key or "fading_volume" in key:
            vol_max = _sf(vol_cond.get("volume_ratio_max")) or 0.8
            return CheckerSpec(
                method="check_volume_in_range",
                params={"ratio_max": vol_max},
            )

        if "elevated_volume" in key or "high_volume" in key or "surge" in key:
            vol_min = _sf(vol_cond.get("volume_ratio_min")) or 1.5
            return CheckerSpec(
                method="check_volume_in_range",
                params={"ratio_min": vol_min},
            )

        return None

    # ── LLM compilation ──────────────────────────────────────────

    def _llm_compile(
        self,
        event: Dict[str, Any],
        branch: Dict[str, Any],
    ) -> Optional[CheckerSpec]:
        """Use LLM to compile an event condition into a CheckerSpec."""
        methods_desc = json.dumps(SUPPORTED_METHODS, indent=2)
        system = _COMPILE_SYSTEM.format(methods=methods_desc)

        # Build compact context (~150 tokens)
        branch_context = {
            "branch_id": branch.get("branch_id"),
            "conditions": branch.get("conditions"),
            "action_template_summary": {
                k: v
                for k, v in (branch.get("action_template") or {}).items()
                if k in ("action", "stop_loss", "take_profit", "time_horizon", "rationale")
            },
        }

        user_prompt = (
            f"EVENT_CONDITION:\n{json.dumps(event, indent=2)}\n\n"
            f"BRANCH_CONTEXT:\n{json.dumps(branch_context, indent=2, default=str)}\n\n"
            f"→ CheckerSpec JSON:"
        )

        try:
            raw = self._llm.complete(system=system, user=user_prompt, max_tokens=200)
        except Exception as e:
            logger.error("LLM compilation failed: %s", e)
            return None

        return self._parse_checker_spec(raw)

    @staticmethod
    def _parse_checker_spec(raw: str) -> Optional[CheckerSpec]:
        """Parse LLM response into a CheckerSpec."""
        import re

        text = raw.strip()

        # Try direct parse
        parsed = _try_json(text)

        # Try code block extraction
        if parsed is None:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                parsed = _try_json(match.group(1))

        # Try first JSON block
        if parsed is None:
            match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if match:
                parsed = _try_json(match.group(0))

        if not isinstance(parsed, dict):
            return None

        method = str(parsed.get("method") or "").strip()
        if not method or method == "unsupported":
            return None

        if method not in SUPPORTED_METHODS:
            logger.warning("LLM produced unsupported method: %s", method)
            return None

        return CheckerSpec(
            method=method,
            params=parsed.get("params") or {},
            negate=bool(parsed.get("negate", False)),
        )


# ── Checker executor (runs compiled specs against tracker) ───────────


def execute_checker_spec(
    spec: CheckerSpec,
    tracker: Any,  # ConditionTracker instance
    **runtime_kwargs,
) -> bool:
    """
    Execute a compiled CheckerSpec against a ConditionTracker.

    Returns True if the condition is met (respecting negate flag).
    """
    method_name = spec.method
    fn = getattr(tracker, method_name, None)
    if fn is None:
        logger.warning("Tracker has no method '%s'", method_name)
        return False

    try:
        params = dict(spec.params)
        # Merge any runtime overrides (e.g., current_ratio for volume checks)
        params.update(runtime_kwargs)

        result = fn(**params)

        # Handle different return types
        if isinstance(result, bool):
            return (not result) if spec.negate else result
        if isinstance(result, (int, float)):
            # For numeric returns (like days_active), treat >0 as True
            val = bool(result > 0)
            return (not val) if spec.negate else val

        return False
    except Exception as e:
        logger.error("CheckerSpec execution failed (%s): %s", method_name, e)
        return False


# ── Helpers ──────────────────────────────────────────────────────────


def _extract_price(key: str) -> Optional[float]:
    import re
    for m in re.findall(r"(\d+\.?\d*)", key):
        v = float(m)
        if v > 10:
            return v
    return None


def _extract_count(key: str) -> Optional[int]:
    import re
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10}
    for w, n in words.items():
        if w in key.lower():
            return n
    match = re.match(r"^(\d+)_", key)
    if match:
        return int(match.group(1))
    return None


def _sf(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _try_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
