# tradingagents/agents/discovery/intelligence_integration.py
"""
Integration layer for stock discovery.

Active architecture:
  Stage 0: Programmatic universe prefilter (tradable US equities -> ADV -> earnings window)
  Stage 1: Technical numeric screening and ranking

Legacy synthesis/sector/catalyst paths were removed from runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from tradingagents.agents.discovery.intelligence import (
    IntelligenceResult,
    IntelligenceScanner,
)

logger = logging.getLogger(__name__)


class IntelligenceDrivenRecommender:
    """
    Discovery recommender powered by prefilter + technical screening only.

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
    ) -> Dict[str, Any]:
        """
        Run discovery pipeline and return legacy-compatible payload.

        Returns:
            Dict with keys expected by StockDiscoveryGraph:
            - tickers: List[str]
            - report: str
            - raw_messages: list
            - iterations: int
        """
        self.logger.info(f"Discovery scan (prefilter+technical only) for {trade_date}")
        excluded_set = self._normalize_ticker_set(excluded_tickers)

        intelligence = self.scanner.scan_with_prefilter_universe(
            trade_date=trade_date,
            excluded_tickers=sorted(excluded_set),
        )

        rankings: List[Dict[str, Any]] = []
        for s in intelligence.technical_signals:
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

        rankings.sort(key=lambda r: float(r.get("composite", 0.0)), reverse=True)
        tickers = [r["ticker"] for r in rankings[:5] if r.get("ticker")]

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
            "excluded_tickers": sorted(excluded_set),
        }

    def _build_report(
        self,
        intelligence: IntelligenceResult,
        rankings: List[Dict[str, Any]],
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
    ) -> str:
        report_parts = [f"# Stock Discovery Report - {trade_date}\n"]
        report_parts.append("## Pipeline Mode\nPrefilter + Technical (sector/catalyst legacy paths disabled)\n")

        if rankings:
            report_parts.append("## Recommended Stocks\n")
            for i, r in enumerate(rankings[:5], 1):
                report_parts.append(
                    f"### {i}. **{r['ticker']}** - Composite: {r.get('composite', 'N/A')}/100"
                )
                if r.get("thesis"):
                    report_parts.append(f"- **Thesis:** {r['thesis']}")
                alignment = r.get("signal_alignment", "")
                if alignment:
                    report_parts.append(f"- **Signal Alignment:** {alignment}")
                report_parts.append("")

        tickers = [r["ticker"] for r in rankings[:5] if r.get("ticker")]
        report_parts.append(f"## Top Pick Summary\n{', '.join(tickers)}")

        if excluded_tickers:
            report_parts.append(
                "\n## Excluded Existing Positions\n" + ", ".join(excluded_tickers)
            )

        report_parts.append(
            f"\n---\n*Scan completed in {intelligence.scan_duration_secs}s. "
            f"Tickers screened: {len(intelligence.technical_signals)}*"
        )
        return "\n".join(report_parts)

    @staticmethod
    def _normalize_ticker_set(tickers: Optional[List[str]]) -> Set[str]:
        return {
            str(t).strip().upper()
            for t in (tickers or [])
            if str(t).strip()
        }


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
