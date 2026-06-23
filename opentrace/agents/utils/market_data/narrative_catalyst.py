"""Narrative / policy / reflexive catalyst tagger (Tier-3 pullback-risk upgrade).

See ``docs/macro_pullback_capability_upgrade.md`` (Tier 3, item 5). The single-ticker catalyst /
news stack is built to flag *discrete, dated, company-specific* events. It is structurally blind to
the *soft, second-order, foreign-jurisdiction* signals that actually triggered the May-2026
memory-complex shock (a Korean policy chief's "citizen dividend" Facebook post) and the June-2026
AI-semis unwind (Broadcom's *peer* guide-down). Those moved crowded baskets with no company-specific
bad news on the names that fell hardest.

This module is the deterministic half of the "hybrid" approach (the other half is prompt steering in
the news / catalyst analysts): a pure, network-free keyword/regex tagger that scans the
**already-fetched** global-news headlines (carried on ``macro_regime['headlines_markdown']``) and
emits ``MacroEventRecord``-shaped rows with a broadened taxonomy:

* ``policy_trial_balloon``  — a floated/walked-back policy idea (windfall tax, citizen dividend, …).
* ``regulatory_narrative``  — export controls, antitrust, tariffs, probes, sanctions.
* ``peer_guidance``         — a peer's soft/cut/un-raised guidance tone (basket read-through).
* ``positioning_unwind``    — parabolic / crowded-trade / momentum-rotation language.

Output dicts round-trip through ``opentrace.schemas.catalyst_events.MacroEventRecord.from_dict`` and
are appended to the regime's ``macro_events`` (so they reach the catalyst bundle and every prompt
that already renders macro events). ``event_type`` is free-form on the schema, so no schema change is
needed. Degrades to ``[]`` on any error or when disabled via ``enable_narrative_catalysts``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)

_MAX_NARRATIVE_EVENTS = 8
_MAX_HEADLINES_CHARS = 8000

# (event_type, compiled pattern, label, affected_sectors, surprise_score, relevance_to_ticker).
# Conservative by design — these surface *fragility/regime* context, not trade signals. Patterns are
# tuned so the two real counterfactuals fire (Korea "citizen dividend"; AVGO guide-down) without
# tripping on routine headlines.
def _p(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_NARRATIVE_RULES: List[Tuple[str, Pattern[str], str, List[str], float, float]] = [
    (
        "policy_trial_balloon",
        _p(r"citizen\s+dividend|national\s+dividend|windfall\s+tax|excess[- ]profit\s+tax|"
           r"redistribut\w+|wealth\s+tax|trial\s+balloon|floated\s+(?:the\s+)?idea|"
           r"personal\s+opinion|walked?\s+back"),
        "Policy trial balloon (redistribution / windfall-tax narrative) targeting the boom thesis",
        ["semiconductors", "memory", "technology", "high_multiple_growth"],
        0.6,
        0.6,
    ),
    (
        "regulatory_narrative",
        _p(r"export\s+control|export\s+ban|chip\s+ban|antitrust|tariff|sanction|"
           r"regulatory\s+crackdown|probe|investigation\s+into|national\s+security\s+review"),
        "Regulatory / policy narrative (export controls / antitrust / tariffs / probe)",
        ["semiconductors", "technology", "broad_market"],
        0.55,
        0.55,
    ),
    (
        "peer_guidance",
        _p(r"guid\w+\s+below|below\s+(?:consensus|estimates|expectations)|cut\s+(?:its\s+)?"
           r"(?:forecast|guidance|outlook)|did\s+not\s+raise|left\s+(?:its\s+)?(?:forecast|guidance)"
           r"\s+unchanged|soft\s+guidance|light\s+guidance|weak\s+guidance|disappointing\s+"
           r"(?:guidance|outlook|forecast)|lowered\s+(?:its\s+)?(?:forecast|guidance|outlook)"),
        "Peer guidance tone soft / cut / not-raised (sector read-through to the crowded basket)",
        ["semiconductors", "technology", "high_multiple_growth"],
        0.55,
        0.55,
    ),
    (
        "positioning_unwind",
        _p(r"parabolic|crowded\s+trade|momentum\s+unwind|rotation\s+out\s+of|profit[- ]taking|"
           r"overbought|buyer\s+exhaustion|stretched\s+valuation|bubble\s+(?:fears?|talk|risk)|"
           r"unwind\w*"),
        "Positioning-unwind language (parabolic / crowded / momentum-rotation narrative)",
        ["momentum_crowded_names", "semiconductors", "technology"],
        0.5,
        0.5,
    ),
]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _narrative_event(
    event_type: str,
    label: str,
    *,
    release_time: str,
    surprise_score: float,
    affected_sectors: List[str],
    relevance_to_ticker: float,
    snippet: str = "",
) -> Dict[str, Any]:
    name = f"[{event_type}] {label}"
    if snippet:
        name = f"{name} - \"{snippet}\""
    return {
        "event_name": name[:300],
        "event_type": event_type,
        "release_time": release_time,
        "series_or_release_id": None,
        "actual": None,
        "consensus": None,
        "previous": None,
        "surprise_score": round(_clamp01(surprise_score), 3),
        "affected_sectors": affected_sectors,
        "relevance_to_ticker": round(_clamp01(relevance_to_ticker), 3),
    }


def _first_snippet(text: str, match: re.Match) -> str:
    """Return a short, single-line context window around a regex match for the event label."""
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    window = re.sub(r"\s+", " ", text[start:end]).strip()
    return window[:120]


def tag_narrative_events(
    headlines_markdown: str,
    *,
    as_of: str = "",
    extra_text: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan already-fetched headline text for soft/second-order/policy/foreign narrative catalysts.

    Pure / network-free. ``headlines_markdown`` is the global-news block already carried on
    ``macro_regime``; ``extra_text`` lets callers fold in additional already-fetched text (e.g. a
    foreign-market headline block from the Tier-3B channel). Returns ``MacroEventRecord``-shaped
    dicts (capped, deduped by event_type), or ``[]`` when disabled / empty / on any error.
    """
    try:
        if config is not None and not bool(config.get("enable_narrative_catalysts", True)):
            return []
        text = f"{headlines_markdown or ''}\n{extra_text or ''}".strip()
        if not text:
            return []
        text = text[:_MAX_HEADLINES_CHARS]

        events: List[Dict[str, Any]] = []
        seen_types: set[str] = set()
        for event_type, pattern, label, sectors, surprise, relevance in _NARRATIVE_RULES:
            if event_type in seen_types:
                continue
            match = pattern.search(text)
            if not match:
                continue
            seen_types.add(event_type)
            events.append(
                _narrative_event(
                    event_type,
                    label,
                    release_time=str(as_of or ""),
                    surprise_score=surprise,
                    affected_sectors=sectors,
                    relevance_to_ticker=relevance,
                    snippet=_first_snippet(text, match),
                )
            )
        return events[:_MAX_NARRATIVE_EVENTS]
    except Exception as exc:  # never break an analysis run on narrative tagging
        logger.debug("narrative catalyst tagging failed: %s", exc)
        return []
