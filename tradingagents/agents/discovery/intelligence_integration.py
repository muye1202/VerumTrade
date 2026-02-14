# tradingagents/agents/discovery/intelligence_integration.py
"""
Integration guide: How to wire IntelligenceScanner into StockDiscoveryGraph.

This replaces the monolithic StockRecommenderAgent.recommend() call with
a two-phase pipeline:
  Phase 1: IntelligenceScanner gathers structured data (3 parallel sub-agents)
  Phase 2: A lightweight synthesis LLM call ranks and selects from pre-filtered data

The key improvement: the synthesis LLM receives ~500 tokens of pre-ranked structured
data instead of ~8000 tokens of raw tool output. This puts the LLM in its sweet spot
(qualitative reasoning over structured inputs) instead of its failure mode
(extracting signal from massive unstructured context).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.discovery.intelligence_sub_agents import (
    IntelligenceScanner,
    IntelligenceResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Stage 2: Synthesis prompt - receives ONLY pre-filtered structured data
# =============================================================================

SYNTHESIS_SYSTEM_PROMPT = """You are scoring stock candidates for swing trading.

You receive pre-screened data from three independent analysis pipelines:
1. Sector momentum rankings
2. News catalyst signals
3. Technical breakout scores

Your job: synthesize across all three dimensions and select the 3-5 strongest candidates.

Scoring dimensions (score each 1-10):
- technical_setup: Trend strength, momentum, volume confirmation
- catalyst_quality: How specific, recent, and actionable is the catalyst?
- sector_alignment: Is the stock in a sector with positive momentum?
- risk_reward: Estimated upside vs downside potential

CRITICAL RULES:
- Multi-signal alignment (technical + catalyst + sector) is worth MORE than any single strong signal
- Reject stocks with catalyst but NO technical confirmation (news trap)
- Reject stocks with technicals but in a WEAK sector (swimming upstream)
- Prefer stocks where volume confirms price action (accumulation > distribution)
- Do NOT recommend tickers listed in EXCLUDED_TICKERS (existing portfolio holdings). Recommend only NEW tickers.

Return ONLY valid JSON:
{
  "rankings": [
    {
      "ticker": "NVDA",
      "scores": {
        "technical_setup": 8,
        "catalyst_quality": 9,
        "sector_alignment": 9,
        "risk_reward": 7
      },
      "composite": 8.3,
      "thesis": "AI capex cycle accelerating; breakout above resistance with volume",
      "key_risk": "Earnings in 2 weeks - event risk",
      "signal_alignment": "triple"
    }
  ],
  "market_regime": "trending | ranging | volatile",
  "pass_count": 4,
  "reject_count": 2
}

