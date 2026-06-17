"""Sector read-through block (Tier-2 peer read-through, Phase 2b).

See docs/macro_pullback_capability_upgrade.md. Phase 2a surfaced peers' *earnings dates* for free
off the already-fetched calendar (``peer_catalyst`` rows). Phase 2b adds the one piece with real
network cost: a *bounded* fetch of the nearest-to-report peers' recent news/guidance, rendered as a
compact "sector read-through" block for the news + catalyst prompts — so a peer's guidance *tone*
(the AVGO-style soft guide that unwound the AI-semis basket, not just the dated event) is visible.

Gated by **both** ``enable_peer_read_through`` and ``peer_read_through.fetch_peer_news`` (default
``False``, since it costs ~``max_peers`` + 1 vendor calls per run). Bounded by
``peer_read_through.max_peers`` (default 2). Degrades to ``{}`` on any error or when disabled, so it
can never break or meaningfully slow an analysis run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from opentrace.agents.utils.market_data.peer_sets import resolve_peers

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PEERS = 2
_NEWS_LOOKBACK_DAYS = 14
_EARNINGS_WINDOW_DAYS = 45
_EXCERPT_CHARS = 700


def _cfg_block(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    block = (config or {}).get("peer_read_through") or {}
    return block if isinstance(block, dict) else {}


def _calendar_items(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("earningsCalendar") or raw.get("earnings_calendar") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return [x for x in items if isinstance(x, dict)]


def _peer_next_earnings(raw: Any, peers: List[str], target: str) -> Dict[str, str]:
    """Map each peer to its earliest upcoming earnings date in the calendar (best-effort)."""
    peer_set = {p for p in peers if p != target}
    out: Dict[str, str] = {}
    for item in _calendar_items(raw):
        sym = str(item.get("symbol") or item.get("ticker") or "").upper()
        if sym not in peer_set:
            continue
        date = str(item.get("date") or item.get("earningsDate") or item.get("reportDate") or "").strip()
        if not date:
            continue
        if sym not in out or date < out[sym]:
            out[sym] = date
    return out


def build_sector_read_through(
    ticker: str,
    trade_date: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    route_fn: Any = None,
    peers: Optional[List[str]] = None,
    calendar_raw: Any = None,
    news_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Build the compact sector read-through block. Returns ``{}`` when disabled or empty.

    Selection: peers with an upcoming earnings date (soonest first) are preferred, then remaining
    peers in curated order; capped at ``max_peers``. Foreign peers (no US news feed, symbols with a
    ``.`` suffix) are skipped. ``peers`` / ``calendar_raw`` / ``news_fn`` are injectable for tests.
    """
    try:
        from opentrace.dataflows.config import get_config

        cfg = config if config is not None else get_config()
        if not bool(cfg.get("enable_peer_read_through", True)):
            return {}
        block = _cfg_block(cfg)
        if not bool(block.get("fetch_peer_news", False)):
            return {}

        target = str(ticker or "").upper()
        peer_list = peers if peers is not None else resolve_peers(target, cfg)
        peer_list = [str(p).upper() for p in peer_list if "." not in str(p) and str(p).upper() != target]
        if not peer_list:
            return {}
        max_peers = int(block.get("max_peers", _DEFAULT_MAX_PEERS) or _DEFAULT_MAX_PEERS)

        if route_fn is None:
            from opentrace.dataflows.interface import route_to_vendor

            route_fn = route_to_vendor

        # Rank by nearest upcoming earnings (best-effort; falls back to curated order).
        if calendar_raw is None:
            try:
                end = (
                    datetime.strptime(str(trade_date), "%Y-%m-%d") + timedelta(days=_EARNINGS_WINDOW_DAYS)
                ).strftime("%Y-%m-%d")
                from opentrace.dataflows.vendors.finnhub.finnhub_vendor import get_earnings_calendar_finnhub

                calendar_raw = get_earnings_calendar_finnhub(str(trade_date), end)
            except Exception:
                calendar_raw = None
        next_earn = _peer_next_earnings(calendar_raw, peer_list, target)
        ranked = sorted([p for p in peer_list if p in next_earn], key=lambda p: next_earn[p])
        ranked += [p for p in peer_list if p not in next_earn]
        selected = ranked[:max_peers]

        start = (
            datetime.strptime(str(trade_date), "%Y-%m-%d") - timedelta(days=_NEWS_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        if news_fn is None:
            def news_fn(sym: str) -> str:  # type: ignore[misc]
                return route_fn("get_news", sym, start, str(trade_date))

        entries: List[Dict[str, Any]] = []
        for peer in selected:
            try:
                raw_news = news_fn(peer)
            except Exception:
                raw_news = ""
            excerpt = " ".join(str(raw_news or "").split())[:_EXCERPT_CHARS]
            if not excerpt:
                continue
            entries.append(
                {"peer": peer, "next_earnings": next_earn.get(peer), "news_excerpt": excerpt}
            )
        if not entries:
            return {}
        return {
            "as_of": str(trade_date),
            "target": target,
            "peers_considered": peer_list,
            "entries": entries,
            "summary": _summary_line(target, entries),
        }
    except Exception as exc:  # never break an analysis run on the read-through
        logger.debug("sector read-through build failed for %s @ %s: %s", ticker, trade_date, exc)
        return {}


def _summary_line(target: str, entries: List[Dict[str, Any]]) -> str:
    names = ", ".join(str(e.get("peer")) for e in entries)
    return (
        f"Recent news/guidance for {len(entries)} nearest-reporting peer(s) of {target}: {names}. "
        "A peer's guidance tone can re-rate the whole crowded basket with no news on the target."
    )


def format_sector_read_through_markdown(block: Dict[str, Any]) -> str:
    """Compact markdown block for LLM prompts. Empty string when no read-through available."""
    if not isinstance(block, dict) or not block:
        return ""
    entries = block.get("entries") or []
    if not entries:
        return ""
    lines = [
        f"## Sector Read-Through (peers of {block.get('target', '')})",
        block.get("summary", ""),
    ]
    for e in entries:
        head = f"- **{e.get('peer', '')}**"
        if e.get("next_earnings"):
            head += f" (reports {e['next_earnings']})"
        lines.append(head + ":")
        lines.append("  " + str(e.get("news_excerpt", ""))[:500])
    return "\n".join(line for line in lines if line).strip()
