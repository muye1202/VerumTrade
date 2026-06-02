# opentrace/agents/discovery/intelligence_integration.py
"""
Integration layer for stock discovery.

Active architecture:
  Stage 0: Programmatic universe prefilter (tradable US equities -> catalyst/liquidity prefilters)
  Stage 1: Batch enrichment (non-LLM)
  Stage 2: Technical numeric screening and ranking

Legacy synthesis/sector/catalyst paths were removed from runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from opentrace.agents.discovery.intelligence import (
    IntelligenceResult,
    IntelligenceScanner,
    MomentumScanHit,
    Stage1EnrichmentScorecard,
    Stage2ScoredCandidate,
)

logger = logging.getLogger(__name__)


def _normalize_console_unsafe_text(text: str) -> str:
    """Normalize characters that commonly fail on cp1252 terminals."""
    return str(text).replace("\u2011", "-")


class IntelligenceDrivenRecommender:
    """
    Discovery recommender powered by prefilter + stage1 enrichment + technical screening.

    Drop-in compatible with StockDiscoveryGraph.run_discovery().
    """

    def __init__(
        self,
        llm=None,
        deep_llm=None,
        quick_llm=None,
        config: Optional[Dict[str, Any]] = None,
        screening_universe: Optional[List[str]] = None,
    ):
        # Preserve constructor compatibility with older call sites.
        if deep_llm is None:
            deep_llm = llm
        if quick_llm is None:
            quick_llm = deep_llm

        self.deep_llm = deep_llm
        self.quick_llm = quick_llm
        self.config = config or {}
        self.scanner = IntelligenceScanner(llm=self.quick_llm, config=config)
        self.screening_universe = screening_universe
        self.logger = logging.getLogger(self.__class__.__name__)

    def recommend(
        self,
        trade_date: str,
        max_iterations: int = 3,  # Kept for API compatibility
        excluded_tickers: Optional[List[str]] = None,
        discovery_track: str = "enricher",
    ) -> Dict[str, Any]:
        """
        Run discovery pipeline and return legacy-compatible payload.

        Args:
            discovery_track: ``"enricher"`` for Stage 1→2 pipeline,
                ``"anomaly_scan"`` for Track B momentum anomaly scans,
                ``"dual_track"`` to run both tracks together and merge results.

        Returns:
            Dict with keys expected by StockDiscoveryGraph:
            - tickers: List[str]
            - report: str
            - raw_messages: list
            - iterations: int
        """
        track = str(discovery_track).strip().lower()
        self.logger.info(
            f"Discovery scan (track={track}) for {trade_date}"
        )
        excluded_set = self._normalize_ticker_set(excluded_tickers)

        intelligence = self.scanner.scan_with_prefilter_universe(
            trade_date=trade_date,
            excluded_tickers=sorted(excluded_set),
            discovery_track=track,
        )

        if track == "anomaly_scan":
            rankings = self._rankings_from_track_b(
                intelligence.momentum_scan_hits, excluded_set,
            )
        elif track == "dual_track":
            rankings = self._rankings_from_dual_track(
                intelligence.stage2_candidates,
                intelligence.momentum_scan_hits,
                excluded_set,
            )
        elif intelligence.stage2_candidates:
            rankings = self._rankings_from_stage2(
                intelligence.stage2_candidates, excluded_set,
            )
        else:
            rankings = self._rankings_from_technical(
                intelligence.technical_signals, excluded_set,
            )

        rankings.sort(key=lambda r: float(r.get("composite", 0.0)), reverse=True)
        # Dual-track targets 8-12 candidates; single tracks use existing logic
        if track == "dual_track":
            top_n = min(12, max(8, len(rankings))) if len(rankings) >= 8 else len(rankings)
        else:
            # Use top 10 if we have more than 10 candidates, otherwise top 5
            top_n = 10 if len(rankings) > 10 else 5
        tickers = [r["ticker"] for r in rankings[:top_n] if r.get("ticker")]

        report = self._build_report(
            intelligence,
            rankings,
            trade_date,
            excluded_tickers=sorted(excluded_set),
        )

        return {
            "tickers": tickers,
            "report": report,
            "raw_messages": [],
            "iterations": 0,
            "intelligence": intelligence,
            "rankings": rankings,
            "stage0": dict(intelligence.stage0_metrics or {}),
            "pre_stage0_snapshot": dict(intelligence.pre_stage0_snapshot or {}),
            "llm_bias_profile": dict(intelligence.llm_bias_profile or {}),
            "indicator_availability": dict(intelligence.indicator_availability or {}),
            "stage1": self._build_stage1_payload(intelligence.stage1_scorecards),
            "stage2": self._build_stage2_payload(intelligence.stage2_candidates),
            "vendor_calls_by_stage": dict(intelligence.vendor_calls_by_stage or {}),
            "data_quality_summary": dict(intelligence.data_quality_summary or {}),
            "filter_relaxations_applied": list(intelligence.filter_relaxations_applied or []),
            "excluded_tickers": sorted(excluded_set),
            "discovery_track": track,
        }

    def _build_report(
        self,
        intelligence: IntelligenceResult,
        rankings: List[Dict[str, Any]],
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
    ) -> str:
        track = intelligence.discovery_track
        report_parts = [f"# Stock Discovery Report - {trade_date}\n"]

        if track == "anomaly_scan":
            report_parts.append(
                "## Pipeline Mode\nTrack B: Short-Term Momentum Anomaly Scans\n"
            )
        elif track == "dual_track":
            report_parts.append(
                "## Pipeline Mode\nDual-Track: Track A (Enricher/Catalyst) + Track B (Momentum Anomaly Scans)\n"
            )
        else:
            report_parts.append(
                "## Pipeline Mode\nTrack A: Prefilter + Stage1 Batch Enrichment + Stage2 Scoring\n"
            )

        report_parts.extend(
            self._build_pre_stage0_snapshot_section(intelligence.pre_stage0_snapshot)
        )
        report_parts.extend(
            self._build_llm_bias_profile_section(intelligence.llm_bias_profile)
        )
        report_parts.extend(
            self._build_indicator_availability_section(intelligence.indicator_availability)
        )

        if intelligence.stage0_metrics:
            m = intelligence.stage0_metrics
            report_parts.append("## Stage 0 Metrics")
            report_parts.append(
                f"- assets_fetch_s: {float(m.get('assets_fetch_s', 0.0)):.2f}"
            )
            report_parts.append(
                f"- earnings_filter_s: {float(m.get('earnings_filter_s', 0.0)):.2f}"
            )
            report_parts.append(
                f"- adv_filter_s: {float(m.get('adv_filter_s', 0.0)):.2f}"
            )
            report_parts.append(
                f"- cache_hits: {int(m.get('cache_hits', 0))}"
            )
            report_parts.append(
                f"- cache_misses: {int(m.get('cache_misses', 0))}"
            )
            report_parts.append(
                f"- vendor_calls_estimate: {int(m.get('vendor_calls_estimate', 0))}"
            )
            report_parts.append("")

        if intelligence.vendor_calls_by_stage:
            report_parts.append("## Vendor Calls by Stage")
            for stage, metrics in sorted((intelligence.vendor_calls_by_stage or {}).items()):
                report_parts.append(f"- {stage}: {metrics}")
            report_parts.append("")

        if track == "anomaly_scan":
            report_parts.extend(
                self._build_track_b_report_section(intelligence.momentum_scan_hits)
            )
        elif track == "dual_track":
            report_parts.extend(
                self._build_stage1_report_section(intelligence.stage1_scorecards)
            )
            report_parts.extend(
                self._build_stage2_report_section(
                    intelligence.stage2_candidates,
                    filter_relaxations_applied=intelligence.filter_relaxations_applied,
                    data_quality_summary=intelligence.data_quality_summary,
                )
            )
            report_parts.extend(
                self._build_track_b_report_section(intelligence.momentum_scan_hits)
            )
            report_parts.extend(
                self._build_dual_track_report_section(rankings)
            )
        else:
            report_parts.extend(
                self._build_stage1_report_section(intelligence.stage1_scorecards)
            )
            report_parts.extend(
                self._build_stage2_report_section(
                    intelligence.stage2_candidates,
                    filter_relaxations_applied=intelligence.filter_relaxations_applied,
                    data_quality_summary=intelligence.data_quality_summary,
                )
            )

        if rankings:
            report_parts.append("## Recommended Stocks\n")
            # Dual-track targets 8-12; single tracks cap at 10 or 5
            if track == "dual_track":
                top_n = min(12, max(8, len(rankings))) if len(rankings) >= 8 else len(rankings)
            else:
                top_n = 10 if len(rankings) > 10 else 5
            for i, r in enumerate(rankings[:top_n], 1):
                source_badge = f" [{r.get('source', '')}]" if r.get('source') else ""
                report_parts.append(
                    f"### {i}. **{r['ticker']}**{source_badge} - Composite: {r.get('composite', 'N/A')}/100"
                )
                if r.get("thesis"):
                    report_parts.append(f"- **Thesis:** {r['thesis']}")
                alignment = r.get("signal_alignment", "")
                if alignment:
                    report_parts.append(f"- **Signal Alignment:** {alignment}")
                report_parts.append("")

        # Top-N summary (match recommend() logic)
        if track == "dual_track":
            top_n = min(12, max(8, len(rankings))) if len(rankings) >= 8 else len(rankings)
        else:
            top_n = 10 if len(rankings) > 10 else 5
        tickers = [r["ticker"] for r in rankings[:top_n] if r.get("ticker")]
        report_parts.append(f"## Top Pick Summary\n{', '.join(tickers)}")

        if excluded_tickers:
            report_parts.append(
                "\n## Excluded Existing Positions\n" + ", ".join(excluded_tickers)
            )

        report_parts.append(
            f"\n---\n*Scan completed in {intelligence.scan_duration_secs}s. "
            f"Track: {track}*"
        )
        return _normalize_console_unsafe_text("\n".join(report_parts))

    @staticmethod
    def _build_pre_stage0_snapshot_section(snapshot: Dict[str, Any]) -> List[str]:
        if not snapshot:
            return ["## Pre-Stage-0 Snapshot\nNo pre-stage snapshot available.\n"]
        lines: List[str] = ["## Pre-Stage-0 Snapshot"]
        idx = (snapshot.get("index_regime") or {}).get("indices") or {}
        spy = idx.get("SPY") or {}
        vix = (snapshot.get("vol_options") or {}).get("vix") or {}
        rates = (snapshot.get("rates_macro") or {}).get("yields") or {}
        calendar = snapshot.get("calendar") or {}
        lines.append(f"- Trade date: {snapshot.get('trade_date', '')}")
        if spy:
            r = spy.get("returns_pct") or {}
            rv = spy.get("realized_vol_annualized_pct") or {}
            lines.append(
                f"- SPY: 1D {r.get('1d', 'N/A')}%, 20D {r.get('20d', 'N/A')}%, RV20 {rv.get('20d', 'N/A')}%"
            )
        if vix:
            vr = vix.get("returns_pct") or {}
            lines.append(
                f"- VIX: level {vix.get('level', 'N/A')}, 1D {vr.get('1d', 'N/A')}%, 5D {vr.get('5d', 'N/A')}%"
            )
        tnx = rates.get("^TNX") or {}
        if tnx:
            tr = tnx.get("returns_pct") or {}
            lines.append(
                f"- UST 10Y: level {tnx.get('level', 'N/A')}, 1D {tr.get('1d', 'N/A')}%"
            )
        earn = (calendar.get("earnings_season_proxy") or {}).get("intensity_label")
        if earn:
            lines.append(f"- Earnings season intensity proxy: {earn}")
        cache = snapshot.get("cache_metrics") or {}
        if cache:
            lines.append(
                f"- Layer-0 cache: hits={cache.get('cache_hits', 0)} misses={cache.get('cache_misses', 0)} vendor_calls_est={cache.get('vendor_calls_estimate', 0)} llm_calls={cache.get('llm_calls', 0)}"
            )
        lines.append("")
        return lines

    @staticmethod
    def _build_llm_bias_profile_section(bias: Dict[str, Any]) -> List[str]:
        if not bias:
            return ["## LLM Bias Profile\nNo LLM bias profile generated.\n"]
        lines: List[str] = ["## LLM Bias Profile"]
        lines.append(f"- Schema version: {bias.get('schema_version', 'legacy')}")
        lines.append(f"- Regime label: {bias.get('regime_label', 'NEUTRAL')}")
        lines.append(f"- Risk posture: {bias.get('risk_posture', 'NEUTRAL')}")
        tracks = bias.get("preferred_tracks") or []
        if isinstance(tracks, list) and tracks:
            lines.append(f"- Preferred tracks: {', '.join(str(t) for t in tracks)}")
        s0 = bias.get("stage0_overrides") or {}
        if s0:
            lines.append(f"- Stage 0 overrides: {s0}")
        tilts = bias.get("stage2_weight_tilts") or {}
        if tilts:
            lines.append(f"- Stage 2 weight tilts: {tilts}")

        policy = bias.get("policy") or {}
        if isinstance(policy, dict):
            universe = policy.get("universe") or {}
            allocation = (universe.get("allocation") or {})
            max_tickers = allocation.get("max_tickers") or {}
            if max_tickers:
                lines.append(f"- Allocation max_tickers: {max_tickers}")
            split = allocation.get("dual_track_split") or {}
            if split:
                lines.append(f"- Dual-track split: {split}")

            sector_weights = universe.get("sector_weights") or {}
            if isinstance(sector_weights, dict):
                non_neutral = {
                    str(k): float(v)
                    for k, v in sector_weights.items()
                    if isinstance(v, (int, float)) and abs(float(v) - 1.0) > 1e-9
                }
                if non_neutral:
                    lines.append(f"- Sector weight multipliers (non-neutral): {non_neutral}")

            scoring = policy.get("scoring") or {}
            hard = scoring.get("stage2_hard_filter_overrides") or {}
            if hard:
                lines.append(f"- Stage 2 hard-filter overrides: {hard}")

            anomaly = policy.get("anomaly_scan") or {}
            enabled_scans = anomaly.get("enabled_scans") or []
            if enabled_scans:
                lines.append(f"- Track B enabled scans: {enabled_scans}")
            thresholds = anomaly.get("thresholds") or {}
            if thresholds:
                lines.append(f"- Track B threshold overrides: {thresholds}")

        note = str(bias.get("scan_notes", "")).strip()
        if note:
            lines.append(f"- Notes: {note}")
        lines.append("")
        return lines

    @staticmethod
    def _build_indicator_availability_section(availability: Dict[str, Any]) -> List[str]:
        if not availability:
            return ["## Skipped Indicators (Unavailable with current tools)\nNo availability report.\n"]
        lines: List[str] = ["## Skipped Indicators (Unavailable with current tools)"]
        skipped = availability.get("skipped_unavailable") or []
        failed = availability.get("failed_runtime") or []
        lines.append(f"- Computed indicators: {len(availability.get('computed') or [])}")
        lines.append(f"- Skipped unavailable: {len(skipped)}")
        if skipped:
            lines.append("- " + ", ".join(str(x) for x in skipped))
        if failed:
            lines.append(f"- Runtime failures: {', '.join(str(x) for x in failed)}")
        lines.append("")
        return lines

    @staticmethod
    def _build_stage1_payload(scorecards: List[Stage1EnrichmentScorecard]) -> Dict[str, Any]:
        payload_rows = [
            {
                "ticker": s.ticker,
                "earnings_beat_rate_4q": s.earnings_beat_rate_4q,
                "options_unusual_score": s.options_unusual_score,
                "short_interest_pct_float": s.short_interest_pct_float,
                "insider_signal": s.insider_signal,
                "data_quality_flags": list(s.data_quality_flags or []),
            }
            for s in scorecards
        ]
        covered = len([r for r in payload_rows if not r["data_quality_flags"]])
        return {
            "count": len(payload_rows),
            "coverage_pct": round((covered / len(payload_rows)) * 100.0, 1) if payload_rows else 0.0,
            "scorecards": payload_rows,
        }

    @staticmethod
    def _build_stage1_report_section(scorecards: List[Stage1EnrichmentScorecard]) -> List[str]:
        if not scorecards:
            return ["## Stage 1 Enrichment\nNo Stage 1 scorecards generated.\n"]

        lines: List[str] = ["## Stage 1 Enrichment"]
        lines.append(f"- Tickers enriched: {len(scorecards)}")
        covered = [s for s in scorecards if not s.data_quality_flags]
        lines.append(f"- Full data coverage: {len(covered)}/{len(scorecards)} ({(len(covered)/len(scorecards))*100:.1f}%)")

        top_options = sorted(scorecards, key=lambda s: s.options_unusual_score, reverse=True)[:3]
        top_short = sorted(scorecards, key=lambda s: s.short_interest_pct_float, reverse=True)[:3]
        top_beat = sorted(scorecards, key=lambda s: s.earnings_beat_rate_4q, reverse=True)[:3]

        if top_options:
            lines.append("- Top options unusual activity: " + ", ".join(f"{s.ticker}({s.options_unusual_score:.1f})" for s in top_options))
        if top_short:
            lines.append("- Top short-interest signals: " + ", ".join(f"{s.ticker}({s.short_interest_pct_float:.1f}%)" for s in top_short))
        if top_beat:
            lines.append("- Top earnings beat-rate: " + ", ".join(f"{s.ticker}({s.earnings_beat_rate_4q:.1f}%)" for s in top_beat))

        lines.append("")
        lines.append("| Ticker | ROC20d | RS-SPY20d | Options | Short % Float | Beat Rate 4Q | Insider | Flags |")
        lines.append("|---|---:|---:|---:|---:|---:|---|---|")
        for s in scorecards:
            flags = ",".join(s.data_quality_flags) if s.data_quality_flags else "-"
            lines.append(
                f"| {s.ticker} | {s.roc_20d:.2f} | {s.rs_vs_spy_20d:.2f} | {s.options_unusual_score:.1f} | "
                f"{s.short_interest_pct_float:.1f} | {s.earnings_beat_rate_4q:.1f} | {s.insider_signal} | {flags} |"
            )
        lines.append("")
        return lines

    @staticmethod
    def _normalize_ticker_set(tickers: Optional[List[str]]) -> Set[str]:
        return {
            str(t).strip().upper()
            for t in (tickers or [])
            if str(t).strip()
        }

    # ------------------------------------------------------------------
    # Stage 2 ranking helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _rankings_from_stage2(
        candidates: List[Stage2ScoredCandidate],
        excluded_set: Set[str],
    ) -> List[Dict[str, Any]]:
        rankings: List[Dict[str, Any]] = []
        for c in candidates:
            ticker = str(c.ticker).strip().upper()
            if not ticker or ticker in excluded_set:
                continue
            rankings.append(
                {
                    "ticker": ticker,
                    "composite": round(float(c.composite_score), 2),
                    "thesis": (
                        f"Stage2 composite {c.composite_score:.2f}; "
                        f"earnings {c.earnings_surprise_score:.0f}; "
                        f"momentum {c.technical_momentum_score:.0f}; "
                        f"options {c.options_flow_score:.0f}; "
                        f"sector {c.sector_momentum_score:.0f}; "
                        f"squeeze {c.short_squeeze_score:.0f}"
                    ),
                    "signal_alignment": "stage2_5factor",
                }
            )
        return rankings

    @staticmethod
    def _rankings_from_technical(technical_signals, excluded_set: Set[str]) -> List[Dict[str, Any]]:
        """Fallback: build rankings from raw technical signals (pre-Stage-2 path)."""
        rankings: List[Dict[str, Any]] = []
        for s in technical_signals:
            ticker = str(getattr(s, "ticker", "")).strip().upper()
            if not ticker or ticker in excluded_set:
                continue
            rankings.append(
                {
                    "ticker": ticker,
                    "composite": round(float(getattr(s, "composite_score", 0.0)), 2),
                    "thesis": (
                        f"Technical composite {float(getattr(s, 'composite_score', 0.0)):.2f}; "
                        f"20d momentum {float(getattr(s, 'momentum_20d', 0.0)):+.2f}%; "
                        f"ADX {float(getattr(s, 'adx', 0.0)):.1f}; "
                        f"RS vs SPY {float(getattr(s, 'relative_strength_vs_spy', 0.0)):.2f}"
                    ),
                    "signal_alignment": "technical",
                }
            )
        return rankings

    # ------------------------------------------------------------------
    # Track B ranking helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _track_b_hit_strength(hit: MomentumScanHit) -> float:
        """Return normalized 0-100 strength for cross-scan ranking."""
        if isinstance(getattr(hit, "normalized_strength", None), (int, float)):
            v = float(getattr(hit, "normalized_strength"))
            if v > 0:
                return max(0.0, min(100.0, v))

        details = dict(getattr(hit, "trigger_details", {}) or {})
        scan = str(getattr(hit, "scan_name", "")).strip().lower()
        raw = abs(float(getattr(hit, "signal_value", 0.0)))
        if scan == "volatility_breakout":
            pct = details.get("bb_width_percentile")
            if isinstance(pct, (int, float)):
                return max(0.0, min(100.0, 100.0 - float(pct)))
            return max(0.0, min(100.0, raw))
        if scan == "momentum_acceleration":
            return max(0.0, min(100.0, (raw / 4.0) * 100.0))
        if scan == "rs_divergence":
            return max(0.0, min(100.0, (raw / 10.0) * 100.0))
        if scan == "stealth_accumulation":
            threshold = details.get("obv_slope_threshold")
            if isinstance(threshold, (int, float)) and float(threshold) > 0:
                ratio = raw / float(threshold)
                return max(0.0, min(100.0, (ratio - 1.0) * 100.0))
            return max(0.0, min(100.0, raw))
        return max(0.0, min(100.0, raw))

    @classmethod
    def _track_b_composite_for_hits(cls, ticker_hits: List[MomentumScanHit]) -> float:
        # Multi-signal alignment: 25 points per scan triggered.
        base_score = min(len(ticker_hits) * 25, 100)
        strongest_strength = max(cls._track_b_hit_strength(h) for h in ticker_hits)
        # Strength bonus contributes up to +20 points.
        bonus = min(20.0, strongest_strength * 0.20)
        return min(base_score + bonus, 100.0)

    @staticmethod
    def _rankings_from_track_b(
        hits: List[MomentumScanHit],
        excluded_set: Set[str],
    ) -> List[Dict[str, Any]]:
        """Build rankings from Track B momentum anomaly scan hits.

        Tickers that trigger multiple scans are ranked higher.  The
        composite score is derived from the number of scans triggered
        (multi-signal alignment bonus) plus the absolute signal value.
        """
        from collections import defaultdict

        # Group hits by ticker.
        by_ticker: Dict[str, List[MomentumScanHit]] = defaultdict(list)
        for h in hits:
            ticker = str(h.ticker).strip().upper()
            if ticker and ticker not in excluded_set:
                by_ticker[ticker].append(h)

        rankings: List[Dict[str, Any]] = []
        for ticker, ticker_hits in by_ticker.items():
            composite = IntelligenceDrivenRecommender._track_b_composite_for_hits(ticker_hits)

            scan_names = sorted({h.scan_name for h in ticker_hits})
            thesis_parts = []
            for h in sorted(
                ticker_hits,
                key=lambda x: IntelligenceDrivenRecommender._track_b_hit_strength(x),
                reverse=True,
            ):
                strength = IntelligenceDrivenRecommender._track_b_hit_strength(h)
                thesis_parts.append(
                    f"{h.scan_name}(signal={h.signal_value:.2f},strength={strength:.1f})"
                )

            rankings.append(
                {
                    "ticker": ticker,
                    "composite": round(composite, 2),
                    "thesis": f"Track B hits: {'; '.join(thesis_parts)}",
                    "signal_alignment": ",".join(scan_names),
                    "scan_count": len(ticker_hits),
                }
            )
        return rankings

    @staticmethod
    def _build_track_b_report_section(
        hits: List[MomentumScanHit],
    ) -> List[str]:
        """Build markdown report section for Track B momentum scan hits."""
        if not hits:
            return ["## Track B: Momentum Anomaly Scans\nNo anomalies detected.\n"]

        lines: List[str] = ["## Track B: Momentum Anomaly Scans"]
        lines.append(f"- Total hits: {len(hits)}")

        # Count by scan type.
        from collections import Counter
        scan_counts = Counter(h.scan_name for h in hits)
        for name, count in sorted(scan_counts.items()):
            lines.append(f"  - {name}: {count}")
        lines.append("")

        lines.append("| Ticker | Scan | Signal Value | Strength | Key Details |")
        lines.append("|---|---|---:|---:|---|")
        for h in sorted(hits, key=lambda x: (x.scan_name, -abs(x.signal_value))):
            details = ", ".join(
                f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                for k, v in sorted(h.trigger_details.items())
            )
            strength = IntelligenceDrivenRecommender._track_b_hit_strength(h)
            lines.append(
                f"| {h.ticker} | {h.scan_name} | {h.signal_value:.4f} | {strength:.1f} | {details} |"
            )
        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Dual-Track ranking + report helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _rankings_from_dual_track(
        stage2_candidates: List["Stage2ScoredCandidate"],
        momentum_hits: List["MomentumScanHit"],
        excluded_set: Set[str],
    ) -> List[Dict[str, Any]]:
        """Merge Track A and Track B results with a convergence bonus.

        Scoring rules (per design doc):
        - Track A tickers start from their Stage 2 composite score (0-100).
        - Track B tickers start from multi-scan score (25 pts per scan triggered
          + strongest signal capped at 20).
        - Tickers appearing in BOTH tracks receive a +20% convergence bonus
          on the higher of the two base scores, capped at 100.
        - Final list is sorted descending by composite.
        """
        from collections import defaultdict

        # ---- Track A: Stage 2 scored candidates ----
        track_a: Dict[str, Dict[str, Any]] = {}
        for c in stage2_candidates:
            ticker = str(c.ticker).strip().upper()
            if not ticker or ticker in excluded_set:
                continue
            track_a[ticker] = {
                "ticker": ticker,
                "composite": round(float(c.composite_score), 2),
                "thesis": (
                    f"Stage2 composite {c.composite_score:.2f}; "
                    f"earnings {c.earnings_surprise_score:.0f}; "
                    f"momentum {c.technical_momentum_score:.0f}; "
                    f"options {c.options_flow_score:.0f}; "
                    f"sector {c.sector_momentum_score:.0f}; "
                    f"squeeze {c.short_squeeze_score:.0f}"
                ),
                "signal_alignment": "stage2_5factor",
                "source": "catalyst",
            }

        # ---- Track B: momentum scan hits ----
        by_ticker: Dict[str, list] = defaultdict(list)
        for h in momentum_hits:
            ticker = str(h.ticker).strip().upper()
            if ticker and ticker not in excluded_set:
                by_ticker[ticker].append(h)

        track_b: Dict[str, Dict[str, Any]] = {}
        for ticker, ticker_hits in by_ticker.items():
            composite = round(
                IntelligenceDrivenRecommender._track_b_composite_for_hits(ticker_hits),
                2,
            )
            scan_names = sorted({h.scan_name for h in ticker_hits})
            thesis_parts = [
                (
                    f"{h.scan_name}(signal={h.signal_value:.2f},"
                    f"strength={IntelligenceDrivenRecommender._track_b_hit_strength(h):.1f})"
                )
                for h in sorted(
                    ticker_hits,
                    key=lambda x: IntelligenceDrivenRecommender._track_b_hit_strength(x),
                    reverse=True,
                )
            ]
            track_b[ticker] = {
                "ticker": ticker,
                "composite": composite,
                "thesis": f"Track B hits: {'; '.join(thesis_parts)}",
                "signal_alignment": ",".join(scan_names),
                "scan_count": len(ticker_hits),
                "source": "momentum",
            }

        # ---- Merge ----
        merged: Dict[str, Dict[str, Any]] = {}
        for ticker in set(track_a) | set(track_b):
            in_a = ticker in track_a
            in_b = ticker in track_b

            if in_a and in_b:
                # Convergence: take higher base score and apply +20% bonus
                base = max(track_a[ticker]["composite"], track_b[ticker]["composite"])
                converged = round(min(base * 1.20, 100.0), 2)
                merged[ticker] = {
                    "ticker": ticker,
                    "composite": converged,
                    "thesis": (
                        f"[BOTH TRACKS] A: {track_a[ticker]['thesis']} | "
                        f"B: {track_b[ticker]['thesis']}"
                    ),
                    "signal_alignment": (
                        f"stage2_5factor,{track_b[ticker]['signal_alignment']}"
                    ),
                    "source": "both",
                }
            elif in_a:
                merged[ticker] = track_a[ticker]
            else:
                merged[ticker] = track_b[ticker]

        return sorted(merged.values(), key=lambda r: float(r["composite"]), reverse=True)

    @staticmethod
    def _build_dual_track_report_section(
        rankings: List[Dict[str, Any]],
    ) -> List[str]:
        """Build a convergence summary table for the dual-track merged results."""
        if not rankings:
            return ["## Dual-Track Convergence Summary\nNo merged candidates.\n"]

        both = [r for r in rankings if r.get("source") == "both"]
        catalyst_only = [r for r in rankings if r.get("source") == "catalyst"]
        momentum_only = [r for r in rankings if r.get("source") == "momentum"]

        lines: List[str] = ["## Dual-Track Convergence Summary"]
        lines.append(f"- Total merged candidates: {len(rankings)}")
        if both:
            lines.append(
                f"- **Convergence tickers (both tracks, +20% bonus):** "
                + ", ".join(r["ticker"] for r in both)
            )
        lines.append(f"- Catalyst-only (Track A): {len(catalyst_only)}")
        lines.append(f"- Momentum-only (Track B): {len(momentum_only)}")
        lines.append("")

        lines.append("| Ticker | Composite | Source | Signal Alignment |")
        lines.append("|---|---:|---|---|")
        for r in rankings:
            source_str = r.get("source", "?")
            if source_str == "both":
                source_str = "**both** ★"
            lines.append(
                f"| {r['ticker']} | {r['composite']:.1f} | {source_str} | {r.get('signal_alignment', '')} |"
            )
        lines.append("")
        return lines

    @staticmethod
    def _build_stage2_payload(
        candidates: List[Stage2ScoredCandidate],
    ) -> Dict[str, Any]:
        rows = [
            {
                "ticker": c.ticker,
                "composite_score": c.composite_score,
                "earnings_surprise_score": c.earnings_surprise_score,
                "technical_momentum_score": c.technical_momentum_score,
                "options_flow_score": c.options_flow_score,
                "sector_momentum_score": c.sector_momentum_score,
                "short_squeeze_score": c.short_squeeze_score,
            }
            for c in candidates
        ]
        return {
            "count": len(rows),
            "candidates": rows,
        }

    @staticmethod
    def _build_stage2_report_section(
        candidates: List[Stage2ScoredCandidate],
        filter_relaxations_applied: Optional[List[str]] = None,
        data_quality_summary: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        if not candidates:
            return ["## Stage 2 Scoring\nNo Stage 2 candidates generated.\n"]

        lines: List[str] = ["## Stage 2 Scoring & Filtering"]
        lines.append(f"- Candidates passed: {len(candidates)}")
        if filter_relaxations_applied:
            lines.append(f"- Min-candidate relaxations applied: {', '.join(filter_relaxations_applied)}")
        quality = dict(data_quality_summary or {})
        if quality:
            lines.append(
                f"- Data quality flagged: {quality.get('flagged', 0)}/{quality.get('total', 0)} "
                f"({quality.get('flagged_pct', 0.0)}%)"
            )
            breadth = dict(quality.get("breadth_context") or {})
            if breadth:
                lines.append(
                    f"- Breadth proxy: %>50DMA={breadth.get('pct_above_50dma', 'N/A')} "
                    f"%>200DMA={breadth.get('pct_above_200dma', 'N/A')} "
                    f"NH-NL={breadth.get('new_high_minus_new_low_proxy_pct', 'N/A')} "
                    f"weak={breadth.get('weak_breadth', False)}"
                )
        lines.append("")
        lines.append(
            "| Ticker | Composite | Earnings | Momentum | Options | Sector | Squeeze |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for c in candidates:
            lines.append(
                f"| {c.ticker} "
                f"| {c.composite_score:.1f} "
                f"| {c.earnings_surprise_score:.0f} "
                f"| {c.technical_momentum_score:.0f} "
                f"| {c.options_flow_score:.0f} "
                f"| {c.sector_momentum_score:.0f} "
                f"| {c.short_squeeze_score:.0f} |"
            )
        lines.append("")
        return lines


def patch_discovery_graph_with_intelligence(discovery_graph) -> None:
    """
    Replace a discovery graph recommender with the active intelligence recommender.
    """
    discovery_graph.recommender = IntelligenceDrivenRecommender(
        deep_llm=getattr(discovery_graph, "deep_llm", getattr(discovery_graph, "llm", None)),
        quick_llm=getattr(discovery_graph, "quick_llm", getattr(discovery_graph, "llm", None)),
        config=discovery_graph.config,
    )
    logger.info("StockDiscoveryGraph patched with IntelligenceDrivenRecommender")
