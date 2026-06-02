"""
LLM Evaluator â€” intelligent trigger evaluation for near-trigger situations.

Design goals:
  1. Minimal token usage: thesis digests are cached, tick context is compact
  2. Minimal API calls: only invoked when rule-based pre-filter detects ambiguity
  3. Structured output: LLM returns executable JSON, not prose
  4. Skip-until hints: LLM can say "nothing happening, check back at EOD"

Architecture:
  Rule pre-filter (every tick, ~0 cost)
    â†’ Proximity scoring (every tick, ~0 cost)
    â†’ Skip-until gate (most ticks exit here)
    â†’ LLM evaluation (2-5 calls/day during active monitoring)
    â†’ Policy validation â†’ Executable order
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# â”€â”€ LLM client protocol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class LLMClient(Protocol):
    """
    Minimal interface for an LLM completion call.
    Implement this to plug in Anthropic, OpenAI, or any provider.
    """

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str:
        """Return raw text response from the LLM."""
        ...


# â”€â”€ Default Anthropic implementation (kept for backward compat) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class AnthropicLLMClient:
    """Thin wrapper around the Anthropic Python SDK."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: Optional[str] = None,
    ):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text


# â”€â”€ Config-driven LLM client (mirrors opentrace_graph.py provider logic) â”€â”€â”€


class ConfiguredLLMClient:
    """
    Provider-agnostic LLMClient that uses whichever model/backend
    the user chose in the main CLI (quick_think_llm).

    Accepts the same config dict that OpenTraceGraph receives:
      {
        "llm_provider": "anthropic" | "openai" | "qwen3-cn" | "deepseek"
                        | "openrouter" | "glm" | "google",
        "quick_think_llm": "<model name>",
        "backend_url": "<base url>",
        ...
      }
    """

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self._llm = self._build(config)

    # â”€â”€ LLMClient protocol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        result = self._llm.invoke(messages)
        return str(getattr(result, "content", result) or "")

    # â”€â”€ Provider construction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @staticmethod
    def _build(config: Dict[str, Any]):
        """Build a LangChain chat model from an OpenTrace config dict."""
        provider = str(config.get("llm_provider") or "openai").lower()
        model = str(config.get("quick_think_llm") or "gpt-4o-mini")

        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            import os as _os
            api_key = _os.getenv("ANTHROPIC_API_KEY")
            base_url = _os.getenv("ANTHROPIC_BASE_URL") or config.get("backend_url")
            kwargs: Dict[str, Any] = {"model": model}
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            return ChatAnthropic(**kwargs)

        if provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model)

        # All OpenAI-compatible providers
        from langchain_openai import ChatOpenAI
        import os as _os
        openai_kwargs: Dict[str, Any] = {
            "model": model,
            "base_url": config.get("backend_url"),
        }
        if provider == "qwen3-cn":
            openai_kwargs["api_key"] = _os.getenv("DASHSCOPE_API_KEY")
        elif provider == "deepseek":
            openai_kwargs["api_key"] = _os.getenv("DEEPSEEK_API_KEY")
        elif provider == "openrouter":
            openai_kwargs["api_key"] = _os.getenv("OPENROUTER_API_KEY")
        elif provider == "glm":
            openai_kwargs["api_key"] = (
                _os.getenv("ZHIPUAI_API_KEY")
                or _os.getenv("GLM_API_KEY")
                or _os.getenv("OPENAI_API_KEY")
            )
        # For plain openai / ollama / other OpenAI-compat providers the default key env is used.
        return ChatOpenAI(**openai_kwargs)

    def __repr__(self) -> str:
        provider = self._config.get("llm_provider", "?")
        model = self._config.get("quick_think_llm", "?")
        return f"ConfiguredLLMClient(provider={provider}, model={model})"