Where signal_alignment is:
- "triple": technical + catalyst + sector all positive
- "double": two of three positive
- "single": only one dimension positive (generally reject these)"""


class IntelligenceDrivenRecommender:
    """
    Replacement for StockRecommenderAgent that uses the three-sub-agent architecture.

    Instead of one monolithic LLM call that gathers data AND synthesizes,
    this class:
      1. Uses IntelligenceScanner for parallel data gathering (structured output)
      2. Filters and ranks programmatically (no LLM needed)
      3. Passes only pre-filtered candidates to a focused synthesis LLM call
      4. Extracts tickers from structured JSON (no regex needed)

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
        # Backward compatibility:
        # - Older callers pass `llm` only.
        # - New callers can split models by responsibility:
        #   quick_llm for Stage 1 scanners, deep_llm for Stage 2 synthesis.
        if deep_llm is None:
            deep_llm = llm
        if quick_llm is None:
            quick_llm = deep_llm
        if deep_llm is None:
            raise ValueError("IntelligenceDrivenRecommender requires an LLM instance")

        self.deep_llm = deep_llm
        self.quick_llm = quick_llm
        self.config = config or {}
        self.scanner = IntelligenceScanner(llm=self.quick_llm, config=config)
        self.screening_universe = screening_universe
        self.logger = logging.getLogger(self.__class__.__name__)

    def recommend(
        self,
        trade_date: str,
        max_iterations: int = 3,  # Kept for API compat; not used in new architecture
        excluded_tickers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Run the two-phase recommendation pipeline.

        Returns dict compatible with StockRecommenderAgent.recommend():
            - tickers: List[str]
            - report: str
            - raw_messages: list (empty for new pipeline)
            - iterations: int
        """
        # Phase 1: Parallel intelligence gathering
        self.logger.info(f"Phase 1: Intelligence gathering for {trade_date}")
        excluded_set = self._normalize_ticker_set(excluded_tickers)

        intelligence = self.scanner.scan_with_dynamic_universe(
            trade_date=trade_date,
            base_universe=self.screening_universe,
            excluded_tickers=sorted(excluded_set),
        )

        # Phase 2: Synthesis
        self.logger.info("Phase 2: Synthesis and ranking")

        synthesis_input = self._build_synthesis_input(intelligence, excluded_set)
        rankings = self._run_synthesis(synthesis_input, trade_date, sorted(excluded_set))

        # Hard filter even if the LLM ignores exclusion instructions
        rankings = [
            r
            for r in rankings
            if str(r.get("ticker", "")).strip().upper() not in excluded_set
        ]

        # Extract tickers (structured, no regex needed)
        tickers = [r["ticker"] for r in rankings[:5] if r.get("ticker")]

        # Build human-readable report from structured data
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
            # New fields (not in old API but useful for downstream)
            "intelligence": intelligence,
            "rankings": rankings,
            "excluded_tickers": sorted(excluded_set),
        }

    def _build_synthesis_input(
        self,
        intelligence: IntelligenceResult,
        excluded_tickers: Optional[Set[str]] = None,
    ) -> str:
        """
        Build a compact, structured input for the synthesis LLM.
        This is the key design point: the LLM receives ~500 tokens of
        pre-ranked data, not ~8000 tokens of raw tool output.
        """
        sections = []

        # Sector context (compact)
        if intelligence.sector_signals:
            sector_lines = []
            for s in intelligence.sector_signals[:6]:
                accel = "UP_ACCEL" if s.return_10d > s.return_30d / 3 else ""
                sector_lines.append(
                    f"  #{s.momentum_rank} {s.sector} ({s.etf}): "
                    f"30d={s.return_30d:+.1f}% 10d={s.return_10d:+.1f}% "
                    f"vs_SPY={s.relative_to_spy:+.1f}% {accel}"
                )
            sections.append("SECTOR MOMENTUM:\n" + "\n".join(sector_lines))

        # Catalysts (only high/medium actionability, positive sentiment)
        relevant_catalysts = [
            c
            for c in intelligence.catalyst_signals
            if c.sentiment_score > 0 and c.actionability in ("high", "medium")
        ]
        if relevant_catalysts:
            cat_lines = []
            for c in relevant_catalysts[:8]:
                cat_lines.append(
                    f"  {c.ticker or c.sector}: [{c.catalyst_type}] "
                    f"sent={c.sentiment_score:+.1f} act={c.actionability} "
                    f"({c.recency_days}d ago) - {c.headline[:80]}"
                )
            sections.append("CATALYSTS:\n" + "\n".join(cat_lines))

        # Technical candidates (top 10 by composite score)
        top_tech = intelligence.technical_signals[:10]
        if top_tech:
            tech_lines = []
            for t in top_tech:
                flags = []
                if t.vs_sma50_pct > 0 and t.vs_sma200_pct > 0:
                    flags.append("ABOVE_MAs")
                if t.adx > 25:
                    flags.append(f"ADX={t.adx:.0f}")
                if t.obv_trend == "accumulation":
                    flags.append("ACCUM")
                if t.relative_strength_vs_spy > 1.0:
                    flags.append(f"RS={t.relative_strength_vs_spy:.1f}")

                tech_lines.append(
                    f"  {t.ticker}: ${t.price:.2f} mom={t.momentum_20d:+.1f}% "
                    f"score={t.composite_score:.0f} [{', '.join(flags)}]"
                )
            sections.append("TECHNICAL CANDIDATES:\n" + "\n".join(tech_lines))

        # Multi-signal alignment (highlight these prominently)
        aligned = intelligence.tickers_with_multi_signal_alignment()
        if aligned:
            sections.append(f"MULTI-SIGNAL ALIGNED (catalyst + technical): {', '.join(aligned)}")

        if excluded_tickers:
            sections.append(
                "EXCLUDED_TICKERS (already in portfolio): "
                + ", ".join(sorted(excluded_tickers))
            )

        return "\n\n".join(sections)

    def _run_synthesis(
        self,
        synthesis_input: str,
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run the synthesis LLM call on pre-filtered data.
        Returns list of ranked candidates with scores.
        """
        excluded_block = (
            f"EXCLUDED_TICKERS: {', '.join(excluded_tickers)}"
            if excluded_tickers
            else "EXCLUDED_TICKERS: (none)"
        )

        try:
            result = self.deep_llm.invoke([
                SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"Date: {trade_date}\n\n{excluded_block}\n\n{synthesis_input}"
                ),
            ])
            content = result.content if hasattr(result, "content") else str(result)
            return self._parse_synthesis_response(content)
        except Exception as e:
            self.logger.warning(f"Synthesis LLM failed: {e}")
            return self._quant_fallback_ranking(
                synthesis_input,
                excluded_tickers=set(excluded_tickers or []),
            )

    def _parse_synthesis_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse structured JSON rankings from synthesis LLM."""
        text = response_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return []
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return []

        rankings = data.get("rankings", [])
        # Sort by composite score descending
        rankings.sort(key=lambda r: float(r.get("composite", 0)), reverse=True)
        return rankings

    def _quant_fallback_ranking(
        self,
        synthesis_input: str,
        excluded_tickers: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        If synthesis LLM fails, extract tickers from the structured input
        and rank by technical composite score.
        """
        # This is a simple fallback - in practice the structured data
        # from Phase 1 is already ranked, so we just need the top tickers.
        tickers = re.findall(r"\b([A-Z]{1,5})\b", synthesis_input)
        seen = set()
        unique = []
        skip = {"ABOVE", "MAs", "ADX", "ACCUM", "SPY", "SECTOR", "ACCEL"}
        excluded_set = {str(t).strip().upper() for t in (excluded_tickers or set())}
        for t in tickers:
            if t not in seen and t not in skip and len(t) <= 5:
                if t in excluded_set:
                    continue
                seen.add(t)
                unique.append({"ticker": t, "composite": 0, "thesis": "quantitative fallback"})
        return unique[:5]

    def _build_report(
        self,
        intelligence: IntelligenceResult,
        rankings: List[Dict[str, Any]],
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
    ) -> str:
        """Build a human-readable report from structured results."""
        report_parts = [f"# Stock Discovery Report - {trade_date}\n"]

        # Market context from sectors
        if intelligence.sector_signals:
            report_parts.append("## Sector Momentum")
            for s in intelligence.hot_sectors:
                report_parts.append(
                    f"- **{s.sector}** ({s.etf}): {s.return_30d:+.1f}% (30d), "
                    f"vs SPY {s.relative_to_spy:+.1f}%. {s.narrative}"
                )
            report_parts.append("")

        # Catalysts
        high_catalysts = intelligence.high_conviction_catalysts
        if high_catalysts:
            report_parts.append("## Key Catalysts")
            for c in high_catalysts[:5]:
                report_parts.append(f"- **{c.ticker}** [{c.catalyst_type}]: {c.headline}")
            report_parts.append("")

        # Rankings
        if rankings:
            report_parts.append("## Recommended Stocks\n")
            for i, r in enumerate(rankings[:5], 1):
                scores = r.get("scores", {})
                report_parts.append(
                    f"### {i}. **{r['ticker']}** - Composite: {r.get('composite', 'N/A')}/10"
                )
                if scores:
                    report_parts.append(
                        f"- Technical: {scores.get('technical_setup', '?')}/10 | "
                        f"Catalyst: {scores.get('catalyst_quality', '?')}/10 | "
                        f"Sector: {scores.get('sector_alignment', '?')}/10 | "
                        f"Risk/Reward: {scores.get('risk_reward', '?')}/10"
                    )
                if r.get("thesis"):
                    report_parts.append(f"- **Thesis:** {r['thesis']}")
                if r.get("key_risk"):
                    report_parts.append(f"- **Key Risk:** {r['key_risk']}")
                alignment = r.get("signal_alignment", "")
                if alignment:
                    report_parts.append(f"- **Signal Alignment:** {alignment}")
                report_parts.append("")

        # Summary line
        tickers = [r["ticker"] for r in rankings[:5] if r.get("ticker")]
        report_parts.append(f"## Top Pick Summary\n{', '.join(tickers)}")

        if excluded_tickers:
            report_parts.append(
                "\n## Excluded Existing Positions\n" + ", ".join(excluded_tickers)
            )

        # Scan metadata
        report_parts.append(
            f"\n---\n*Scan completed in {intelligence.scan_duration_secs}s. "
            f"Sectors ranked: {len(intelligence.sector_signals)}, "
            f"Catalysts found: {len(intelligence.catalyst_signals)}, "
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


# =============================================================================
# Drop-in replacement for StockDiscoveryGraph
# =============================================================================

def patch_discovery_graph_with_intelligence(discovery_graph) -> None:
    """
    Monkey-patch an existing StockDiscoveryGraph instance to use the
    IntelligenceDrivenRecommender instead of the original StockRecommenderAgent.

    Usage:
        graph = StockDiscoveryGraph(config=config)
        patch_discovery_graph_with_intelligence(graph)
        result = graph.run_discovery(trade_date="2025-06-15")
        # Now uses the three-sub-agent architecture internally
    """
    discovery_graph.recommender = IntelligenceDrivenRecommender(
        deep_llm=getattr(discovery_graph, "deep_llm", getattr(discovery_graph, "llm", None)),
        quick_llm=getattr(discovery_graph, "quick_llm", getattr(discovery_graph, "llm", None)),
        config=discovery_graph.config,
    )
    logger.info("StockDiscoveryGraph patched with IntelligenceDrivenRecommender")
