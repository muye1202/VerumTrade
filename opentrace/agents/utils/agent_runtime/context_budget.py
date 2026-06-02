from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List

from opentrace.dataflows.config import get_config
from opentrace.agents.analysts.workbench import build_ledger_evidence_summary


logger = logging.getLogger(__name__)


ANALYST_REPORT_KEYS = [
    ("market", "market_report"),
    ("sentiment", "sentiment_report"),
    ("news", "news_report"),
    ("fundamentals", "fundamentals_report"),
]

_REPORT_PREAMBLE_PATTERNS = [
    r"^\s*I now have all the data needed\.\s*Here is the complete .*?\n+",
    r"^\s*Now I have comprehensive data\.\s*Let me compile .*?\n+",
    r"^\s*Now I have all the data I need\.\s*Let me compile .*?\n+",
    r"^\s*I have all the data I need\.\s*Let me compile .*?\n+",
    r"^\s*Based on the .*?data retrieved, I have sufficient information.*?\n+",
]


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
    mode = str(cfg.get("context_budget_mode", "adaptive")).strip().lower()
    if mode not in {"off", "adaptive", "compact"}:
        mode = "adaptive"
    settings = {
        "mode": mode,
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
    settings = get_budget_settings()
    if settings["mode"] == "off":
        return normalize_text(text)
    capped = clip_middle(text, int(max_chars))
    return capped


def _strip_analyst_noise(text: Any) -> str:
    s = normalize_text(text)
    for pattern in _REPORT_PREAMBLE_PATTERNS:
        s = re.sub(pattern, "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(
        r"FINAL TRANSACTION PROPOSAL:?\s*\*\*(BUY|SELL|HOLD)\*\*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"(?is)---\s*\**FINAL TRANSACTION PROPOSAL:.*", "", s)
    s = re.sub(r"(?is)\n#+\s*Final Transaction Proposal\b.*", "", s)
    return normalize_text(s)


def _score_evidence_line(line: str) -> int:
    lower = line.lower()
    score = 0
    if re.search(r"\$?\d+(?:\.\d+)?%?", line):
        score += 2
    for term in (
        "risk",
        "invalidation",
        "trigger",
        "support",
        "resistance",
        "stop",
        "target",
        "confidence",
        "because",
        "therefore",
        "implies",
        "suggests",
        "indicates",
        "missing",
        "unavailable",
        "discrepancy",
        "conflict",
        "valuation",
        "earnings",
        "volume",
        "atr",
        "vwap",
    ):
        if term in lower:
            score += 1
    if line.lstrip().startswith(("-", "*", "|")):
        score += 1
    return score


def build_report_evidence_summary(label: str, report: Any, max_chars: int | None = None) -> str:
    """Compress an analyst Markdown report into decision evidence.

    This is intentionally deterministic so saved DB reports can be replayed without
    invoking an LLM or vendor APIs.
    """
    settings = get_budget_settings()
    limit = int(max_chars or settings["section_max_chars_report"])
    text = _strip_analyst_noise(report)
    if not text:
        return f"## {label.title()} Evidence\nNo report available."

    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    title = next((line for line in lines if line.startswith("#")), "")
    verdict = next(
        (
            line
            for line in lines
            if re.search(r"\b(verdict|bottom line|bias|recommendation|regime)\b", line, re.I)
        ),
        "",
    )

    candidates = []
    for idx, line in enumerate(lines):
        if line.startswith("#"):
            continue
        score = _score_evidence_line(line)
        if score <= 0:
            continue
        candidates.append((score, idx, line))

    selected = []
    seen = set()
    for _, _, line in sorted(candidates, key=lambda item: (-item[0], item[1])):
        compact = re.sub(r"\s+", " ", line).strip()
        key = compact.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        selected.append(compact)
        if len(selected) >= 8:
            break

    out = [f"## {label.title()} Evidence"]
    if title:
        out.append(title.lstrip("#").strip())
    if verdict and verdict != title:
        out.append(f"Verdict/context: {verdict.lstrip('-* ').strip()}")
    if selected:
        out.append("Key decision evidence:")
        out.extend(f"- {line.lstrip('-* ').strip()}" for line in selected)
    else:
        out.append(cap_section(label, text, limit))

    return cap_section(label, "\n".join(out), limit)


def format_analyst_evidence_context(
    state: Dict[str, Any],
    *,
    max_chars_per_report: int | None = None,
) -> str:
    """Build compact analyst context for downstream debate and manager nodes."""
    sections: List[str] = ["# Analyst Evidence Context"]
    for label, report_key in ANALYST_REPORT_KEYS:
        evidence_key = f"{label}_evidence"
        ledger_key = f"{label}_ledger"
        evidence = state.get(evidence_key)
        if evidence:
            section = cap_section(
                evidence_key,
                evidence,
                int(max_chars_per_report or get_budget_settings()["section_max_chars_report"]),
            )
        elif state.get(ledger_key):
            section = cap_section(
                ledger_key,
                build_ledger_evidence_summary(
                    label,
                    state.get(ledger_key, {}),
                    max_chars=max_chars_per_report,
                ),
                int(max_chars_per_report or get_budget_settings()["section_max_chars_report"]),
            )
        else:
            section = build_report_evidence_summary(
                label,
                state.get(report_key, ""),
                max_chars=max_chars_per_report,
            )
        if section:
            sections.append(section)
    return normalize_text("\n\n".join(sections))


def cap_sections_with_soft_token_cap(
    sections: Dict[str, Any],
    soft_cap_tokens: int,
    priorities: Iterable[str] | None = None,
    *,
    min_chars: int = 280,
) -> Dict[str, str]:
    settings = get_budget_settings()
    if settings["mode"] == "off":
        return {k: normalize_text(v) for k, v in sections.items()}

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
