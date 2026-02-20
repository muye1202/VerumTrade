from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any, Tuple

from .models import IntelligenceResult
from .momentum_anomaly_scans import MomentumAnomalyScanner
from .pre_stage0_intelligence import PreStage0IntelligenceBuilder
from .pre_stage0_llm import build_llm_bias_profile
from .stage1_enrichment import Stage1BatchEnricher
from .stage2_scoring import Stage2Scorer
from .technical_momentum import TechnicalMomentumScanner


class IntelligenceScanner:
    """
    Top-level discovery orchestrator for Stage 0 → Stage 1 → Stage 2 pipeline.

    Supports three discovery tracks:
      - ``"enricher"`` (default): Stage 1 enrichment → Stage 2 scoring
      - ``"anomaly_scan"``: Track B short-term momentum anomaly scans
      - ``"dual_track"``: Both tracks run together; results merged with convergence bonus
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.technical_scanner = TechnicalMomentumScanner(llm=llm, config=config)
        self.stage1_enricher = Stage1BatchEnricher(config=config)
        self.stage2_scorer = Stage2Scorer(config=config)
        self.anomaly_scanner = MomentumAnomalyScanner(config=config)
        self.pre_stage0_builder = PreStage0IntelligenceBuilder(config=config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def _pre_stage0_cache_cfg(self, ttl_hours: int) -> Dict[str, Any]:
        base_cfg = {
            "enabled": True,
            "ttl_hours": int(ttl_hours),
            "force_refresh": False,
            "dir": None,
        }
        numeric_cache = (
            (self.config.get("numeric_filter") or {}).get("stage0_cache", {}) or {}
        )
        return {**base_cfg, **numeric_cache}

    @staticmethod
    def _cap_universe(universe: List[str], max_tickers: int) -> List[str]:
        if max_tickers <= 0:
            return []
        deduped: List[str] = []
        seen = set()
        for item in universe:
            ticker = str(item).strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            deduped.append(ticker)
        return deduped[:max_tickers]

    def _order_universe_by_sector_weights(
        self,
        universe: List[str],
        sector_weights: Dict[str, float],
    ) -> List[str]:
        if not sector_weights:
            return sorted({str(t).strip().upper() for t in universe if str(t).strip()})
        try:
            all_neutral = all(abs(float(v) - 1.0) < 1e-9 for v in sector_weights.values())
        except Exception:
            all_neutral = False
        if all_neutral:
            return sorted({str(t).strip().upper() for t in universe if str(t).strip()})

        ordered: List[Tuple[float, str]] = []
        for ticker in sorted({str(t).strip().upper() for t in universe if str(t).strip()}):
            etf = self.stage2_scorer._ticker_to_sector_etf(ticker)
            try:
                multiplier = float(sector_weights.get(str(etf or "").upper(), 1.0))
            except Exception:
                multiplier = 1.0
            ordered.append((multiplier, ticker))
        ordered.sort(key=lambda x: (-x[0], x[1]))
        return [ticker for _, ticker in ordered]

    def _dual_track_universe_split(
        self,
        ordered_universe: List[str],
        allocation: Dict[str, Any],
    ) -> Tuple[List[str], List[str]]:
        max_cfg = (allocation.get("max_tickers") or {})
        split_cfg = (allocation.get("dual_track_split") or {})

        dual_total = int(max_cfg.get("dual_track_total", len(ordered_universe) or 300))
        capped = ordered_universe[:max(0, dual_total)]
        if not capped:
            return [], []

        try:
            enricher_ratio = float(split_cfg.get("enricher", 0.5))
        except Exception:
            enricher_ratio = 0.5
        enricher_ratio = max(0.1, min(0.9, enricher_ratio))

        enricher_n = max(1, int(round(len(capped) * enricher_ratio)))
        anomaly_n = max(1, len(capped) - enricher_n)
        if enricher_n + anomaly_n > len(capped):
            anomaly_n = len(capped) - enricher_n
        if anomaly_n <= 0:
            anomaly_n = 1
            enricher_n = max(1, len(capped) - 1)

        track_a = capped[:enricher_n]
        track_b = capped[-anomaly_n:]
        return track_a, track_b

    def run_pre_stage0_intelligence(
        self,
        trade_date: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        snapshot, availability = self.pre_stage0_builder.build(trade_date=trade_date)
        cache_metrics = dict(snapshot.get("cache_metrics") or {})
        bias = build_llm_bias_profile(
            llm=self.llm,
            trade_date=trade_date,
            snapshot=snapshot,
            cache_config=self._pre_stage0_cache_cfg(ttl_hours=12),
            metrics=cache_metrics,
        )
        snapshot["cache_metrics"] = cache_metrics
        return snapshot, bias, availability

    def scan_with_prefilter_universe(
        self,
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
        discovery_track: str = "anomaly_scan",
    ) -> IntelligenceResult:
        import time

        start_time = time.time()
        excluded_set = {
            str(t).strip().upper()
            for t in (excluded_tickers or [])
            if str(t).strip()
        }

        pre_stage0_snapshot, llm_bias_profile, indicator_availability = self.run_pre_stage0_intelligence(
            trade_date=trade_date
        )
        policy = dict(llm_bias_profile.get("policy") or {})
        universe_policy = dict(policy.get("universe") or {})
        scoring_policy = dict(policy.get("scoring") or {})
        anomaly_policy = dict(policy.get("anomaly_scan") or {})
        stage0_overrides = dict(llm_bias_profile.get("stage0_overrides") or {})
        stage2_weight_tilts = dict(llm_bias_profile.get("stage2_weight_tilts") or {})
        stage2_hard_filter_overrides = dict(scoring_policy.get("stage2_hard_filter_overrides") or {})
        sector_weight_multipliers = dict(universe_policy.get("sector_weights") or {})
        allocation_policy = dict(universe_policy.get("allocation") or {})

        # Stage 0: prefilter pipeline (tradeable US equities -> ADV -> earnings).
        try:
            prefiltered_universe = self.technical_scanner.build_numeric_universe(
                trade_date,
                excluded_tickers=sorted(excluded_set),
                stage0_overrides=stage0_overrides,
            )
        except TypeError:
            prefiltered_universe = self.technical_scanner.build_numeric_universe(trade_date)
            if excluded_set:
                prefiltered_universe = [
                    t for t in prefiltered_universe
                    if str(t).strip().upper() not in excluded_set
                ]

        # ----- Track routing -----
        track = str(discovery_track).strip().lower()
        if track in {"", "auto", "bias"}:
            preferred = llm_bias_profile.get("preferred_tracks") or []
            if isinstance(preferred, list) and preferred:
                p0 = str(preferred[0]).strip().lower()
                if p0 in {"enricher", "anomaly_scan", "dual_track"}:
                    track = p0

        ordered_universe = self._order_universe_by_sector_weights(
            prefiltered_universe,
            sector_weight_multipliers,
        )

        if track == "anomaly_scan":
            anomaly_max = int(((allocation_policy.get("max_tickers") or {}).get("anomaly_scan", len(ordered_universe) or 250)))
            track_universe = self._cap_universe(ordered_universe, anomaly_max)
            return self._run_track_b(
                track_universe, trade_date, start_time,
                pre_stage0_snapshot=pre_stage0_snapshot,
                llm_bias_profile=llm_bias_profile,
                indicator_availability=indicator_availability,
                anomaly_scan_policy=anomaly_policy,
            )
        elif track == "dual_track":
            track_a_universe, track_b_universe = self._dual_track_universe_split(
                ordered_universe,
                allocation_policy,
            )
            return self._run_dual_track(
                track_a_universe, track_b_universe, trade_date, start_time,
                pre_stage0_snapshot=pre_stage0_snapshot,
                llm_bias_profile=llm_bias_profile,
                indicator_availability=indicator_availability,
                stage2_weight_tilts=stage2_weight_tilts,
                stage2_hard_filter_overrides=stage2_hard_filter_overrides,
                sector_weight_multipliers=sector_weight_multipliers,
                anomaly_scan_policy=anomaly_policy,
            )
        else:
            enricher_max = int(((allocation_policy.get("max_tickers") or {}).get("enricher", len(ordered_universe) or 250)))
            track_universe = self._cap_universe(ordered_universe, enricher_max)
            return self._run_track_a(
                track_universe, trade_date, start_time,
                pre_stage0_snapshot=pre_stage0_snapshot,
                llm_bias_profile=llm_bias_profile,
                indicator_availability=indicator_availability,
                stage2_weight_tilts=stage2_weight_tilts,
                stage2_hard_filter_overrides=stage2_hard_filter_overrides,
                sector_weight_multipliers=sector_weight_multipliers,
            )

    # ------------------------------------------------------------------
    # Track A: Enricher → Stage 2 scoring (existing pipeline)
    # ------------------------------------------------------------------

    def _run_track_a(
        self,
        universe: List[str],
        trade_date: str,
        start_time: float,
        pre_stage0_snapshot: Optional[Dict[str, Any]] = None,
        llm_bias_profile: Optional[Dict[str, Any]] = None,
        indicator_availability: Optional[Dict[str, Any]] = None,
        stage2_weight_tilts: Optional[Dict[str, float]] = None,
        stage2_hard_filter_overrides: Optional[Dict[str, Any]] = None,
        sector_weight_multipliers: Optional[Dict[str, float]] = None,
    ) -> IntelligenceResult:
        import time

        # Stage 1: batch enrichment (no LLM).
        try:
            stage1_scorecards = self.stage1_enricher.enrich_universe(
                universe=universe,
                trade_date=trade_date,
            )
        except Exception as e:
            self.logger.error(f"Stage 1 enrichment failed: {e}")
            stage1_scorecards = []

        try:
            technical_signals = self.technical_scanner.scan_numeric_filter(
                universe=universe,
                trade_date=trade_date,
            )
        except Exception as e:
            self.logger.error(f"Technical scan failed: {e}")
            technical_signals = []

        # Stage 2: numeric scoring & filtering (no LLM).
        try:
            stage2_candidates = self.stage2_scorer.score_and_filter(
                scorecards=stage1_scorecards,
                trade_date=trade_date,
                weight_tilts=stage2_weight_tilts,
                hard_filter_overrides=stage2_hard_filter_overrides,
                sector_weight_multipliers=sector_weight_multipliers,
            )
        except Exception as e:
            self.logger.error(f"Stage 2 scoring failed: {e}")
            stage2_candidates = []

        result = IntelligenceResult(
            sector_signals=[],
            catalyst_signals=[],
            technical_signals=technical_signals,
            stage1_scorecards=stage1_scorecards,
            stage2_candidates=stage2_candidates,
            pre_stage0_snapshot=dict(pre_stage0_snapshot or {}),
            llm_bias_profile=dict(llm_bias_profile or {}),
            indicator_availability=dict(indicator_availability or {}),
            stage0_metrics=self.technical_scanner.get_stage0_last_metrics(),
            discovery_track="enricher",
            scan_date=trade_date,
            scan_duration_secs=round(time.time() - start_time, 1),
        )

        self.logger.info(
            "Track A (enricher) complete. "
            f"prefiltered={len(universe)} "
            f"stage1={len(stage1_scorecards)} "
            f"stage2={len(stage2_candidates)} "
            f"screened={len(technical_signals)} duration={result.scan_duration_secs}s"
        )
        return result

    # ------------------------------------------------------------------
    # Track B: Momentum anomaly scans
    # ------------------------------------------------------------------

    def _run_track_b(
        self,
        universe: List[str],
        trade_date: str,
        start_time: float,
        pre_stage0_snapshot: Optional[Dict[str, Any]] = None,
        llm_bias_profile: Optional[Dict[str, Any]] = None,
        indicator_availability: Optional[Dict[str, Any]] = None,
        anomaly_scan_policy: Optional[Dict[str, Any]] = None,
    ) -> IntelligenceResult:
        import time

        try:
            momentum_hits = self.anomaly_scanner.run_all_scans(
                universe=universe,
                trade_date=trade_date,
                policy_overrides=anomaly_scan_policy,
            )
        except Exception as e:
            self.logger.error(f"Track B anomaly scans failed: {e}")
            momentum_hits = []

        result = IntelligenceResult(
            momentum_scan_hits=momentum_hits,
            pre_stage0_snapshot=dict(pre_stage0_snapshot or {}),
            llm_bias_profile=dict(llm_bias_profile or {}),
            indicator_availability=dict(indicator_availability or {}),
            stage0_metrics=self.technical_scanner.get_stage0_last_metrics(),
            discovery_track="anomaly_scan",
            scan_date=trade_date,
            scan_duration_secs=round(time.time() - start_time, 1),
        )

        self.logger.info(
            "Track B (anomaly_scan) complete. "
            f"prefiltered={len(universe)} "
            f"hits={len(momentum_hits)} "
            f"duration={result.scan_duration_secs}s"
        )
        return result

    # ------------------------------------------------------------------
    # Dual-Track: run A + B together, return merged IntelligenceResult
    # ------------------------------------------------------------------

    def _run_dual_track(
        self,
        track_a_universe: List[str],
        track_b_universe: List[str],
        trade_date: str,
        start_time: float,
        pre_stage0_snapshot: Optional[Dict[str, Any]] = None,
        llm_bias_profile: Optional[Dict[str, Any]] = None,
        indicator_availability: Optional[Dict[str, Any]] = None,
        stage2_weight_tilts: Optional[Dict[str, float]] = None,
        stage2_hard_filter_overrides: Optional[Dict[str, Any]] = None,
        sector_weight_multipliers: Optional[Dict[str, float]] = None,
        anomaly_scan_policy: Optional[Dict[str, Any]] = None,
    ) -> IntelligenceResult:
        """Run Track A (enricher) and Track B (anomaly scans) sequentially.

        Results are merged into a single IntelligenceResult with
        ``discovery_track="dual_track"``.  Scoring / convergence-bonus
        logic lives in IntelligenceDrivenRecommender._rankings_from_dual_track.
        """
        import time

        # Run Track A (uses its own intermediate timer internally; we ignore
        # that timing and track total wall-time here).
        result_a = self._run_track_a(
            track_a_universe,
            trade_date,
            start_time,
            pre_stage0_snapshot=pre_stage0_snapshot,
            llm_bias_profile=llm_bias_profile,
            indicator_availability=indicator_availability,
            stage2_weight_tilts=stage2_weight_tilts,
            stage2_hard_filter_overrides=stage2_hard_filter_overrides,
            sector_weight_multipliers=sector_weight_multipliers,
        )
        result_b = self._run_track_b(
            track_b_universe,
            trade_date,
            start_time,
            pre_stage0_snapshot=pre_stage0_snapshot,
            llm_bias_profile=llm_bias_profile,
            indicator_availability=indicator_availability,
            anomaly_scan_policy=anomaly_scan_policy,
        )

        merged = IntelligenceResult(
            sector_signals=result_a.sector_signals,
            catalyst_signals=result_a.catalyst_signals,
            technical_signals=result_a.technical_signals,
            stage1_scorecards=result_a.stage1_scorecards,
            stage2_candidates=result_a.stage2_candidates,
            momentum_scan_hits=result_b.momentum_scan_hits,
            pre_stage0_snapshot=dict(pre_stage0_snapshot or {}),
            llm_bias_profile=dict(llm_bias_profile or {}),
            indicator_availability=dict(indicator_availability or {}),
            stage0_metrics=result_a.stage0_metrics or result_b.stage0_metrics,
            discovery_track="dual_track",
            scan_date=trade_date,
            scan_duration_secs=round(time.time() - start_time, 1),
        )

        self.logger.info(
            "Dual-Track complete. "
            f"prefiltered_a={len(track_a_universe)} "
            f"prefiltered_b={len(track_b_universe)} "
            f"stage2={len(result_a.stage2_candidates)} "
            f"momentum_hits={len(result_b.momentum_scan_hits)} "
            f"duration={merged.scan_duration_secs}s"
        )
        return merged
