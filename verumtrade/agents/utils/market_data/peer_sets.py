"""Peer-set + sector-ETF resolver (Tier-2 peer / sector read-through).

See docs/macro_pullback_capability_upgrade.md. The per-ticker analysis path only ever sees the
*target's* own calendar and news, so a peer's event is invisible to it — e.g. Broadcom's soft
AI guidance on 2026-06-03 (a *peer's* print) is what unwound the whole AI-semis basket, yet it was
never in NVDA/AMD/MRVL's bundle. This module resolves a ticker to:

* ``resolve_peers``   — its peer basket: the small group of names that co-move on the same demand
  drivers / narrative (so peer catalysts and basket signals become visible).
* ``sector_etf_for``  — a representative sector / basket ETF (so the per-ticker path can ask
  "is my basket parabolic?" and measure beta to it).

Source = a curated ``_BASKETS`` map for the known crowded baskets (precise where it matters, so the
May/June 2026 counterfactuals provably fire), with the theme taxonomy
(``verumtrade.agents.discovery.theme_engine.taxonomy.ThemeTaxonomyLoader``) as an automatic fallback
for tickers that aren't curated. Everything degrades to ``[]`` / ``None`` on error or when disabled
via the ``enable_peer_read_through`` / ``enable_sector_parabola`` config flags, so it can never
break an analysis run.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Curated crowded baskets. ``members`` are US-listed tickers (matchable against the finnhub
# earnings calendar); ``foreign`` are co-moving names with no US earnings rows (used for the
# read-through narrative, skipped for peer-earnings matching). ``sector_etf`` is the most
# representative basket ETF for the "is my basket parabolic?" / basket-beta check — it is *not*
# restricted to the discovery heatmap's XL* set (we fetch it directly).
_BASKETS: Dict[str, Dict[str, Any]] = {
    "memory": {
        "members": ["MU", "WDC", "SNDK", "STX"],
        "foreign": ["000660.KS", "005930.KS"],  # SK Hynix, Samsung
        "foreign_markets": ["korea", "taiwan"],  # KOSPI/Taiwan read-through (Tier-3 item 6)
        "sector_etf": "SMH",
    },
    "ai_semis": {
        "members": ["NVDA", "AMD", "AVGO", "MRVL", "INTC", "TSM", "QCOM", "ARM", "MU"],
        "foreign": [],
        "foreign_markets": ["taiwan", "korea"],
        "sector_etf": "SMH",
    },
    "semi_equipment": {
        "members": ["AMAT", "LRCX", "KLAC", "ASML"],
        "foreign": [],
        "foreign_markets": ["taiwan", "korea"],
        "sector_etf": "SMH",
    },
    "megacap_tech": {
        "members": ["AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA"],
        "foreign": [],
        "sector_etf": "XLK",
    },
    "software": {
        "members": ["CRM", "NOW", "SNOW", "PLTR", "ADBE", "ORCL"],
        "foreign": [],
        "sector_etf": "XLK",
    },
}

_MAX_PEERS = 12


def _flag(config: Optional[Dict[str, Any]], key: str) -> bool:
    try:
        from verumtrade.dataflows.config import get_config

        cfg = config if config is not None else get_config()
        return bool(cfg.get(key, True))
    except Exception:
        return True


def _norm(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _curated_baskets_for(ticker: str) -> List[Dict[str, Any]]:
    return [b for b in _BASKETS.values() if ticker in b.get("members", [])]


def _curated_peers(ticker: str) -> List[str]:
    out: List[str] = []
    for basket in _curated_baskets_for(ticker):
        for sym in list(basket.get("members", [])) + list(basket.get("foreign", [])):
            if sym != ticker and sym not in out:
                out.append(sym)
    return out


def _taxonomy_peers(ticker: str, config: Optional[Dict[str, Any]]) -> List[str]:
    """Fallback: co-members of any theme chain the ticker has exposure to."""
    try:
        from verumtrade.agents.discovery.theme_engine.taxonomy import ThemeTaxonomyLoader

        loader = ThemeTaxonomyLoader(config=config)
        theme_ids = {c.theme_id for c in loader.candidates_for_ticker(ticker)}
        out: List[str] = []
        for theme_id in theme_ids:
            chain = loader.load_by_id(theme_id)
            if chain is None:
                continue
            for sym in chain.all_tickers:
                sym = _norm(sym)
                if sym and sym != ticker and sym not in out:
                    out.append(sym)
        return out
    except Exception as exc:  # taxonomy missing / pyyaml absent / malformed
        logger.debug("taxonomy peer fallback failed for %s: %s", ticker, exc)
        return []


def resolve_peers(ticker: str, config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Resolve ``ticker`` to its peer basket. Returns ``[]`` when disabled or none found.

    Curated baskets first (precise); theme-taxonomy co-members as fallback.
    """
    ticker = _norm(ticker)
    if not ticker or not _flag(config, "enable_peer_read_through"):
        return []
    peers = _curated_peers(ticker)
    if not peers:
        peers = _taxonomy_peers(ticker, config)
    return peers[:_MAX_PEERS]


def foreign_markets_for(ticker: str, config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Resolve ``ticker`` to the foreign market(s) whose stress reads through to its basket.

    Returns keys matching the foreign-market channel (``korea``/``taiwan``/``japan``/``china`` — see
    ``macro_regime._FOREIGN_MARKETS``), e.g. memory/AI-semis -> ``["korea", "taiwan"]``. ``[]`` when
    disabled or the ticker isn't in a curated basket. Gated by ``enable_foreign_market_channel``.
    """
    ticker = _norm(ticker)
    if not ticker or not _flag(config, "enable_foreign_market_channel"):
        return []
    out: List[str] = []
    for basket in _curated_baskets_for(ticker):
        for mkt in basket.get("foreign_markets", []) or []:
            if mkt not in out:
                out.append(str(mkt))
    return out


def sector_etf_for(ticker: str, config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve ``ticker`` to a representative basket/sector ETF for the parabola/beta check.

    Returns ``None`` when disabled or the ticker isn't in a curated basket (the taxonomy fallback
    has no reliable single-ETF mapping, so we don't guess).
    """
    ticker = _norm(ticker)
    if not ticker or not _flag(config, "enable_sector_parabola"):
        return None
    for basket in _curated_baskets_for(ticker):
        etf = basket.get("sector_etf")
        if etf:
            return str(etf)
    return None