def build_llm_client_from_config(config: Optional[Dict[str, Any]]) -> Optional["ConfiguredLLMClient"]:
    """
    Public factory: returns a ConfiguredLLMClient if a valid config dict is
    supplied, else None (disables Tier 2 LLM evaluation).

    Usage in JournalScheduler:
        llm_client = build_llm_client_from_config(agent_config)
    """
    if not config:
        return None
    try:
        return ConfiguredLLMClient(config)
    except Exception as exc:
        logger.warning("Could not build LLM client from config: %s", exc)
        return None


# â”€â”€ Trigger proximity scoring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass
class BranchProximity:
    """How close a single execution_plan branch is to firing."""

    branch_id: str
    score: float  # 0.0 = far, 1.0 = at/past trigger
    breakdown: Dict[str, float] = field(default_factory=dict)
    blocking_reasons: List[str] = field(default_factory=list)
    branch_data: Dict[str, Any] = field(default_factory=dict)


def score_branch_proximity(
    branch: Dict[str, Any],
    *,
    price: Optional[float],
    volume_ratio: Optional[float],
    trade_day: Optional[str],
    market_session: str,
    tracker_state: Dict[str, Any],
) -> BranchProximity:
    """
    Compute a 0â€“1 proximity score for a single branch.

    The score is the minimum across all sub-condition scores (AND logic).
    A sub-condition scores 1.0 when met, 0.0 when far, and intermediate
    values when close.
    """
    branch_id = str(branch.get("branch_id") or "")
    conditions = branch.get("conditions") or {}
    scores: Dict[str, float] = {}
    blockers: List[str] = []

    # â”€â”€ Price conditions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    price_cond = conditions.get("price") or {}
    if price_cond and price is not None:
        close_above = _sf(price_cond.get("close_above"))
        close_below = _sf(price_cond.get("close_below"))

        if close_above is not None:
            if price >= close_above:
                scores["price_above"] = 1.0
            else:
                dist_pct = (close_above - price) / max(close_above, 1e-9) * 100
                scores["price_above"] = max(0.0, 1.0 - dist_pct / 3.0)
                if scores["price_above"] < 0.3:
                    blockers.append(f"price ${price:.2f} far below close_above ${close_above:.2f}")

        if close_below is not None:
            if price <= close_below:
                scores["price_below"] = 1.0
            else:
                dist_pct = (price - close_below) / max(close_below, 1e-9) * 100
                scores["price_below"] = max(0.0, 1.0 - dist_pct / 3.0)
                if scores["price_below"] < 0.3:
                    blockers.append(f"price ${price:.2f} far above close_below ${close_below:.2f}")
    elif price_cond and price is None:
        scores["price"] = 0.0
        blockers.append("no price data")

    # â”€â”€ Volume conditions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    vol_cond = conditions.get("volume") or {}
    if vol_cond:
        ratio_min = _sf(vol_cond.get("volume_ratio_min"))
        ratio_max = _sf(vol_cond.get("volume_ratio_max"))
        if volume_ratio is not None:
            if ratio_min is not None:
                scores["vol_min"] = 1.0 if volume_ratio >= ratio_min else volume_ratio / max(ratio_min, 1e-9)
            if ratio_max is not None:
                scores["vol_max"] = 1.0 if volume_ratio <= ratio_max else max(0.0, 1.0 - (volume_ratio - ratio_max))
        else:
            if ratio_min is not None or ratio_max is not None:
                scores["volume"] = 0.5  # unknown, don't block
                blockers.append("volume data unavailable")

    # â”€â”€ Schedule conditions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    schedule = conditions.get("schedule") or {}
    if schedule:
        valid_from = str(schedule.get("valid_from") or "").strip()
        valid_to = str(schedule.get("valid_to") or "").strip()
        session_req = str(schedule.get("session_constraint") or "any").strip().lower().replace("-", "_").replace(" ", "_")

        if session_req != "any" and market_session != session_req:
            scores["session"] = 0.0
            blockers.append(f"session={market_session}, need={session_req}")
        else:
            scores["session"] = 1.0

        if trade_day:
            if valid_from and trade_day < valid_from:
                scores["schedule_from"] = 0.0
                blockers.append(f"before valid_from={valid_from}")
            elif valid_to and trade_day > valid_to:
                scores["schedule_to"] = 0.0
                blockers.append(f"past valid_to={valid_to}")
            else:
                scores["schedule_window"] = 1.0

    # â”€â”€ Event conditions (always 0 unless confirmed externally) â”€â”€
    events = branch.get("event_conditions") or conditions.get("event_conditions") or []
    if events and isinstance(events, list):
        has_required = any(
            isinstance(e, dict) and e.get("requires_confirmation", True)
            for e in events
        )
        if has_required:
            scores["event_confirmation"] = 0.3  # can't assess, penalize mildly
            blockers.append("event confirmation pending")

    # â”€â”€ Composite score â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if not scores:
        final = 1.0  # no conditions = always matches
    else:
        final = min(scores.values())

    return BranchProximity(
        branch_id=branch_id,
        score=round(final, 3),
        breakdown=scores,
        blocking_reasons=blockers,
        branch_data=branch,
    )


