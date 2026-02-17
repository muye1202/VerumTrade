# tradingagents/agents/discovery/intelligence/stage2_scoring.py
"""
Stage 2: Numeric Scoring & Filtering.

Pure computation — zero LLM calls.  Consumes Stage 1 enrichment scorecards,
applies hard filters (eliminate ~60-70%), computes a 5-factor weighted
composite score on survivors, and returns the top 8-12 candidates.

Hard Filters:
  - Price above 50-day SMA
  - Relative strength vs SPY above threshold
  - Average daily dollar volume > $5M
  - Not gapping down into earnings (last-day return >= -5%)

Composite Scoring (weighted):
  - Earnings surprise history (30%)
  - Technical momentum alignment (25%)
  - Options flow signal (20%)
  - Sector momentum (15%)
  - Short interest squeeze potential (10%)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import Stage1EnrichmentScorecard, Stage2ScoredCandidate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector ETF mapping — maps GICS-style sector names to their most liquid ETF.
# Used for sector momentum scoring.  yfinance `info["sector"]` returns these
# strings (or close variants).
# ---------------------------------------------------------------------------
SECTOR_ETF_MAP: Dict[str, str] = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Basic Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Communication": "XLC",
}


class Stage2Scorer:
    """
    Stage 2: hard-filter + composite scoring on Stage 1 enrichment output.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self._sector_roc_cache: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Progress callback
    # ------------------------------------------------------------------
    def _emit_progress(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Emit progress event to the discovery CLI logger if configured."""
        callback = self.config.get("discovery_progress_callback")
        if callback:
            try:
                callback(event, payload)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _settings(self) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "hard_filters": {
                "require_above_sma50": True,
                "min_rs_vs_spy_differential": -5.0,
                "min_avg_dollar_volume_20d": 5_000_000.0,
                "max_gap_down_pct": -5.0,
            },
            "weights": {
                "earnings_surprise": 0.30,
                "technical_momentum": 0.25,
                "options_flow": 0.20,
                "sector_momentum": 0.15,
                "short_squeeze": 0.10,
            },
            "output": {
                "min_candidates": 8,
                "max_candidates": 12,
            },
        }
        override = self.config.get("stage2_scoring", {})
        return {
            "hard_filters": {
                **defaults["hard_filters"],
                **override.get("hard_filters", {}),
            },
            "weights": {**defaults["weights"], **override.get("weights", {})},
            "output": {**defaults["output"], **override.get("output", {})},
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score_and_filter(
        self,
        scorecards: List[Stage1EnrichmentScorecard],
        trade_date: str,
    ) -> List[Stage2ScoredCandidate]:
        """
        Run Stage 2: hard filters → composite scoring → rank → top N.

        Args:
            scorecards: Stage 1 enrichment output (per-ticker data).
            trade_date: The analysis date (yyyy-mm-dd).

        Returns:
            Sorted list of top candidates (highest composite first).
        """
        if not scorecards:
            return []

        cfg = self._settings()

        # Emit start event
        self._emit_progress("stage2.start", {"total": len(scorecards), "trade_date": trade_date})

        # Reset per-run caches
        self._sector_roc_cache.clear()

        # Pre-fetch sector ROCs (one per unique sector, not per ticker)
        self._prefetch_sector_rocs(scorecards, trade_date)

        all_candidates: List[Stage2ScoredCandidate] = []
        for sc in scorecards:
            passed, fail_reasons = self._apply_hard_filters(sc, cfg)
            candidate = Stage2ScoredCandidate(
                ticker=sc.ticker,
                hard_filter_passed=passed,
                hard_filter_fail_reasons=fail_reasons,
                stage1_scorecard=sc,
            )
            if passed:
                self._compute_composite_score(candidate, sc, cfg)
            all_candidates.append(candidate)
            
            # Emit per-ticker progress
            self._emit_progress("stage2.ticker_done", {"ticker": sc.ticker, "ok": passed})

        passed_candidates = [c for c in all_candidates if c.hard_filter_passed]
        passed_candidates.sort(
            key=lambda c: c.composite_score, reverse=True,
        )

        max_n = int(cfg["output"]["max_candidates"])
        top = passed_candidates[:max_n]

        n_filtered = len(scorecards) - len(passed_candidates)
        pct = (n_filtered / len(scorecards) * 100.0) if scorecards else 0.0
        
        # Emit complete event
        self._emit_progress("stage2.complete", {
            "total": len(scorecards),
            "passed": len(passed_candidates),
            "trade_date": trade_date,
        })
        
        self.logger.info(
            f"Stage 2 complete: {len(scorecards)} in → "
            f"{n_filtered} filtered ({pct:.0f}%) → "
            f"{len(top)} candidates out"
        )
        return top

    # ------------------------------------------------------------------
    # Hard filters
    # ------------------------------------------------------------------
    def _apply_hard_filters(
        self,
        sc: Stage1EnrichmentScorecard,
        cfg: Dict[str, Any],
    ) -> tuple:
        """Return (passed: bool, fail_reasons: List[str])."""
        hf = cfg["hard_filters"]
        fail_reasons: List[str] = []

        # 1. Price above 50-day SMA
        if hf["require_above_sma50"] and sc.vs_sma50_pct <= 0:
            fail_reasons.append("price_below_sma50")

        # 2. Relative strength vs SPY
        if sc.rs_vs_spy_20d < hf["min_rs_vs_spy_differential"]:
            fail_reasons.append("rs_vs_spy_too_low")

        # 3. Average daily dollar volume > $5M
        if sc.avg_dollar_volume_20d < hf["min_avg_dollar_volume_20d"]:
            fail_reasons.append("insufficient_dollar_volume")

        # 4. Not gapping down into earnings
        #    Use roc_20d as a proxy; a large negative recent momentum
        #    combined with an earnings catalyst window signals a falling knife.
        #    A more precise check would use the 1-day return, but we only have
        #    20d ROC from Stage 1.  We approximate: if the stock is deeply
        #    negative on ROC and well below SMA50, treat it as a gap-down.
        if (
            sc.roc_20d < hf["max_gap_down_pct"]
            and sc.vs_sma50_pct < 0
        ):
            fail_reasons.append("gapping_down_into_earnings")

        return (len(fail_reasons) == 0), fail_reasons

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------
    def _compute_composite_score(
        self,
        candidate: Stage2ScoredCandidate,
        sc: Stage1EnrichmentScorecard,
        cfg: Dict[str, Any],
    ) -> None:
        """Compute all factor sub-scores and the weighted composite."""
        weights = cfg["weights"]

        candidate.earnings_surprise_score = self._score_earnings_surprise(sc)
        candidate.technical_momentum_score = self._score_technical_momentum(sc)
        candidate.options_flow_score = self._score_options_flow(sc)
        candidate.sector_momentum_score = self._score_sector_momentum(sc)
        candidate.short_squeeze_score = self._score_short_squeeze(sc)

        composite = (
            candidate.earnings_surprise_score * weights["earnings_surprise"]
            + candidate.technical_momentum_score * weights["technical_momentum"]
            + candidate.options_flow_score * weights["options_flow"]
            + candidate.sector_momentum_score * weights["sector_momentum"]
            + candidate.short_squeeze_score * weights["short_squeeze"]
        )
        candidate.composite_score = round(composite, 2)

    # ------------------------------------------------------------------
    # Individual factor scorers  (each returns 0-100)
    # ------------------------------------------------------------------
    @staticmethod
    def _score_earnings_surprise(sc: Stage1EnrichmentScorecard) -> float:
        """
        30% weight — Earnings surprise history (past 4Q beat rate).
        beat_rate_4q is 0-100 representing percentage of quarters beating.
        Direct map: 0% beats → 0, 100% beats → 100.
        """
        return _clamp(sc.earnings_beat_rate_4q, 0.0, 100.0)

    @staticmethod
    def _score_technical_momentum(sc: Stage1EnrichmentScorecard) -> float:
        """
        25% weight — Technical momentum alignment (ROC + trend + ADX).
        Blend of three sub-indicators normalised 0-100:
          - ROC 20d: [-20, +25] → [0, 100]
          - ADX: [10, 50] → [0, 100]
          - Price vs 50 SMA (%): [-10, +30] → [0, 100]
        """
        roc_norm = _normalize(sc.roc_20d, -20.0, 25.0)
        adx_norm = _normalize(sc.adx, 10.0, 50.0)
        sma_norm = _normalize(sc.vs_sma50_pct, -10.0, 30.0)
        return round(roc_norm * 0.45 + adx_norm * 0.30 + sma_norm * 0.25, 2)

    @staticmethod
    def _score_options_flow(sc: Stage1EnrichmentScorecard) -> float:
        """
        20% weight — Unusual call activity / smart money signal.
        options_unusual_score is typically 0-10 from Stage 1.
        Normalise to [0, 100].
        """
        return _normalize(sc.options_unusual_score, 0.0, 10.0)

    def _score_sector_momentum(self, sc: Stage1EnrichmentScorecard) -> float:
        """
        15% weight — Sector ETF relative performance vs SPY.
        Uses cached sector ROC values (pre-fetched at start of scoring run).
        """
        sector_roc = self._get_sector_roc(sc.ticker)
        # sector_roc is the sector ETF's 20d ROC minus SPY's 20d ROC.
        # Typical range: [-10, +10]
        return _normalize(sector_roc, -10.0, 10.0)

    @staticmethod
    def _score_short_squeeze(sc: Stage1EnrichmentScorecard) -> float:
        """
        10% weight — Short interest squeeze potential.
        Combines short_interest_pct_float and days_to_cover.
        Higher short interest + higher days-to-cover = more squeeze fuel.
        """
        # short_interest_pct_float: typical 0-50 (%, of float)
        si_norm = _normalize(sc.short_interest_pct_float, 0.0, 30.0)
        # days_to_cover: typical 0-10+
        dtc_norm = _normalize(sc.days_to_cover, 0.0, 8.0)
        return round(si_norm * 0.6 + dtc_norm * 0.4, 2)

    # ------------------------------------------------------------------
    # Sector momentum helpers
    # ------------------------------------------------------------------
    def _prefetch_sector_rocs(
        self,
        scorecards: List[Stage1EnrichmentScorecard],
        trade_date: str,
    ) -> None:
        """Pre-fetch sector ETF 20d ROC for all unique sectors in the universe."""
        tickers = [sc.ticker for sc in scorecards]
        sectors_needed: Dict[str, str] = {}  # ticker -> sector_etf

        for ticker in tickers:
            etf = self._ticker_to_sector_etf(ticker)
            if etf and etf not in self._sector_roc_cache:
                sectors_needed[etf] = etf

        for etf in sectors_needed:
            try:
                roc = self._fetch_etf_relative_roc(etf, trade_date)
                self._sector_roc_cache[etf] = roc
            except Exception as e:
                self.logger.debug(f"Sector ETF {etf} ROC fetch failed: {e}")
                self._sector_roc_cache[etf] = 0.0

    def _get_sector_roc(self, ticker: str) -> float:
        """Look up cached sector ETF relative ROC for a ticker."""
        etf = self._ticker_to_sector_etf(ticker)
        if etf and etf in self._sector_roc_cache:
            return self._sector_roc_cache[etf]
        return 0.0

    @staticmethod
    def _ticker_to_sector_etf(ticker: str) -> Optional[str]:
        """Map a ticker to its sector ETF via yfinance. Cached per-call."""
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector", "")
            return SECTOR_ETF_MAP.get(sector)
        except Exception:
            return None

    @staticmethod
    def _fetch_etf_relative_roc(etf_symbol: str, trade_date: str) -> float:
        """
        Compute 20-day ROC of a sector ETF minus SPY's 20-day ROC.

        Uses the same vendor interface as the rest of the pipeline.
        """
        from tradingagents.agents.discovery.intelligence.utils import (
            parse_price_volume_csv,
        )
        from datetime import datetime, timedelta
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=60)).strftime("%Y-%m-%d")

        def _roc_20d(symbol: str) -> float:
            raw_csv = route_to_vendor("get_stock_data", symbol, start_date, trade_date)
            prices, _ = parse_price_volume_csv(raw_csv)
            if len(prices) < 20:
                return 0.0
            return ((prices[-1] - prices[-20]) / prices[-20]) * 100.0

        etf_roc = _roc_20d(etf_symbol)
        spy_roc = _roc_20d("SPY")
        return etf_roc - spy_roc


# ---------------------------------------------------------------------------
# Pure utility functions
# ---------------------------------------------------------------------------

def _normalize(value: float, low: float, high: float) -> float:
    """Linearly normalise *value* from [low, high] to [0, 100], clamped."""
    if high <= low:
        return 50.0
    scaled = (value - low) / (high - low) * 100.0
    return _clamp(scaled, 0.0, 100.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
