from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from tradingagents.dataflows.config import get_config


logger = logging.getLogger(__name__)


DEFAULT_PRIORITY = [
    "current_response",
    "trader_plan",
    "history_tail",
    "portfolio_context",
    "reports",
    "memories",
]


def get_budget_settings() -> Dict[str, Any]:
    cfg = get_config()
    settings = {
        "mode": str(cfg.get("context_budget_mode", "adaptive")).strip().lower(),
        "soft_cap_tokens": int(cfg.get("prompt_soft_cap_tokens", 45000)),
        "char_per_token_estimate": float(cfg.get("char_per_token_estimate", 4.0)),
        "section_max_chars_report": int(cfg.get("section_max_chars_report", 2200)),
        "section_max_chars_history": int(cfg.get("section_max_chars_history", 8000)),
        "section_max_chars_response": int(cfg.get("section_max_chars_response", 1800)),
        "section_max_chars_memory": int(cfg.get("section_max_chars_memory", 1200)),
        "section_max_chars_portfolio": int(cfg.get("section_max_chars_portfolio", 2500)),
        "section_max_chars_trader_plan": int(cfg.get("section_max_chars_trader_plan", 2000)),
    }
    if settings["mode"] == "compact":
        settings["soft_cap_tokens"] = max(2000, int(settings["soft_cap_tokens"] * 0.7))
        for key in (
            "section_max_chars_report",
            "section_max_chars_history",
            "section_max_chars_response",
            "section_max_chars_memory",
            "section_max_chars_portfolio",
            "section_max_chars_trader_plan",
        ):
            settings[key] = max(300, int(settings[key] * 0.6))
    return settings


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def estimate_tokens(text_or_messages: Any, *, char_per_token: float | None = None) -> int:
    settings = get_budget_settings()
    cpt = float(char_per_token or settings["char_per_token_estimate"] or 4.0)
    if cpt <= 0:
        cpt = 4.0

    if isinstance(text_or_messages, (list, tuple)):
        joined = "\n".join(normalize_text(x) for x in text_or_messages)
    elif isinstance(text_or_messages, dict):
        joined = "\n".join(f"{k}:{normalize_text(v)}" for k, v in text_or_messages.items())
    else:
        joined = normalize_text(text_or_messages)

    if not joined:
        return 0
    return max(1, int(len(joined) / cpt))


def clip_middle(text: Any, max_chars: int) -> str:
    s = normalize_text(text)
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s

    marker = f"\n...[TRUNCATED {len(s) - max_chars} chars]...\n"
    keep = max_chars - len(marker)
    if keep <= 20:
        return s[:max_chars]

    head = keep // 2
    tail = keep - head
    return s[:head] + marker + s[-tail:]


def cap_section(label: str, text: Any, max_chars: int) -> str:
    capped = clip_middle(text, int(max_chars))
    return capped


def cap_sections_with_soft_token_cap(
    sections: Dict[str, Any],
    soft_cap_tokens: int,
    priorities: Iterable[str] | None = None,
    *,
    min_chars: int = 280,
) -> Dict[str, str]:
    if soft_cap_tokens <= 0:
        return {k: normalize_text(v) for k, v in sections.items()}

    out: Dict[str, str] = {k: normalize_text(v) for k, v in sections.items()}
    if estimate_tokens(out) <= soft_cap_tokens:
        return out

    p = list(priorities or DEFAULT_PRIORITY)
    low_to_high = [k for k in reversed(p) if k in out] + [k for k in out.keys() if k not in p]

    # Progressive shrinking of lower-priority sections first.
    for _ in range(14):
        if estimate_tokens(out) <= soft_cap_tokens:
            break

        changed = False
        for key in low_to_high:
            current = out.get(key, "")
            if len(current) <= min_chars:
                continue
            new_len = max(min_chars, int(len(current) * 0.78))
            out[key] = clip_middle(current, new_len)
            changed = True
            if estimate_tokens(out) <= soft_cap_tokens:
                break

        if not changed:
            break

    # Final emergency clamp if still above soft cap.
    if estimate_tokens(out) > soft_cap_tokens:
        for key in low_to_high:
            current = out.get(key, "")
            if len(current) <= min_chars:
                continue
            out[key] = clip_middle(current, min_chars)
            if estimate_tokens(out) <= soft_cap_tokens:
                break

    return out


def prompt_diagnostics(node_name: str, sections: Dict[str, Any], clipped: bool) -> None:
    total_chars = sum(len(normalize_text(v)) for v in sections.values())
    total_tokens = estimate_tokens(sections)
    logger.debug(
        "[context-budget] node=%s clipped=%s approx_tokens=%s total_chars=%s",
        node_name,
        clipped,
        total_tokens,
        total_chars,
    )