# â”€â”€ Thesis digest (cached, compact) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class ThesisDigestCache:
    """
    Cache compressed thesis summaries to avoid rebuilding on every tick.
    The digest is a ~100-token summary of the thesis intent, built once
    and reused for all LLM calls on that thesis.
    """

    def __init__(self, max_size: int = 200):
        self._cache: Dict[str, str] = {}
        self._max_size = max_size

    def get_or_build(
        self,
        thesis_id: str,
        decision_text: Optional[str],
        plan_json: Optional[Dict[str, Any]],
    ) -> str:
        if thesis_id in self._cache:
            return self._cache[thesis_id]

        digest = self._build_digest(decision_text, plan_json)
        self._cache[thesis_id] = digest

        # Evict oldest if over capacity
        if len(self._cache) > self._max_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

        return digest

    def invalidate(self, thesis_id: str):
        self._cache.pop(thesis_id, None)

    @staticmethod
    def _build_digest(decision_text: Optional[str], plan: Optional[Dict[str, Any]]) -> str:
        """
        Build a compact thesis digest (~100-150 tokens) from available data.
        Extracts: ticker, direction, key levels, time horizon, core rationale.
        """
        parts: List[str] = []

        if plan and isinstance(plan, dict):
            ticker = plan.get("ticker", "?")
            action = plan.get("action", "?")
            confidence = plan.get("confidence", "?")
            horizon = plan.get("time_horizon", "?")
            rationale = str(plan.get("rationale") or "")[:300]

            parts.append(f"{ticker} {action} | conf={confidence} | horizon={horizon}")

            # Extract key levels from branches
            branches = plan.get("execution_plan") or []
            for b in branches[:3]:  # max 3 branches
                if not isinstance(b, dict):
                    continue
                bid = b.get("branch_id", "?")
                tmpl = b.get("action_template") or {}
                sl = tmpl.get("stop_loss")
                tp = tmpl.get("take_profit")
                lp = tmpl.get("limit_price")
                conds = b.get("conditions") or {}
                price_cond = conds.get("price") or {}

                level_parts = []
                for k, v in price_cond.items():
                    if v is not None:
                        level_parts.append(f"{k}={v}")
                if lp:
                    level_parts.append(f"limit={lp}")
                if sl:
                    level_parts.append(f"SL={sl}")
                if tp:
                    level_parts.append(f"TP={tp}")
                parts.append(f"  [{bid}] {tmpl.get('action','?')} {' '.join(level_parts)}")

            if rationale:
                # Take first 2 sentences of rationale
                sentences = rationale.replace("\n", " ").split(". ")
                parts.append("Rationale: " + ". ".join(sentences[:2]).strip())

        elif decision_text:
            # Fallback: extract from raw text
            lines = decision_text.strip().split("\n")
            # Take the first heading and first paragraph
            for line in lines[:20]:
                stripped = line.strip()
                if stripped.startswith("#") or (stripped and len(stripped) > 20):
                    parts.append(stripped[:200])
                    if len(parts) >= 3:
                        break

        return "\n".join(parts) if parts else "No thesis context available."


