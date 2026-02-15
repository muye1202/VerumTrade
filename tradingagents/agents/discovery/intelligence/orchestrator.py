from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any

from .models import IntelligenceResult
from .technical_momentum import TechnicalMomentumScanner


class IntelligenceScanner:
    """
    Top-level discovery orchestrator for prefilter + technical mode.
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.technical_scanner = TechnicalMomentumScanner(llm=llm, config=config)
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
        prefiltered_universe = self.technical_scanner.build_numeric_universe(trade_date)

        if excluded_set:
            prefiltered_universe = [
                t for t in prefiltered_universe
                if str(t).strip().upper() not in excluded_set
            ]

        try:
            technical_signals = self.technical_scanner.scan_numeric_filter(
                universe=prefiltered_universe,
                trade_date=trade_date,
            )
        except Exception as e:
            self.logger.error(f"Technical scan failed: {e}")
            technical_signals = []

        result = IntelligenceResult(
            sector_signals=[],
            catalyst_signals=[],
            technical_signals=technical_signals,
            scan_date=trade_date,
            scan_duration_secs=round(time.time() - start_time, 1),
        )

        self.logger.info(
            "Prefilter+technical scan complete. "
            f"prefiltered={len(prefiltered_universe)} "
            f"screened={len(technical_signals)} duration={result.scan_duration_secs}s"
        )
        return result
