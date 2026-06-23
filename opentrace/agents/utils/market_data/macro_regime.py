"""Macro / regime context bus for the per-ticker analysis stack.

Tier-0 upgrade (see docs/macro_pullback_capability_upgrade.md): the discovery lane already
computes a rich cross-asset / regime / positioning snapshot in
``opentrace.agents.discovery.intelligence.market_context_snapshot.PreStage0IntelligenceBuilder``
(sector heatmap, factor spreads incl. momentum-vs-SPY, VIX, rate impulse, oil, calendar), but
that intelligence never reaches the per-ticker news / catalyst / risk nodes. This module wraps
the builder and exposes:

* ``build_macro_regime_context`` — build the cached snapshot and reduce it to a compact, prompt
  ready dict (the "context bus" payload injected into graph state as ``macro_regime``).
* ``summarize_macro_regime`` — pure reduction of an already-built snapshot dict (network-free,
  unit-testable).
* ``extract_macro_events`` — derive ``MacroEventRecord``-shaped events from regime facts to
  populate ``CatalystEventBundle.macro_events`` (previously hard-coded to ``[]``).
* ``format_macro_regime_markdown`` — render a compact markdown block for LLM prompts.

Every public entry degrades to an empty result on any error or when disabled via the
``enable_macro_regime_context`` config flag, so it can never break an analysis run.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Thresholds for deriving regime "events" from the snapshot. Conservative by design — these
# surface *fragility/regime* context, not trade signals.
_OIL_SHOCK_5D_PCT = 5.0
_VIX_ELEVATED_LEVEL = 20.0
_VIX_SPIKE_1D_PCT = 8.0
_MOMENTUM_CROWD_SPREAD_PCT = 4.0
_SECTOR_DISTRIBUTION_5D_PCT = -3.0
_FOREIGN_STRESS_1D_PCT = -3.0
_FOREIGN_STRESS_5D_PCT = -5.0
_MAX_MACRO_EVENTS = 10

# Foreign-market label -> (display name, affected sectors for read-through). Tier-3 item 6.
_FOREIGN_MARKETS = {
    "korea": ("Korea (EWY)", ["memory", "semiconductors", "technology"]),
    "taiwan": ("Taiwan (EWT)", ["semiconductors", "technology"]),
    "japan": ("Japan (EWJ)", ["technology", "broad_market"]),
    "china": ("China (FXI)", ["broad_market", "materials"]),
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_macro_regime_context(
    trade_date: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    route_fn: Any = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the compact macro-regime context for ``trade_date``.

    Wraps the discovery-lane snapshot builder (24h disk-cached). Returns ``{}`` when disabled,
    on any error, or when the snapshot is empty, so callers can treat an empty dict as
    "no macro context available" without special-casing failures.
    """
    try:
        from opentrace.dataflows.config import get_config

        cfg = config if config is not None else get_config()
        if not bool(cfg.get("enable_macro_regime_context", True)):
            return {}
        if snapshot is None:
            from opentrace.agents.discovery.intelligence.market_context_snapshot import (
                PreStage0IntelligenceBuilder,
            )

            builder_cfg = dict(cfg)
            if route_fn is not None:
                builder_cfg["_route_to_vendor"] = route_fn
            builder = PreStage0IntelligenceBuilder(config=builder_cfg)
            snapshot, _availability = builder.build(str(trade_date))
        return summarize_macro_regime(snapshot, trade_date=str(trade_date), config=cfg)
    except Exception as exc:  # never break an analysis run on macro context
        logger.debug("macro_regime build failed for %s: %s", trade_date, exc)
        return {}


def _compact_index(idx: Dict[str, Any]) -> Dict[str, Any]:
    idx = idx or {}
    returns = idx.get("returns_pct") or {}
    dist = idx.get("distance_to_dma_pct") or {}
    return {
        "ret_1d_pct": _num(returns.get("1d")),
        "ret_5d_pct": _num(returns.get("5d")),
        "ret_20d_pct": _num(returns.get("20d")),
        "ret_ytd_pct": _num(returns.get("ytd")),
        "above_all_smas": bool(idx.get("above_all_smas")),
        "dist_to_50dma_pct": _num(dist.get("to_50dma_pct")),
        "dist_to_200dma_pct": _num(dist.get("to_200dma_pct")),
    }