# â”€â”€ Skip-until cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class SkipUntilCache:
    """
    When the LLM says "nothing happening, check back in 2 hours",
    we cache that directive and skip LLM calls until the hint expires.
    """

    def __init__(self):
        self._cache: Dict[str, datetime] = {}
        self._state_hashes: Dict[str, str] = {}

    def should_skip(self, thesis_id: str, state_hash: str) -> bool:
        """True if we should skip LLM evaluation for this thesis."""
        # If state has materially changed, don't skip
        if self._state_hashes.get(thesis_id) != state_hash:
            return False

        skip_until = self._cache.get(thesis_id)
        if skip_until and datetime.utcnow() < skip_until:
            return True
        return False

    def set_skip(self, thesis_id: str, state_hash: str, hint: str):
        """Record a skip-until directive from the LLM."""
        duration = self._parse_hint(hint)
        if duration:
            self._cache[thesis_id] = datetime.utcnow() + duration
            self._state_hashes[thesis_id] = state_hash
            logger.debug("Skip-until set for %s: %s (%s)", thesis_id, hint, duration)

    def clear(self, thesis_id: str):
        self._cache.pop(thesis_id, None)
        self._state_hashes.pop(thesis_id, None)

    @staticmethod
    def _parse_hint(hint: str) -> Optional[timedelta]:
        h = str(hint).strip().lower().replace("-", "_").replace(" ", "_")
        mapping = {
            "next_tick": timedelta(minutes=0),
            "end_of_day": timedelta(hours=4),
            "eod": timedelta(hours=4),
            "hours_1": timedelta(hours=1),
            "hours_2": timedelta(hours=2),
            "hours_4": timedelta(hours=4),
            "next_session": timedelta(hours=12),
            "tomorrow": timedelta(hours=16),
        }
        return mapping.get(h)


# â”€â”€ State hashing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def compute_state_hash(
    *,
    price: Optional[float],
    volume_ratio: Optional[float],
    market_session: str,
    trade_day: Optional[str],
    days_active: int,
) -> str:
    """
    Coarse state hash for skip-until logic. Two ticks with the same
    hash are "materially identical" and don't need re-evaluation.

    Price is bucketed to 0.5% increments to avoid thrashing.
    """
    price_bucket = round(price * 200) / 200 if price else 0  # ~0.5% buckets
    vol_bucket = round((volume_ratio or 0) * 10) / 10         # 0.1x buckets
    raw = f"{price_bucket}|{vol_bucket}|{market_session}|{trade_day}|{days_active}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# â”€â”€ LLM evaluation result â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass
class LLMEvalResult:
    """Parsed, validated output from the LLM evaluator."""

    action: str  # EXECUTE, HOLD, CLOSE, WATCH
    branch_id: Optional[str] = None
    order: Optional[Dict[str, Any]] = None  # executable order params
    phase_transition: Optional[str] = None   # e.g. "WATCHING â†’ TRIGGERED"
    reasoning: str = ""
    next_eval_hint: str = "next_tick"
    confidence: float = 0.0
    raw_response: str = ""

    @property
    def is_executable(self) -> bool:
        return self.action == "EXECUTE" and self.order is not None


