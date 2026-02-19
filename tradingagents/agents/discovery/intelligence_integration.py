# tradingagents/agents/discovery/intelligence_integration.py
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

from tradingagents.agents.discovery.intelligence import (
    IntelligenceResult,
    IntelligenceScanner,
    MomentumScanHit,
    Stage1EnrichmentScorecard,
    Stage2ScoredCandidate,
)

logger = logging.getLogger(__name__)


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
                ``"anomaly_scan"`` for Track B momentum anomaly scans.

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
        elif intelligence.stage2_candidates:
            rankings = self._rankings_from_stage2(
                intelligence.stage2_candidates, excluded_set,
            )
        else:
            rankings = self._rankings_from_technical(
                intelligence.technical_signals, excluded_set,
            )

        rankings.sort(key=lambda r: float(r.get("composite", 0.0)), reverse=True)
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
            "stage1": self._build_stage1_payload(intelligence.stage1_scorecards),
            "stage2": self._build_stage2_payload(intelligence.stage2_candidates),
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
        else:
            report_parts.append(
                "## Pipeline Mode\nTrack A: Prefilter + Stage1 Batch Enrichment + Stage2 Scoring\n"
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

        if track == "anomaly_scan":
            report_parts.extend(
                self._build_track_b_report_section(intelligence.momentum_scan_hits)
            )
        else:
            report_parts.extend(
                self._build_stage1_report_section(intelligence.stage1_scorecards)
            )
            report_parts.extend(
                self._build_stage2_report_section(intelligence.stage2_candidates)
            )

        if rankings:
            report_parts.append("## Recommended Stocks\n")
            for i, r in enumerate(rankings[:10] if len(rankings) > 10 else rankings[:5], 1):
                report_parts.append(
                    f"### {i}. **{r['ticker']}** - Composite: {r.get('composite', 'N/A')}/100"
                )
                if r.get("thesis"):
                    report_parts.append(f"- **Thesis:** {r['thesis']}")
                alignment = r.get("signal_alignment", "")
                if alignment:
                    report_parts.append(f"- **Signal Alignment:** {alignment}")
                report_parts.append("")

        # Use top 10 if we have more than 10 candidates, otherwise top 5
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
        return "\n".join(report_parts)

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
            # Multi-signal alignment: 25 points per scan triggered.
            base_score = min(len(ticker_hits) * 25, 100)
            # Add a bonus from the strongest signal value (capped at 20).
            strongest = max(abs(h.signal_value) for h in ticker_hits)
            composite = min(base_score + min(strongest, 20), 100)

            scan_names = sorted({h.scan_name for h in ticker_hits})
            thesis_parts = []
            for h in sorted(ticker_hits, key=lambda x: abs(x.signal_value), reverse=True):
                thesis_parts.append(
                    f"{h.scan_name}(signal={h.signal_value:.2f})"
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

        lines.append(
            "| Ticker | Scan | Signal Value | Key Details |"
        )
        lines.append("|---|---|---:|---|")
        for h in sorted(hits, key=lambda x: (x.scan_name, -abs(x.signal_value))):
            details = ", ".join(
                f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                for k, v in sorted(h.trigger_details.items())
            )
            lines.append(
                f"| {h.ticker} | {h.scan_name} | {h.signal_value:.4f} | {details} |"
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
    ) -> List[str]:
        if not candidates:
            return ["## Stage 2 Scoring\nNo Stage 2 candidates generated.\n"]

        lines: List[str] = ["## Stage 2 Scoring & Filtering"]
        lines.append(f"- Candidates passed: {len(candidates)}")
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
