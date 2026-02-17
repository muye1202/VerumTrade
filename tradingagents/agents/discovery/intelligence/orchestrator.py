from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any

from .models import IntelligenceResult
from .stage1_enrichment import Stage1BatchEnricher
from .stage2_scoring import Stage2Scorer
from .technical_momentum import TechnicalMomentumScanner


class IntelligenceScanner:
    """
    Top-level discovery orchestrator for Stage 0 → Stage 1 → Stage 2 pipeline.
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.technical_scanner = TechnicalMomentumScanner(llm=llm, config=config)
        self.stage1_enricher = Stage1BatchEnricher(config=config)
        self.stage2_scorer = Stage2Scorer(config=config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def scan_with_prefilter_universe(
        self,
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
    ) -> IntelligenceResult:
        import time

        start_time = time.time()
        excluded_set = {
            str(t).strip().upper()
            for t in (excluded_tickers or [])
            if str(t).strip()
        }

        # Stage 0: prefilter pipeline (tradeable US equities -> ADV -> earnings).
        try:
            prefiltered_universe = self.technical_scanner.build_numeric_universe(
                trade_date,
                excluded_tickers=sorted(excluded_set),
            )
        except TypeError:
            prefiltered_universe = self.technical_scanner.build_numeric_universe(trade_date)
            if excluded_set:
                prefiltered_universe = [
                    t for t in prefiltered_universe
                    if str(t).strip().upper() not in excluded_set
                ]

        # Stage 1: batch enrichment (no LLM).
        try:
            stage1_scorecards = self.stage1_enricher.enrich_universe(
                universe=prefiltered_universe,
                trade_date=trade_date,
            )
        except Exception as e:
            self.logger.error(f"Stage 1 enrichment failed: {e}")
            stage1_scorecards = []

        try:
            technical_signals = self.technical_scanner.scan_numeric_filter(
                universe=prefiltered_universe,
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
            stage0_metrics=self.technical_scanner.get_stage0_last_metrics(),
            scan_date=trade_date,
            scan_duration_secs=round(time.time() - start_time, 1),
        )

        self.logger.info(
            "Prefilter+stage1+stage2+technical scan complete. "
            f"prefiltered={len(prefiltered_universe)} "
            f"stage1={len(stage1_scorecards)} "
            f"stage2={len(stage2_candidates)} "
            f"screened={len(technical_signals)} duration={result.scan_duration_secs}s"
        )
        return result