# â”€â”€ Core LLM evaluator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# System prompt â€” static, benefits from API-level caching (~150 tokens)
_SYSTEM_PROMPT = """You evaluate trade triggers for an automated journal agent.

Given a thesis digest, current tick data, and near-trigger branch details,
decide whether to EXECUTE a branch, HOLD (wait), CLOSE (exit/time-stop), or WATCH (keep monitoring).

RULES:
- "close_below"/"close_above" conditions refer to DAILY CLOSING prices, not intraday prints
- If the market session is not near close (after 3:30 PM ET), intraday price crossing a close_X level should be HOLD, not EXECUTE
- Time stops are hard exits â€” if days_active >= max_days, recommend CLOSE
- Volume conditions matter: if thesis requires declining volume but volume is elevated, that's a HOLD signal
- If an invalidation branch's conditions are closer to triggering than the primary branch, flag it

OUTPUT FORMAT â€” respond with ONLY this JSON, no other text:
{
  "action": "EXECUTE|HOLD|CLOSE|WATCH",
  "branch_id": "which branch if EXECUTE",
  "order": {"action":"BUY|SELL","order_type":"...","limit_price":...,"stop_loss":...,"take_profit":...,"quantity":null,"time_in_force":"DAY","position_size_pct":...},
  "reasoning": "one sentence",
  "next_eval_hint": "next_tick|end_of_day|hours_2|hours_4|next_session|tomorrow",
  "confidence": 0.0-1.0
}

If action is not EXECUTE, omit the "order" field or set it to null."""


