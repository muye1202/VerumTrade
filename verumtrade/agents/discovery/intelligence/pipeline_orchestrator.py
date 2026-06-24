from __future__ import annotations
"""
Pipeline Orchestrator:
The central top-level orchestrator that unifies pre-stage, track A, track B, and the final scoring stage of the discovery pipeline.
"""

import logging
import hashlib
from typing import Dict, List, Optional, Any, Tuple

from .pipeline_models import IntelligenceResult
from .track_b_anomaly_scans import MomentumAnomalyScanner
from .feature_matrix import build_ohlcv_cache
from .market_context_snapshot import PreStage0IntelligenceBuilder
from .market_policy_llm import build_llm_bias_profile
from .track_a_enrichment import Stage1BatchEnricher
from .candidate_scoring import Stage2Scorer
from .technical_momentum_metrics import TechnicalMomentumScanner
from .attention_gap import AttentionGapDetector
from .business_inflection import BusinessInflectionExtractor
from .discovery_evidence_pack import DiscoveryEvidencePackBuilder
from .thesis_card_validator import ThesisCardValidator
from .two_layer_discovery_scoring import TwoLayerDiscoveryScorer
from verumtrade.agents.discovery.theme_engine.theme_scanner import ThemeScanner
from verumtrade.agents.discovery.theme_engine.exposure_scorer import ExposureScorer


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
        self.theme_scanner = ThemeScanner(llm=llm, config=config)
        self.exposure_scorer = ExposureScorer(llm=llm, config=config)
        self.business_inflection_extractor = BusinessInflectionExtractor(config=config)
        self.attention_gap_detector = AttentionGapDetector(config=config)
        self.evidence_pack_builder = DiscoveryEvidencePackBuilder()
        self.two_layer_scorer = TwoLayerDiscoveryScorer(config=config)
        self.thesis_card_validator = ThesisCardValidator()
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

    def _feature_matrix_cache_cfg(self) -> Dict[str, Any]:
        feature_cfg = dict(((self.config.get("discovery") or {}).get("feature_matrix") or {}))
        ttl = int(feature_cfg.get("cache_ttl_hours", 24))
        return {
            "enabled": True,
            "ttl_hours": max(1, ttl),
            "force_refresh": bool(feature_cfg.get("force_refresh", False)),
            "dir": feature_cfg.get("dir"),
        }

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
        trade_date: str,
    ) -> List[str]:
        deduped = sorted({str(t).strip().upper() for t in universe if str(t).strip()})
        if not sector_weights:
            return deduped
        try:
            all_neutral = all(abs(float(v) - 1.0) < 1e-9 for v in sector_weights.values())
        except Exception:
            all_neutral = False
        if all_neutral:
            # Deterministic date-seeded shuffle avoids persistent alphabetical bias.
            def _seeded_key(ticker: str) -> str:
                payload = f"{trade_date}:{ticker}".encode("utf-8")
                return hashlib.sha256(payload).hexdigest()
            return sorted(deduped, key=_seeded_key)

        ordered: List[Tuple[float, str]] = []
        for ticker in deduped:
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

        # Ratio-preserving split over seeded order avoids head/tail concentration.
        track_a: List[str] = []
        track_b: List[str] = []
        for ticker in capped:
            if len(track_a) >= enricher_n:
                track_b.append(ticker)
                continue
            if len(track_b) >= anomaly_n:
                track_a.append(ticker)
                continue
            next_total = len(track_a) + len(track_b) + 1
            projected = (len(track_a) + 1) / float(next_total)
            if projected <= enricher_ratio:
                track_a.append(ticker)
            else:
                track_b.append(ticker)
        return track_a, track_b

    def run_pre_stage0_intelligence(
        self,
        trade_date: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        snapshot, availability = self.pre_stage0_builder.build(trade_date=trade_date)
        cache_metrics = dict(snapshot.get("cache_metrics") or {})
        policy_cfg = dict((self.config.get("discovery") or {}))
        policy_mode = str(policy_cfg.get("policy_mode", "off")).strip().lower()
        min_conf = float(policy_cfg.get("min_regime_confidence_for_no_llm", 0.70))
        allow_llm = False
        uncertainty = self._estimate_regime_uncertainty(snapshot)
        regime_confidence = max(0.0, min(1.0, 1.0 - uncertainty))
        if policy_mode == "adaptive":
            allow_llm = regime_confidence < max(0.0, min(1.0, min_conf))
        elif policy_mode == "cached_only":
            allow_llm = False
        elif policy_mode == "off":
            allow_llm = False
        else:
            # Unknown mode falls back to deterministic behavior.
            allow_llm = False
        bias = build_llm_bias_profile(
            llm=self.llm,
            trade_date=trade_date,
            snapshot=snapshot,
            cache_config=self._pre_stage0_cache_cfg(ttl_hours=12),
            metrics=cache_metrics,
            allow_llm_call=allow_llm,
        )
        if policy_mode == "off":
            bias["scan_notes"] = "Deterministic policy mode (LLM disabled)."
        elif policy_mode == "cached_only":
            bias["scan_notes"] = "Cached-only policy mode (no new LLM calls)."
        elif policy_mode == "adaptive":
            bias["scan_notes"] = (
                f"Adaptive policy mode: regime_confidence={regime_confidence:.2f}, "
                f"llm_called={allow_llm}"
            )
        snapshot["cache_metrics"] = cache_metrics
        return snapshot, bias, availability

    @staticmethod
    def _estimate_regime_uncertainty(snapshot: Dict[str, Any]) -> float:
        """Higher value means more conflicting market-regime evidence."""
        idx = ((snapshot.get("index_regime") or {}).get("indices") or {})
        if not idx:
            return 1.0
        trend_votes = 0
        mean_rev_votes = 0
        considered = 0
        for data in idx.values():
            flags = dict((data or {}).get("regime_flags") or {})
            if not flags:
                continue
            considered += 1
            if bool(flags.get("TRENDING")):
                trend_votes += 1
            if bool(flags.get("MEAN_REVERTING")):
                mean_rev_votes += 1
        if considered == 0:
            return 1.0
        spread = abs(trend_votes - mean_rev_votes) / float(max(1, considered))
        # Uncertainty is inverse of vote spread.
        return max(0.0, min(1.0, 1.0 - spread))

    def scan_with_prefilter_universe(
        self,
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
        discovery_track: str = "enricher",
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

        # Stage -1: Theme-driven discovery
        theme_candidates = []
        theme_injected_tickers = []
        try:
            theme_candidates = self.theme_scanner.scan(trade_date)

            # Step 4: LLM re-scoring of theme exposure candidates
            if theme_candidates:
                _policy_mode = str(
                    (self.config.get("discovery") or {}).get("policy_mode", "off")
                ).strip().lower()
                _allow_scorer_llm = _policy_mode not in {"off", "cached_only"}
                _cache_metrics: dict = {}
                try:
                    theme_candidates = self.exposure_scorer.score(
                        candidates=theme_candidates,
                        trade_date=trade_date,
                        allow_llm_call=_allow_scorer_llm,
                        cache_config=self._pre_stage0_cache_cfg(ttl_hours=24),
                        metrics=_cache_metrics,
                    )
                except Exception as _score_err:
                    self.logger.warning(
                        "Step 4 exposure scoring failed (non-fatal): %s", _score_err
                    )

            # Inject high-confidence theme tickers into universe if not already present
            _min_theme_conf = float(
                ((self.config.get("theme_engine") or {}).get("min_injection_confidence", 0.70))
            )
            existing = set(str(t).upper() for t in prefiltered_universe)
            for c in theme_candidates:
                if c.exposure_confidence >= _min_theme_conf and c.ticker not in existing:
                    theme_injected_tickers.append(c.ticker)
                    existing.add(c.ticker)
            if theme_injected_tickers:
                prefiltered_universe = list(prefiltered_universe) + theme_injected_tickers
                self.logger.info(
                    "Stage -1: injected %d theme tickers into universe: %s",
                    len(theme_injected_tickers),
                    theme_injected_tickers[:20],
                )
        except Exception as _e:
            self.logger.warning("Stage -1 theme scan failed (non-fatal): %s", _e)

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
            trade_date=trade_date,
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
                theme_candidates=theme_candidates,
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
                theme_candidates=theme_candidates,
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
                theme_candidates=theme_candidates,
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
        shared_ohlcv_cache: Optional[Dict[str, str]] = None,
        prefetch_metrics: Optional[Dict[str, Any]] = None,
        theme_candidates=None,
    ) -> IntelligenceResult:
        import time

        stage1_ohlcv_cache: Dict[str, str] = dict(shared_ohlcv_cache or {})
        track_a_prefetch_metrics: Dict[str, Any] = dict(prefetch_metrics or {})
        if not stage1_ohlcv_cache:
            try:
                stage1_ohlcv_cache, track_a_prefetch_metrics = build_ohlcv_cache(
                    universe=universe,
                    trade_date=trade_date,
                    max_workers=8,
                    cache_config=self._feature_matrix_cache_cfg(),
                )
            except Exception as e:
                self.logger.debug(f"Track A OHLCV prefetch failed: {e}")
                stage1_ohlcv_cache, track_a_prefetch_metrics = {}, {}

        # Stage 1: batch enrichment (no LLM).
        try:
            stage1_scorecards = self.stage1_enricher.enrich_universe(
                universe=universe,
                trade_date=trade_date,
                ohlcv_cache=stage1_ohlcv_cache,
            )
        except Exception as e:
            self.logger.error(f"Stage 1 enrichment failed: {e}")
            stage1_scorecards = []

        try:
            technical_signals = self.technical_scanner.technical_signals_from_scorecards(
                stage1_scorecards,
            )
            if not technical_signals:
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
        stage2_meta = self.stage2_scorer.get_last_run_metadata()
        data_quality_summary = dict(stage2_meta.get("data_quality_summary") or {})
        breadth_context = dict(stage2_meta.get("breadth_context") or {})
        if breadth_context:
            data_quality_summary["breadth_context"] = breadth_context

        try:
            business_inflection_signals = (
                self.business_inflection_extractor.extract_for_scorecards(
                    stage1_scorecards,
                    trade_date=trade_date,
                )
            )
        except Exception as e:
            self.logger.warning("Business inflection extraction failed (non-fatal): %s", e)
            business_inflection_signals = []

        try:
            attention_gap_signals = self.attention_gap_detector.score(
                scorecards=stage1_scorecards,
                theme_candidates=theme_candidates or [],
                inflection_signals=business_inflection_signals,
            )
        except Exception as e:
            self.logger.warning("Attention gap scoring failed (non-fatal): %s", e)
            attention_gap_signals = []

        try:
            evidence_packs = self.evidence_pack_builder.build(
                stage1_scorecards=stage1_scorecards,
                stage2_candidates=stage2_candidates,
                theme_candidates=theme_candidates or [],
                business_inflection_signals=business_inflection_signals,
                attention_gap_signals=attention_gap_signals,
            )
        except Exception as e:
            self.logger.warning("Evidence pack build failed (non-fatal): %s", e)
            evidence_packs = []

        try:
            two_layer_candidates = self.two_layer_scorer.score(evidence_packs)
        except Exception as e:
            self.logger.warning("Two-layer discovery scoring failed (non-fatal): %s", e)
            two_layer_candidates = []

        try:
            thesis_cards = self.thesis_card_validator.validate(two_layer_candidates)
        except Exception as e:
            self.logger.warning("Thesis card validation failed (non-fatal): %s", e)
            thesis_cards = []

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
            vendor_calls_by_stage={
                "stage0": dict(self.technical_scanner.get_stage0_last_metrics()),
                "track_a_prefetch": dict(track_a_prefetch_metrics or {}),
            },
            data_quality_summary=data_quality_summary,
            filter_relaxations_applied=list(stage2_meta.get("filter_relaxations_applied") or []),
            theme_candidates=list(theme_candidates or []),
            business_inflection_signals=list(business_inflection_signals or []),
            attention_gap_signals=list(attention_gap_signals or []),
            evidence_packs=list(evidence_packs or []),
            two_layer_candidates=list(two_layer_candidates or []),
            thesis_cards=list(thesis_cards or []),
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
        shared_ohlcv_cache: Optional[Dict[str, str]] = None,
        prefetch_metrics: Optional[Dict[str, Any]] = None,
        theme_candidates=None,
    ) -> IntelligenceResult:
        import time

        if shared_ohlcv_cache is not None:
            ohlcv_cache = shared_ohlcv_cache
            track_b_metrics = dict(prefetch_metrics or {})
        else:
            ohlcv_cache = {}
            track_b_metrics: Dict[str, Any] = {}
            try:
                ohlcv_cache, track_b_metrics = build_ohlcv_cache(
                    universe=universe,
                    trade_date=trade_date,
                    max_workers=8,
                    cache_config=self._feature_matrix_cache_cfg(),
                )
            except Exception as e:
                self.logger.debug(f"Track B OHLCV prefetch failed: {e}")
                ohlcv_cache, track_b_metrics = {}, {}

        try:
            momentum_hits = self.anomaly_scanner.run_all_scans(
                universe=universe,
                trade_date=trade_date,
                ohlcv_cache=ohlcv_cache,
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
            vendor_calls_by_stage={
                "stage0": dict(self.technical_scanner.get_stage0_last_metrics()),
                "track_b_prefetch": dict(track_b_metrics or {}),
            },
            theme_candidates=list(theme_candidates or []),
            business_inflection_signals=[],
            attention_gap_signals=[],
            evidence_packs=[],
            two_layer_candidates=[],
            thesis_cards=[],
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
        theme_candidates=None,
    ) -> IntelligenceResult:
        """Run Track A (enricher) and Track B (anomaly scans) sequentially.

        Results are merged into a single IntelligenceResult with
        ``discovery_track="dual_track"``.  Scoring / convergence-bonus
        logic lives in IntelligenceDrivenRecommender._rankings_from_dual_track.
        """
        import time

        shared_universe = sorted({
            str(t).strip().upper()
            for t in (track_a_universe + track_b_universe)
            if str(t).strip()
        })
        shared_ohlcv_cache: Dict[str, str] = {}
        shared_prefetch_metrics: Dict[str, Any] = {}
        try:
            shared_ohlcv_cache, shared_prefetch_metrics = build_ohlcv_cache(
                universe=shared_universe,
                trade_date=trade_date,
                max_workers=8,
                cache_config=self._feature_matrix_cache_cfg(),
            )
        except Exception as e:
            self.logger.debug(f"Dual-track shared OHLCV prefetch failed: {e}")
            shared_ohlcv_cache, shared_prefetch_metrics = {}, {}

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
            shared_ohlcv_cache=shared_ohlcv_cache,
            prefetch_metrics={**shared_prefetch_metrics, "shared_for_dual_track": True},
            theme_candidates=theme_candidates,
        )
        result_b = self._run_track_b(
            track_b_universe,
            trade_date,
            start_time,
            pre_stage0_snapshot=pre_stage0_snapshot,
            llm_bias_profile=llm_bias_profile,
            indicator_availability=indicator_availability,
            anomaly_scan_policy=anomaly_scan_policy,
            shared_ohlcv_cache=shared_ohlcv_cache,
            prefetch_metrics={**shared_prefetch_metrics, "shared_for_dual_track": True},
            theme_candidates=theme_candidates,
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
            vendor_calls_by_stage={
                **dict(result_a.vendor_calls_by_stage or {}),
                **dict(result_b.vendor_calls_by_stage or {}),
            },
            data_quality_summary=dict(result_a.data_quality_summary or {}),
            filter_relaxations_applied=list(result_a.filter_relaxations_applied or []),
            theme_candidates=list(theme_candidates or []),
            business_inflection_signals=list(result_a.business_inflection_signals or []),
            attention_gap_signals=list(result_a.attention_gap_signals or []),
            evidence_packs=list(result_a.evidence_packs or []),
            two_layer_candidates=list(result_a.two_layer_candidates or []),
            thesis_cards=list(result_a.thesis_cards or []),
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