def _compact_sector_heatmap(heatmap: Dict[str, Any]) -> Dict[str, Any]:
    compact: Dict[str, Dict[str, Any]] = {}
    for etf, row in (heatmap or {}).items():
        if not isinstance(row, dict):
            continue
        returns = row.get("returns_pct") or {}
        rs = row.get("rs_vs_spy_pct") or {}
        compact[etf] = {
            "ret_5d_pct": _num(returns.get("5d")),
            "rs_vs_spy_5d_pct": _num(rs.get("5d")),
        }
    return compact


def _compact_foreign(foreign: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, row in (foreign or {}).items():
        if not isinstance(row, dict):
            continue
        returns = row.get("returns_pct") or {}
        out[name] = {
            "ret_1d_pct": _num(returns.get("1d")),
            "ret_5d_pct": _num(returns.get("5d")),
        }
    return out


def _sector_leaders_laggards(compact_heatmap: Dict[str, Any]) -> Dict[str, List[str]]:
    scored = [
        (etf, row.get("rs_vs_spy_5d_pct"))
        for etf, row in compact_heatmap.items()
        if isinstance(row.get("rs_vs_spy_5d_pct"), (int, float))
    ]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    leaders = [etf for etf, _ in scored[:3]]
    laggards = [etf for etf, _ in scored[-3:]][::-1]
    return {"leaders": leaders, "laggards": laggards}


def summarize_macro_regime(
    snapshot: Dict[str, Any],
    trade_date: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reduce a built snapshot dict to the compact ``macro_regime`` bus payload.

    Pure / network-free: safe to call in tests with a synthetic snapshot. ``config`` (optional) gates
    the Tier-3 narrative tagger via ``enable_narrative_catalysts``; when omitted, tagging is on.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return {}

    index_regime = snapshot.get("index_regime") or {}
    indices = index_regime.get("indices") or {}
    vol = snapshot.get("vol_options") or {}
    vix = vol.get("vix") or {}
    rates = snapshot.get("rates_macro") or {}
    cross = snapshot.get("cross_asset") or {}
    sector = snapshot.get("sector_factor") or {}
    calendar = snapshot.get("calendar") or {}
    foreign = snapshot.get("foreign_markets") or {}

    compact_heatmap = _compact_sector_heatmap(sector.get("sector_heatmap") or {})
    regime: Dict[str, Any] = {
        "as_of": snapshot.get("trade_date") or trade_date,
        "risk_off": bool(index_regime.get("risk_off_flag")),
        "spx": _compact_index(indices.get("SPY") or {}),
        "ndx": _compact_index(indices.get("QQQ") or {}),
        "vix": {
            "level": _num(vix.get("level")),
            "ret_1d_pct": _num((vix.get("returns_pct") or {}).get("1d")),
        },
        "rate_impulse": rates.get("rate_impulse"),
        "curve_10y_minus_3m": _num((rates.get("curve_slopes") or {}).get("10y_minus_3m")),
        "oil": {
            "brent_5d_pct": _num(((cross.get("oil_brent") or {}).get("returns_pct") or {}).get("5d")),
            "wti_5d_pct": _num(((cross.get("oil_wti") or {}).get("returns_pct") or {}).get("5d")),
        },
        "foreign_markets": _compact_foreign(foreign),
        "sector_heatmap": compact_heatmap,
        "sector_leaders_laggards": _sector_leaders_laggards(compact_heatmap),
        "factor_spreads_20d_pct": sector.get("factor_spreads_20d_pct") or {},
        "calendar": {
            "opex_week": bool(calendar.get("opex_week_flag")),
            "quarter_end": bool(calendar.get("quarter_end_flag")),
            "month_end": bool(calendar.get("month_end_flag")),
            "earnings_intensity": (calendar.get("earnings_season_proxy") or {}).get("intensity_label"),
        },
        "headlines_markdown": str((snapshot.get("global_news") or {}).get("headlines_markdown") or "")[:1200],
    }
    regime["macro_events"] = extract_macro_events(regime)
    # Tier-3 (item 5): tag soft/second-order/policy/foreign narrative catalysts from the
    # already-fetched headline text and fold them into macro_events (no extra network call). The
    # tagger degrades to [] on any error/disable, so this never breaks the reduction.
    try:
        from opentrace.agents.utils.market_data.narrative_catalyst import tag_narrative_events

        narrative_events = tag_narrative_events(
            regime.get("headlines_markdown") or "",
            as_of=str(regime.get("as_of") or ""),
            config=config,
        )
        if narrative_events:
            regime["macro_events"] = (regime["macro_events"] + narrative_events)[:_MAX_MACRO_EVENTS]
            regime["narrative_events"] = narrative_events
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("narrative catalyst tagging skipped: %s", exc)
    regime["summary"] = _summary_line(regime)
    return regime


def _event(
    event_name: str,
    *,
    release_time: str,
    surprise_score: float,
    affected_sectors: List[str],
    relevance_to_ticker: float,
) -> Dict[str, Any]:
    return {
        "event_name": event_name,
        "release_time": release_time,
        "series_or_release_id": None,
        "actual": None,
        "consensus": None,
        "previous": None,
        "surprise_score": round(max(0.0, min(1.0, surprise_score)), 3),
        "affected_sectors": affected_sectors,
        "relevance_to_ticker": round(max(0.0, min(1.0, relevance_to_ticker)), 3),
    }


def extract_macro_events(regime: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive ``MacroEventRecord``-shaped regime events from the compact regime payload.

    These are *regime/positioning* events (risk-off tape, rates-up impulse, oil shock, elevated
    vol, crowded momentum factor, sector distribution, calendar windows) — the class of signal
    that single-ticker event feeds miss. Output dicts round-trip through
    ``opentrace.schemas.catalyst_events.MacroEventRecord.from_dict``.
    """
    if not isinstance(regime, dict) or not regime:
        return []
    as_of = str(regime.get("as_of") or "")
    events: List[Dict[str, Any]] = []

    if regime.get("risk_off"):
        events.append(
            _event(
                "Broad risk-off tape (SPX down, VIX up, credit weak)",
                release_time=as_of,
                surprise_score=0.6,
                affected_sectors=["broad_market"],
                relevance_to_ticker=0.6,
            )
        )

    if regime.get("rate_impulse") == "RATES_UP":
        events.append(
            _event(
                "10Y yields rising (rate impulse up) - pressures high-multiple growth",
                release_time=as_of,
                surprise_score=0.55,
                affected_sectors=["technology", "semiconductors", "high_multiple_growth"],
                relevance_to_ticker=0.6,
            )
        )

    oil = regime.get("oil") or {}
    oil_5d = max(
        [v for v in (oil.get("brent_5d_pct"), oil.get("wti_5d_pct")) if isinstance(v, (int, float))],
        default=None,
    )
    if isinstance(oil_5d, (int, float)) and oil_5d >= _OIL_SHOCK_5D_PCT:
        events.append(
            _event(
                f"Oil spike (+{oil_5d:.1f}% / 5d) - energy/inflation pressure, rate-fear channel",
                release_time=as_of,
                surprise_score=min(0.8, 0.4 + oil_5d / 50.0),
                affected_sectors=["energy(+)", "rate_sensitive(-)", "broad_margins(-)"],
                relevance_to_ticker=0.5,
            )
        )

    vix = regime.get("vix") or {}
    vix_level = vix.get("level")
    vix_1d = vix.get("ret_1d_pct")
    if (isinstance(vix_level, (int, float)) and vix_level >= _VIX_ELEVATED_LEVEL) or (
        isinstance(vix_1d, (int, float)) and vix_1d >= _VIX_SPIKE_1D_PCT
    ):
        label = f"{vix_level:.1f}" if isinstance(vix_level, (int, float)) else "elevated"
        events.append(
            _event(
                f"Volatility regime elevated (VIX {label})",
                release_time=as_of,
                surprise_score=0.55,
                affected_sectors=["broad_market"],
                relevance_to_ticker=0.55,
            )
        )

    mom_spread = (regime.get("factor_spreads_20d_pct") or {}).get("momentum_minus_spy")
    if isinstance(mom_spread, (int, float)) and mom_spread >= _MOMENTUM_CROWD_SPREAD_PCT:
        events.append(
            _event(
                f"Crowded momentum factor (MTUM-SPY +{mom_spread:.1f}%/20d) - unwind risk if leadership breaks",
                release_time=as_of,
                surprise_score=min(0.7, 0.4 + mom_spread / 40.0),
                affected_sectors=["momentum_crowded_names"],
                relevance_to_ticker=0.5,
            )
        )

    laggards = (regime.get("sector_leaders_laggards") or {}).get("laggards") or []
    heatmap = regime.get("sector_heatmap") or {}
    for etf in laggards[:2]:
        ret_5d = (heatmap.get(etf) or {}).get("ret_5d_pct")
        if isinstance(ret_5d, (int, float)) and ret_5d <= _SECTOR_DISTRIBUTION_5D_PCT:
            events.append(
                _event(
                    f"Sector under distribution: {etf} {ret_5d:.1f}%/5d",
                    release_time=as_of,
                    surprise_score=min(0.7, 0.4 + abs(ret_5d) / 30.0),
                    affected_sectors=[etf],
                    relevance_to_ticker=0.45,
                )
            )

    foreign = regime.get("foreign_markets") or {}
    for key, (label, sectors) in _FOREIGN_MARKETS.items():
        row = foreign.get(key) or {}
        ret_1d = row.get("ret_1d_pct")
        ret_5d = row.get("ret_5d_pct")
        stressed = (isinstance(ret_1d, (int, float)) and ret_1d <= _FOREIGN_STRESS_1D_PCT) or (
            isinstance(ret_5d, (int, float)) and ret_5d <= _FOREIGN_STRESS_5D_PCT
        )
        if not stressed:
            continue
        worst = min([v for v in (ret_1d, ret_5d) if isinstance(v, (int, float))], default=0.0)
        events.append(
            _event(
                f"Foreign-market stress: {label} {worst:.1f}% - cross-border flow/basket "
                "read-through (a foreign shock can hit the US basket before company news)",
                release_time=as_of,
                surprise_score=min(0.75, 0.45 + abs(worst) / 30.0),
                affected_sectors=sectors,
                relevance_to_ticker=0.55,
            )
        )

    calendar = regime.get("calendar") or {}
    if calendar.get("opex_week"):
        events.append(
            _event(
                "Options-expiration week (dealer/gamma flows can amplify moves)",
                release_time=as_of,
                surprise_score=0.4,
                affected_sectors=["broad_market"],
                relevance_to_ticker=0.4,
            )
        )
    if calendar.get("quarter_end"):
        events.append(
            _event(
                "Quarter-end rebalancing window",
                release_time=as_of,
                surprise_score=0.4,
                affected_sectors=["broad_market"],
                relevance_to_ticker=0.4,
            )
        )

    return events[:_MAX_MACRO_EVENTS]


def _summary_line(regime: Dict[str, Any]) -> str:
    parts: List[str] = []
    parts.append("RISK-OFF" if regime.get("risk_off") else "risk-neutral/on")
    impulse = regime.get("rate_impulse")
    if impulse:
        parts.append(f"rates {impulse}")
    vix = regime.get("vix") or {}
    if isinstance(vix.get("level"), (int, float)):
        v1d = vix.get("ret_1d_pct")
        v1d_txt = f" ({v1d:+.1f}% 1d)" if isinstance(v1d, (int, float)) else ""
        parts.append(f"VIX {vix['level']:.1f}{v1d_txt}")
    oil = regime.get("oil") or {}
    if isinstance(oil.get("brent_5d_pct"), (int, float)):
        parts.append(f"Brent {oil['brent_5d_pct']:+.1f}%/5d")
    mom = (regime.get("factor_spreads_20d_pct") or {}).get("momentum_minus_spy")
    if isinstance(mom, (int, float)) and mom >= _MOMENTUM_CROWD_SPREAD_PCT:
        parts.append("momentum-crowded")
    return "Regime: " + " | ".join(parts)


def _fmt_pct(value: Any) -> str:
    return f"{value:+.1f}%" if isinstance(value, (int, float)) else "n/a"


def format_macro_regime_markdown(regime: Dict[str, Any]) -> str:
    """Render a compact markdown block for LLM prompts. Empty string when no regime data."""
    if not isinstance(regime, dict) or not regime:
        return ""
    spx = regime.get("spx") or {}
    ndx = regime.get("ndx") or {}
    vix = regime.get("vix") or {}
    oil = regime.get("oil") or {}
    spreads = regime.get("factor_spreads_20d_pct") or {}
    ll = regime.get("sector_leaders_laggards") or {}
    cal = regime.get("calendar") or {}

    vix_level = vix.get("level")
    vix_txt = f"{vix_level:.1f} ({_fmt_pct(vix.get('ret_1d_pct'))} 1d)" if isinstance(vix_level, (int, float)) else "n/a"
    curve = regime.get("curve_10y_minus_3m")
    curve_txt = f"{curve:+.2f}" if isinstance(curve, (int, float)) else "n/a"

    lines = [
        f"## Market Regime Context (as of {regime.get('as_of', '')})",
        regime.get("summary", ""),
        f"- Tape: {'RISK-OFF' if regime.get('risk_off') else 'risk-neutral/on'}; "
        f"SPX 1d {_fmt_pct(spx.get('ret_1d_pct'))} / 5d {_fmt_pct(spx.get('ret_5d_pct'))} "
        f"(above all SMAs: {spx.get('above_all_smas')}); NDX 5d {_fmt_pct(ndx.get('ret_5d_pct'))}",
        f"- Vol: VIX {vix_txt}",
        f"- Rates: impulse {regime.get('rate_impulse') or 'n/a'}; 10y-3m {curve_txt}",
        f"- Oil: Brent {_fmt_pct(oil.get('brent_5d_pct'))}/5d, WTI {_fmt_pct(oil.get('wti_5d_pct'))}/5d",
        f"- Factor spreads (20d): momentum-SPY {_fmt_pct(spreads.get('momentum_minus_spy'))}, "
        f"growth-value {_fmt_pct(spreads.get('growth_minus_value'))}, "
        f"small-large {_fmt_pct(spreads.get('small_minus_large'))}",
        f"- Sector 5d RS vs SPY - leaders: {', '.join(ll.get('leaders') or []) or 'n/a'}; "
        f"laggards: {', '.join(ll.get('laggards') or []) or 'n/a'}",
        f"- Calendar: OPEX week {cal.get('opex_week')}, quarter-end {cal.get('quarter_end')}, "
        f"earnings intensity {cal.get('earnings_intensity') or 'n/a'}",
    ]
    foreign = regime.get("foreign_markets") or {}
    foreign_bits = [
        f"{_FOREIGN_MARKETS.get(k, (k.title(), []))[0]} {_fmt_pct((row or {}).get('ret_1d_pct'))}/1d"
        for k, row in foreign.items()
        if isinstance((row or {}).get("ret_1d_pct"), (int, float)) or isinstance((row or {}).get("ret_5d_pct"), (int, float))
    ]
    if foreign_bits:
        lines.append("- Foreign markets (proxy for cross-border flow stress): " + ", ".join(foreign_bits))
    macro_events = regime.get("macro_events") or []
    if macro_events:
        lines.append("- Derived macro/regime events:")
        for ev in macro_events:
            lines.append(
                f"  - {ev.get('event_name', '')} (relevance {ev.get('relevance_to_ticker', 0)})"
            )
    return "\n".join(line for line in lines if line).strip()