class LLMEvaluator:
    """
    The Tier 2 intelligence layer. Calls an LLM only when the rule-based
    pre-filter detects a near-trigger situation.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        digest_cache: Optional[ThesisDigestCache] = None,
        skip_cache: Optional[SkipUntilCache] = None,
    ):
        self._llm = llm_client
        self._digest_cache = digest_cache or ThesisDigestCache()
        self._skip_cache = skip_cache or SkipUntilCache()
        self._call_count = 0
        self._last_call_ts: Optional[float] = None

    @property
    def call_count(self) -> int:
        return self._call_count

    def evaluate(
        self,
        *,
        thesis_id: str,
        decision_text: Optional[str],
        plan_json: Optional[Dict[str, Any]],
        near_branches: List[BranchProximity],
        price: Optional[float],
        volume_ratio: Optional[float],
        market_session: str,
        trade_day: Optional[str],
        days_active: int,
        eod_series: List[Dict[str, Any]],
        tracker_state: Dict[str, Any],
    ) -> Optional[LLMEvalResult]:
        """
        Run LLM evaluation. Returns None if skipped (due to cache/cooldown).
        """
        # â”€â”€ Skip-until check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        state_hash = compute_state_hash(
            price=price,
            volume_ratio=volume_ratio,
            market_session=market_session,
            trade_day=trade_day,
            days_active=days_active,
        )
        if self._skip_cache.should_skip(thesis_id, state_hash):
            logger.debug("Skipping LLM eval for %s (skip-until active)", thesis_id)
            return None

        # â”€â”€ Rate limiting (min 60s between calls for same thesis) â”€
        if self._last_call_ts and (time.time() - self._last_call_ts) < 30:
            logger.debug("Rate-limiting LLM eval (too soon)")
            return None

        # â”€â”€ Build compact prompt â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        digest = self._digest_cache.get_or_build(thesis_id, decision_text, plan_json)
        user_prompt = self._build_user_prompt(
            digest=digest,
            near_branches=near_branches,
            price=price,
            volume_ratio=volume_ratio,
            market_session=market_session,
            trade_day=trade_day,
            days_active=days_active,
            eod_series=eod_series,
        )

        # â”€â”€ Call LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            raw = self._llm.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=400,
            )
            self._call_count += 1
            self._last_call_ts = time.time()
        except Exception as e:
            logger.error("LLM evaluation failed: %s", e)
            return None

        # â”€â”€ Parse response â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        result = self._parse_response(raw)
        if result is None:
            logger.warning("Failed to parse LLM response: %s", raw[:200])
            return None

        # â”€â”€ Record skip-until hint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if result.next_eval_hint and result.action != "EXECUTE":
            self._skip_cache.set_skip(thesis_id, state_hash, result.next_eval_hint)

        return result

    def clear_skip(self, thesis_id: str):
        self._skip_cache.clear(thesis_id)

    # â”€â”€ Prompt construction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_user_prompt(
        self,
        *,
        digest: str,
        near_branches: List[BranchProximity],
        price: Optional[float],
        volume_ratio: Optional[float],
        market_session: str,
        trade_day: Optional[str],
        days_active: int,
        eod_series: List[Dict[str, Any]],
    ) -> str:
        """Build a compact (~200-350 token) user prompt."""
        lines: List[str] = []

        lines.append(f"THESIS:\n{digest}")

        # Tick context
        vr_str = f"{volume_ratio:.2f}x" if volume_ratio else "N/A"
        lines.append(
            f"\nTICK: price=${price:.2f} vol={vr_str} session={market_session} "
            f"day={trade_day} days_active={days_active}"
        )

        # Recent EOD series (last 3 days, very compact)
        if eod_series:
            eod_parts = []
            for e in eod_series[-3:]:
                vr = f" v={e['volume_ratio']:.1f}x" if e.get("volume_ratio") else ""
                eod_parts.append(f"{e['date']}=${e['price']:.2f}{vr}")
            lines.append(f"EOD_HISTORY: {' | '.join(eod_parts)}")

        # Near-trigger branches (only the relevant ones)
        if near_branches:
            lines.append("\nNEAR_BRANCHES:")
            for bp in near_branches:
                tmpl = bp.branch_data.get("action_template") or {}
                conds = bp.branch_data.get("conditions") or {}
                price_cond = conds.get("price") or {}

                cond_parts = []
                for k, v in price_cond.items():
                    cond_parts.append(f"{k}={v}")
                vol_cond = conds.get("volume") or {}
                for k, v in vol_cond.items():
                    cond_parts.append(f"{k}={v}")

                lines.append(
                    f"  [{bp.branch_id}] prox={bp.score:.2f} "
                    f"action={tmpl.get('action','?')} conds={{{','.join(cond_parts)}}} "
                    f"SL={tmpl.get('stop_loss')} TP={tmpl.get('take_profit')} "
                    f"limit={tmpl.get('limit_price')}"
                )
                if bp.blocking_reasons:
                    lines.append(f"    blockers: {'; '.join(bp.blocking_reasons[:3])}")

        lines.append("\nâ†’ JSON recommendation:")
        return "\n".join(lines)

    # â”€â”€ Response parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def _parse_response(raw: str) -> Optional[LLMEvalResult]:
        """Extract structured JSON from LLM response."""
        text = raw.strip()

        # Try direct JSON parse
        parsed = _try_parse_json(text)

        # Try extracting from markdown code block
        if parsed is None:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                parsed = _try_parse_json(match.group(1))

        # Try finding first { ... } block
        if parsed is None:
            match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if match:
                parsed = _try_parse_json(match.group(0))

        if not isinstance(parsed, dict):
            return None

        action = str(parsed.get("action", "HOLD")).upper()
        if action not in {"EXECUTE", "HOLD", "CLOSE", "WATCH"}:
            action = "HOLD"

        order = parsed.get("order")
        if action == "EXECUTE" and isinstance(order, dict):
            # Validate minimum order fields
            if "action" not in order:
                action = "HOLD"
                order = None
        else:
            order = None

        return LLMEvalResult(
            action=action,
            branch_id=str(parsed.get("branch_id") or "").strip() or None,
            order=order,
            reasoning=str(parsed.get("reasoning") or "")[:300],
            next_eval_hint=str(parsed.get("next_eval_hint") or "next_tick"),
            confidence=min(1.0, max(0.0, float(parsed.get("confidence", 0.5)))),
            raw_response=raw[:500],
        )


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _sf(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
