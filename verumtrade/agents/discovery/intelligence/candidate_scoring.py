from __future__ import annotations
"""
Candidate Scoring (Stage 2):
Final scoring engine that applies strict structural exclusions and calculates a composite score for high-conviction trade candidates.
"""
# verumtrade/agents/discovery/intelligence/candidate_scoring.py
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


import logging
from typing import Any, Dict, List, Optional, Tuple

from .pipeline_models import Stage1EnrichmentScorecard, Stage2ScoredCandidate
from .pipeline_cache import load_cache_value, save_cache_value, stable_key
from .pipeline_utils import compute_return_pct

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
        self._sector_etf_cache: Dict[str, Optional[str]] = {}
        self._last_run_metadata: Dict[str, Any] = {
            "filter_relaxations_applied": [],
            "passed_candidates": 0,
            "total_scorecards": 0,
            "data_quality_summary": {},
            "breadth_context": {},
        }

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
            "quality": {
                "fail_on_data_flags": False,
                "penalty_per_flag": 2.5,
                "max_penalty": 15.0,
            },
            "noise": {
                "enabled": True,
                "rv_shock_ratio_threshold": 1.8,
                "allow_rv_shock_if_breakout_top_decile": True,
                "whipsaw_penalty_start": 4,
                "whipsaw_penalty_per_flip": 1.5,
                "whipsaw_penalty_max": 12.0,
            },
            "breadth": {
                "enabled": True,
                "weak_pct_above_50dma": 35.0,
                "weak_pct_above_200dma": 45.0,
                "weak_min_new_high_minus_new_low": -5.0,
                "weak_mode_min_trend_quality": 55.0,
                "weak_mode_min_roc_20d": 0.0,
            },
            "weights": {
                "earnings_surprise": 0.15,
                "technical_momentum": 0.20,
                "options_flow": 0.10,
                "sector_momentum": 0.05,
                "short_squeeze": 0.05,
                "estimate_revision": 0.20,
                "breakout_persistence": 0.15,
                "accum_distrib": 0.10,
            },
            "output": {
                "min_candidates": 8,
                "max_candidates": 12,
                "min_candidates_relaxation": [
                    "loosen_rs_floor",
                    "disable_sma50_requirement",
                    "loosen_gap_down",
                    "lower_dollar_volume_floor",
                ],
            },
        }
        override = self.config.get("stage2_scoring", {})
        return {
            "hard_filters": {
                **defaults["hard_filters"],
                **override.get("hard_filters", {}),
            },
            "quality": {
                **defaults["quality"],
                **override.get("quality", {}),
            },
            "noise": {
                **defaults["noise"],
                **override.get("noise", {}),
            },
            "breadth": {
                **defaults["breadth"],
                **override.get("breadth", {}),
            },
            "weights": {**defaults["weights"], **override.get("weights", {})},
            "output": {**defaults["output"], **override.get("output", {})},
        }

    def get_last_run_metadata(self) -> Dict[str, Any]:
        return dict(self._last_run_metadata or {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score_and_filter(
        self,
        scorecards: List[Stage1EnrichmentScorecard],
        trade_date: str,
        weight_tilts: Optional[Dict[str, float]] = None,
        hard_filter_overrides: Optional[Dict[str, Any]] = None,
        sector_weight_multipliers: Optional[Dict[str, float]] = None,
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
        base_hard_filters = self._effective_hard_filters(
            cfg["hard_filters"],
            hard_filter_overrides or {},
        )
        cfg["hard_filters"] = dict(base_hard_filters)
        relaxations_applied: List[str] = []

        # Emit start event
        self._emit_progress("stage2.start", {"total": len(scorecards), "trade_date": trade_date})

        # Reset per-run caches
        self._sector_roc_cache.clear()
        self._sector_etf_cache.clear()

        # Pre-fetch sector ROCs (one per unique sector, not per ticker)
        self._prefetch_sector_rocs(scorecards, trade_date)
        run_context = self._build_run_context(scorecards, cfg)

        _, passed_candidates = self._evaluate_candidates(
            scorecards=scorecards,
            cfg=cfg,
            run_context=run_context,
            weight_tilts=weight_tilts,
            sector_weight_multipliers=sector_weight_multipliers,
            emit_progress=True,
        )

        min_n = max(1, int(cfg["output"].get("min_candidates", 1)))
        relax_rules = cfg["output"].get("min_candidates_relaxation") or []
        working_filters = dict(cfg["hard_filters"])
        for rule_name in [str(x).strip() for x in relax_rules if str(x).strip()]:
            if len(passed_candidates) >= min_n:
                break
            working_filters, applied_label = self._apply_min_candidate_relaxation(
                working_filters,
                rule_name,
            )
            if not applied_label:
                continue
            relaxations_applied.append(applied_label)
            cfg["hard_filters"] = dict(working_filters)
            _, passed_candidates = self._evaluate_candidates(
                scorecards=scorecards,
                cfg=cfg,
                run_context=run_context,
                weight_tilts=weight_tilts,
                sector_weight_multipliers=sector_weight_multipliers,
                emit_progress=False,
            )

        max_n = int(cfg["output"]["max_candidates"])
        top = passed_candidates[:max_n]

        n_filtered = len(scorecards) - len(passed_candidates)
        pct = (n_filtered / len(scorecards) * 100.0) if scorecards else 0.0
        data_quality_summary = self._data_quality_summary(scorecards)
        self._last_run_metadata = {
            "filter_relaxations_applied": list(relaxations_applied),
            "passed_candidates": len(passed_candidates),
            "total_scorecards": len(scorecards),
            "data_quality_summary": data_quality_summary,
            "hard_filters_initial": dict(base_hard_filters),
            "hard_filters_final": dict(cfg["hard_filters"]),
            "breadth_context": dict(run_context.get("breadth") or {}),
        }
        
        # Emit complete event
        self._emit_progress("stage2.complete", {
            "total": len(scorecards),
            "passed": len(passed_candidates),
            "trade_date": trade_date,
        })
        
        self.logger.info(
            f"Stage 2 complete: {len(scorecards)} in -> "
            f"{n_filtered} filtered ({pct:.0f}%) -> "
            f"{len(top)} candidates out "
            f"(relaxations={','.join(relaxations_applied) if relaxations_applied else 'none'})"
        )
        return top

    def _evaluate_candidates(
        self,
        *,
        scorecards: List[Stage1EnrichmentScorecard],
        cfg: Dict[str, Any],
        run_context: Dict[str, Any],
        weight_tilts: Optional[Dict[str, float]],
        sector_weight_multipliers: Optional[Dict[str, float]],
        emit_progress: bool,
    ) -> Tuple[List[Stage2ScoredCandidate], List[Stage2ScoredCandidate]]:
        all_candidates: List[Stage2ScoredCandidate] = []
        for sc in scorecards:
            passed, fail_reasons = self._apply_hard_filters(sc, cfg, run_context=run_context)
            candidate = Stage2ScoredCandidate(
                ticker=sc.ticker,
                hard_filter_passed=passed,
                hard_filter_fail_reasons=fail_reasons,
                stage1_scorecard=sc,
            )
            if passed:
                self._compute_composite_score(
                    candidate,
                    sc,
                    cfg,
                    run_context=run_context,
                    weight_tilts=weight_tilts,
                    sector_weight_multipliers=sector_weight_multipliers,
                )
            all_candidates.append(candidate)
            if emit_progress:
                self._emit_progress("stage2.ticker_done", {"ticker": sc.ticker, "ok": passed})

        passed_candidates = [c for c in all_candidates if c.hard_filter_passed]
        passed_candidates.sort(key=lambda c: c.composite_score, reverse=True)
        return all_candidates, passed_candidates

    @staticmethod
    def _apply_min_candidate_relaxation(
        hard_filters: Dict[str, Any],
        rule_name: str,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        out = dict(hard_filters)
        rule = str(rule_name or "").strip().lower()
        if rule == "loosen_rs_floor":
            current = float(out.get("min_rs_vs_spy_differential", -5.0))
            updated = max(-15.0, current - 2.0)
            if updated != current:
                out["min_rs_vs_spy_differential"] = updated
                return out, "loosen_rs_floor"
            return out, None
        if rule == "disable_sma50_requirement":
            if bool(out.get("require_above_sma50", True)):
                out["require_above_sma50"] = False
                return out, "disable_sma50_requirement"
            return out, None
        if rule == "loosen_gap_down":
            current = float(out.get("max_gap_down_pct", -5.0))
            updated = max(-15.0, current - 2.5)
            if updated != current:
                out["max_gap_down_pct"] = updated
                return out, "loosen_gap_down"
            return out, None
        if rule == "lower_dollar_volume_floor":
            current = float(out.get("min_avg_dollar_volume_20d", 5_000_000.0))
            updated = max(2_000_000.0, current * 0.8)
            if abs(updated - current) > 1e-9:
                out["min_avg_dollar_volume_20d"] = updated
                return out, "lower_dollar_volume_floor"
            return out, None
        return out, None

    @staticmethod
    def _data_quality_summary(scorecards: List[Stage1EnrichmentScorecard]) -> Dict[str, Any]:
        total = len(scorecards)
        flagged = 0
        flag_counts: Dict[str, int] = {}
        for sc in scorecards:
            flags = list(sc.data_quality_flags or [])
            if flags:
                flagged += 1
            for flag in flags:
                key = str(flag)
                flag_counts[key] = int(flag_counts.get(key, 0)) + 1
        return {
            "total": total,
            "flagged": flagged,
            "flagged_pct": round((flagged / total) * 100.0, 2) if total else 0.0,
            "missingness_pct": round((flagged / total) * 100.0, 2) if total else 0.0,
            "flag_counts": flag_counts,
        }

    @staticmethod
    def _build_run_context(
        scorecards: List[Stage1EnrichmentScorecard],
        cfg: Dict[str, Any],
    ) -> Dict[str, Any]:
        noise_cfg = dict(cfg.get("noise") or {})
        breadth_cfg = dict(cfg.get("breadth") or {})
        breakout_vals = sorted(
            float(max(0.0, getattr(sc, "breakout_efficiency", 0.0)))
            for sc in scorecards
        )
        breakout_top_decile = 0.0
        if breakout_vals:
            idx = int(max(0, min(len(breakout_vals) - 1, len(breakout_vals) * 0.9)))
            breakout_top_decile = float(breakout_vals[idx])

        total = float(max(1, len(scorecards)))
        above_50 = sum(1 for sc in scorecards if float(sc.vs_sma50_pct) > 0.0)
        above_200 = sum(1 for sc in scorecards if float(sc.vs_sma200_pct) > 0.0)
        new_high = sum(
            1 for sc in scorecards
            if float(getattr(sc, "breakout_efficiency", 0.0)) > 0.0 or float(sc.roc_20d) >= 5.0
        )
        new_low = sum(
            1 for sc in scorecards
            if float(sc.roc_20d) <= -5.0 and float(sc.vs_sma50_pct) < 0.0
        )
        pct_above_50 = (above_50 / total) * 100.0
        pct_above_200 = (above_200 / total) * 100.0
        nh_nl = ((new_high - new_low) / total) * 100.0
        sufficient_sample = len(scorecards) >= 25
        weak_breadth = False
        if bool(breadth_cfg.get("enabled", True)) and sufficient_sample:
            weak_breadth = bool(
                pct_above_50 < float(breadth_cfg.get("weak_pct_above_50dma", 35.0))
                or pct_above_200 < float(breadth_cfg.get("weak_pct_above_200dma", 45.0))
                or nh_nl < float(breadth_cfg.get("weak_min_new_high_minus_new_low", -5.0))
            )

        return {
            "noise": {
                "enabled": bool(noise_cfg.get("enabled", True)),
                "breakout_top_decile": breakout_top_decile,
            },
            "breadth": {
                "pct_above_50dma": round(pct_above_50, 2),
                "pct_above_200dma": round(pct_above_200, 2),
                "new_high_minus_new_low_proxy_pct": round(nh_nl, 2),
                "sufficient_sample": sufficient_sample,
                "weak_breadth": weak_breadth,
            },
        }

    # ------------------------------------------------------------------
    # Hard filters
    # ------------------------------------------------------------------
    def _apply_hard_filters(
        self,
        sc: Stage1EnrichmentScorecard,
        cfg: Dict[str, Any],
        run_context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Return (passed: bool, fail_reasons: List[str])."""
        hf = cfg["hard_filters"]
        quality_cfg = cfg.get("quality") or {}
        noise_cfg = cfg.get("noise") or {}
        breadth_cfg = cfg.get("breadth") or {}
        context = dict(run_context or {})
        breadth_state = dict(context.get("breadth") or {})
        noise_state = dict(context.get("noise") or {})
        fail_reasons: List[str] = []

        if bool(quality_cfg.get("fail_on_data_flags", False)) and list(sc.data_quality_flags or []):
            fail_reasons.append("data_quality_flags")

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

        # Anti-noise gate: suppress volatility shocks unless breakout is exceptional.
        if bool(noise_cfg.get("enabled", True)):
            rv5 = float(getattr(sc, "rv5_pct", 0.0))
            rv20 = float(getattr(sc, "rv20_pct", 0.0))
            if rv20 > 0.0:
                ratio = rv5 / rv20
                allow_top_breakout = bool(noise_cfg.get("allow_rv_shock_if_breakout_top_decile", True))
                breakout_cutoff = float(noise_state.get("breakout_top_decile", 0.0))
                breakout_value = float(getattr(sc, "breakout_efficiency", 0.0))
                if ratio > float(noise_cfg.get("rv_shock_ratio_threshold", 1.8)):
                    allow_exception = bool(
                        allow_top_breakout
                        and breakout_cutoff > 0.0
                        and breakout_value >= breakout_cutoff
                    )
                    if not allow_exception:
                        fail_reasons.append("rv_shock_noise")

        # Breadth-aware gating: require stronger momentum quality when breadth is weak.
        if bool(breadth_cfg.get("enabled", True)) and bool(breadth_state.get("weak_breadth", False)):
            if float(getattr(sc, "trend_quality_score", 0.0)) < float(
                breadth_cfg.get("weak_mode_min_trend_quality", 55.0)
            ):
                fail_reasons.append("weak_breadth_trend_quality")
            if float(sc.roc_20d) < float(breadth_cfg.get("weak_mode_min_roc_20d", 0.0)):
                fail_reasons.append("weak_breadth_roc_gate")

        return (len(fail_reasons) == 0), fail_reasons

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------
    def _compute_composite_score(
        self,
        candidate: Stage2ScoredCandidate,
        sc: Stage1EnrichmentScorecard,
        cfg: Dict[str, Any],
        run_context: Optional[Dict[str, Any]] = None,
        weight_tilts: Optional[Dict[str, float]] = None,
        sector_weight_multipliers: Optional[Dict[str, float]] = None,
    ) -> None:
        """Compute all factor sub-scores and the weighted composite."""
        weights = self._effective_weights(cfg["weights"], weight_tilts or {})

        candidate.earnings_surprise_score = self._score_earnings_surprise(sc)
        candidate.technical_momentum_score = self._score_technical_momentum(sc)
        candidate.options_flow_score = self._score_options_flow(sc)
        candidate.sector_momentum_score = self._score_sector_momentum(sc)
        candidate.short_squeeze_score = self._score_short_squeeze(sc)
        candidate.estimate_revision_score = self._score_estimate_revision(sc)
        candidate.breakout_persistence_score = self._score_breakout_persistence(sc)
        candidate.accum_distrib_score = self._score_accum_distrib(sc)

        composite = (
            candidate.earnings_surprise_score * weights["earnings_surprise"]
            + candidate.technical_momentum_score * weights["technical_momentum"]
            + candidate.options_flow_score * weights["options_flow"]
            + candidate.sector_momentum_score * weights["sector_momentum"]
            + candidate.short_squeeze_score * weights["short_squeeze"]
            + candidate.estimate_revision_score * weights["estimate_revision"]
            + candidate.breakout_persistence_score * weights["breakout_persistence"]
            + candidate.accum_distrib_score * weights["accum_distrib"]
        )
        quality_cfg = cfg.get("quality") or {}
        noise_cfg = cfg.get("noise") or {}
        quality_penalty = 0.0
        if list(sc.data_quality_flags or []):
            try:
                per_flag = float(quality_cfg.get("penalty_per_flag", 2.5))
                max_penalty = float(quality_cfg.get("max_penalty", 15.0))
                quality_penalty = min(max_penalty, max(0.0, per_flag) * len(sc.data_quality_flags))
            except Exception:
                quality_penalty = 0.0

        whipsaw_penalty = 0.0
        if bool(noise_cfg.get("enabled", True)):
            flips = int(getattr(sc, "whipsaw_count_20", 0))
            start = int(noise_cfg.get("whipsaw_penalty_start", 4))
            if flips > start:
                whipsaw_penalty = min(
                    float(noise_cfg.get("whipsaw_penalty_max", 12.0)),
                    float(flips - start) * float(noise_cfg.get("whipsaw_penalty_per_flip", 1.5)),
                )

        sector_multiplier = self._sector_multiplier_for_ticker(
            sc.ticker,
            sector_weight_multipliers or {},
        )
        adjusted = max(0.0, (composite - quality_penalty - whipsaw_penalty)) * sector_multiplier
        candidate.composite_score = round(_clamp(adjusted, 0.0, 100.0), 2)

    @staticmethod
    def _effective_weights(
        base_weights: Dict[str, float],
        weight_tilts: Dict[str, float],
    ) -> Dict[str, float]:
        key_map = {
            "earnings_surprise": "earnings_surprise",
            "technical_momentum": "technical_momentum",
            "options_flow": "options_flow",
            "sector_momentum": "sector_momentum",
            "short_squeeze": "short_squeeze",
            "estimate_revision": "estimate_revision",
            "breakout_persistence": "breakout_persistence",
            "accum_distrib": "accum_distrib",
        }
        adjusted: Dict[str, float] = {}
        for key, base in base_weights.items():
            tilt_key = key_map.get(key, key)
            raw_tilt = weight_tilts.get(tilt_key, 1.0)
            try:
                tilt = float(raw_tilt)
            except Exception:
                tilt = 1.0
            tilt = max(0.5, min(1.5, tilt))
            adjusted[key] = float(base) * tilt

        total = sum(adjusted.values())
        if total <= 0:
            return dict(base_weights)
        return {k: (v / total) for k, v in adjusted.items()}

    @staticmethod
    def _effective_hard_filters(
        base_filters: Dict[str, Any],
        hard_filter_overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        out = dict(base_filters)
        if "require_above_sma50" in hard_filter_overrides:
            out["require_above_sma50"] = bool(hard_filter_overrides.get("require_above_sma50"))
        if "min_rs_vs_spy_differential" in hard_filter_overrides:
            try:
                out["min_rs_vs_spy_differential"] = float(
                    hard_filter_overrides.get("min_rs_vs_spy_differential")
                )
            except Exception:
                pass
        if "min_avg_dollar_volume_20d" in hard_filter_overrides:
            try:
                out["min_avg_dollar_volume_20d"] = float(
                    hard_filter_overrides.get("min_avg_dollar_volume_20d")
                )
            except Exception:
                pass
        if "max_gap_down_pct" in hard_filter_overrides:
            try:
                out["max_gap_down_pct"] = float(hard_filter_overrides.get("max_gap_down_pct"))
            except Exception:
                pass
        return out

    def _sector_multiplier_for_ticker(
        self,
        ticker: str,
        sector_weight_multipliers: Dict[str, float],
    ) -> float:
        if not sector_weight_multipliers:
            return 1.0
        etf = self._ticker_to_sector_etf(ticker)
        if not etf:
            return 1.0
        raw = sector_weight_multipliers.get(etf, 1.0)
        try:
            f = float(raw)
        except Exception:
            return 1.0
        return _clamp(f, 0.5, 1.5)

    # ------------------------------------------------------------------
    # Individual factor scorers  (each returns 0-100)
    # ------------------------------------------------------------------
    @staticmethod
    def _score_earnings_surprise(sc: Stage1EnrichmentScorecard) -> float:
        """15% weight — Earnings surprise history + magnitude trend."""
        beat_rate_norm = _clamp(sc.earnings_beat_rate_4q, 0.0, 100.0)
        # Slope ranges roughly from -20 to +20 per quarter
        trend_norm = _normalize(sc.earnings_surprise_trend_slope, -10.0, 15.0)
        return round(beat_rate_norm * 0.50 + trend_norm * 0.50, 2)

    @staticmethod
    def _score_estimate_revision(sc: Stage1EnrichmentScorecard) -> float:
        """
        20% weight — Estimate revision momentum.
        """
        breadth_norm = _normalize(sc.eps_revision_breadth_30d, 0.0, 100.0)
        magnitude_norm = _normalize(sc.eps_revision_magnitude_30d, -10.0, 20.0)
        revenue_bonus = 100.0 if sc.revenue_revision_direction > 0 else 0.0
        return round(breadth_norm * 0.40 + magnitude_norm * 0.40 + revenue_bonus * 0.20, 2)

    @staticmethod
    def _score_technical_momentum(sc: Stage1EnrichmentScorecard) -> float:
        """
        20% weight — Technical momentum with multi-timeframe alignment.
        """
        roc_norm = _normalize(sc.roc_20d, -20.0, 25.0)
        adx_norm = _normalize(sc.adx, 10.0, 50.0)
        sma_norm = _normalize(sc.vs_sma50_pct, -10.0, 30.0)
        alignment_norm = sc.momentum_alignment_score / 100.0 * 100.0  # already 0-100
        return round(
            roc_norm * 0.30 +
            adx_norm * 0.20 +
            sma_norm * 0.15 +
            alignment_norm * 0.35,
            2
        )

    @staticmethod
    def _score_breakout_persistence(sc: Stage1EnrichmentScorecard) -> float:
        """15% weight — New high proximity and breakout persistence."""
        # Distance from 52w high: 0% gap = 100, -20% = 0
        high_proximity = _normalize(sc.distance_from_52w_high_pct, -20.0, 0.0)
        # New high count: 0/20 = 0, 15/20 = 100
        new_high_norm = _normalize(sc.new_high_count_20d, 0.0, 15.0)
        # Persistence: 0 days = 0, 10+ days = 100
        persistence_norm = _normalize(sc.breakout_persistence_days, 0.0, 10.0)
        return round(high_proximity * 0.40 + new_high_norm * 0.30 + persistence_norm * 0.30, 2)

    @staticmethod
    def _score_accum_distrib(sc: Stage1EnrichmentScorecard) -> float:
        """10% weight — Accumulation/distribution ratio."""
        # Ratio 0.5 (more distribution) → 0, ratio 3.0+ → 100
        return round(_normalize(sc.accum_distrib_ratio_20d, 0.5, 3.0), 2)

    @staticmethod
    def _score_options_flow(sc: Stage1EnrichmentScorecard) -> float:
        """
        20% weight — Unusual call activity / smart money signal.
        options_unusual_score is scaled 0-100 in Stage 1.
        Normalise to [0, 100].
        """
        return _normalize(sc.options_unusual_score, 0.0, 100.0)

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

    def _sector_cache_cfg(self) -> Dict[str, Any]:
        base = {
            "enabled": True,
            "ttl_hours": 24 * 7,
            "dir": None,
            "force_refresh": False,
        }
        cfg = dict((self.config.get("discovery") or {}).get("feature_matrix") or {})
        if "cache_ttl_hours" in cfg and "ttl_hours" not in cfg:
            try:
                cfg["ttl_hours"] = int(cfg.get("cache_ttl_hours"))
            except Exception:
                pass
        return {**base, **cfg}

    def _ticker_to_sector_etf(self, ticker: str) -> Optional[str]:
        """Map a ticker to its sector ETF via yfinance with disk+memory cache."""
        key_ticker = str(ticker or "").strip().upper()
        if not key_ticker:
            return None
        if key_ticker in self._sector_etf_cache:
            return self._sector_etf_cache[key_ticker]

        cache_key = stable_key({
            "type": "stage2_ticker_sector_etf",
            "ticker": key_ticker,
        })
        cached, hit = load_cache_value(
            namespace="stage2_ticker_sector_etf",
            key=cache_key,
            cache_config=self._sector_cache_cfg(),
        )
        if hit:
            etf_cached = str(cached).strip().upper() if isinstance(cached, str) else ""
            out_cached = etf_cached or None
            self._sector_etf_cache[key_ticker] = out_cached
            return out_cached

        etf: Optional[str] = None
        try:
            import yfinance as yf
            info = yf.Ticker(key_ticker).info or {}
            sector = info.get("sector", "")
            etf = SECTOR_ETF_MAP.get(sector)
        except Exception:
            etf = None

        save_cache_value(
            namespace="stage2_ticker_sector_etf",
            key=cache_key,
            value=(etf or ""),
            cache_config=self._sector_cache_cfg(),
        )
        self._sector_etf_cache[key_ticker] = etf
        return etf

    @staticmethod
    def _fetch_etf_relative_roc(etf_symbol: str, trade_date: str) -> float:
        """
        Compute 20-day ROC of a sector ETF minus SPY's 20-day ROC.

        Uses the same vendor interface as the rest of the pipeline.
        """
        from verumtrade.agents.discovery.intelligence.pipeline_utils import (
            parse_price_volume_csv,
        )
        from datetime import datetime, timedelta
        from verumtrade.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=60)).strftime("%Y-%m-%d")

        def _roc_20d(symbol: str) -> float:
            raw_csv = route_to_vendor("get_stock_data", symbol, start_date, trade_date)
            prices, _ = parse_price_volume_csv(raw_csv)
            ret = compute_return_pct(prices, 20)
            if ret is None:
                return 0.0
            return float(ret)

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
