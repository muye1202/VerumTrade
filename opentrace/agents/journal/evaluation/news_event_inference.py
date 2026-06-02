from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, Optional

from opentrace.agents.journal.core.models import TradeThesis


def infer_event_flags(
    *,
    thesis: TradeThesis,
    event_keys: Iterable[str],
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Infer event confirmation flags for requested event keys.
    Deterministic rules are always available; optional LLM provider is placeholder-gated.
    """
    provider_name = str(provider or os.getenv("JOURNAL_EVENT_INFERENCE_PROVIDER", "rules")).strip().lower()
    if provider_name == "llm":
        # Placeholder hook. Deterministic fallback keeps behavior predictable.
        return _infer_by_rules(thesis=thesis, event_keys=event_keys)
    return _infer_by_rules(thesis=thesis, event_keys=event_keys)


def event_inference_enabled() -> bool:
    return str(os.getenv("JOURNAL_EVENT_INFERENCE_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _infer_by_rules(
    *,
    thesis: TradeThesis,
    event_keys: Iterable[str],
) -> Dict[str, Any]:
    corpus = " ".join(
        [
            str(getattr(thesis, "news_summary", "") or ""),
            str(getattr(thesis, "risk_judge_summary", "") or ""),
            str(getattr(thesis, "final_decision_text", "") or ""),
            str(getattr(thesis, "key_risks", "") or ""),
        ]
    ).lower()
    out: Dict[str, Any] = {}
    for raw_key in event_keys:
        key = str(raw_key or "").strip()
        if not key:
            continue
        key_l = key.lower()
        if "clean" in key_l:
            out[key] = _infer_clean_signal(corpus, key_l)
            continue
        if "delay" in key_l:
            out[key] = _contains_any(corpus, ["delay", "delayed", "pushback", "slip"])
            continue
        # Generic fallback: key-token presence -> true
        tokens = [t for t in re.split(r"[_\-\s]+", key_l) if len(t) > 2]
        if tokens and all(t in corpus for t in tokens[:2]):
            out[key] = True
    return out


def _infer_clean_signal(corpus: str, key_l: str) -> bool:
    # Treat "clean commentary" style keys as positive only when no obvious negatives are nearby.
    positives = ["on track", "no delay", "affirmed", "clear progress", "successful"]
    negatives = ["delay", "rupture", "issue", "concern", "risk", "slip", "problem"]
    has_pos = _contains_any(corpus, positives)
    has_neg = _contains_any(corpus, negatives)
    if "neutron" in key_l and "neutron" not in corpus:
        return False
    return bool(has_pos and not has_neg)


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)
