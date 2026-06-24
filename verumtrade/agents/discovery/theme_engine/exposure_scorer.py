from __future__ import annotations
"""
Theme Engine — Step 4: LLM Exposure Scorer

Re-validates each ThemeExposureCandidate against the company's actual
business description and fresh evidence headlines, producing calibrated
confidence scores and concise narrative justifications.

This is the only LLM call in the theme engine. It is fully optional:
  - When allow_llm_call=False (default) seed scores pass through unchanged.
  - When allow_llm_call=True candidates are grouped by theme_id (one LLM
    call per theme, ≤15 calls instead of one per ticker) and results are
    cached for 24 h.

Usage:
    scorer = ExposureScorer(llm=my_llm, config=config)
    candidates = scorer.score(candidates, trade_date, allow_llm_call=True,
                              cache_config=..., metrics=...)
"""

import json
import logging
from copy import copy
from dataclasses import replace
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from verumtrade.agents.discovery.intelligence.pipeline_cache import (
    load_cache_value,
    save_cache_value,
)
from verumtrade.agents.discovery.intelligence.pipeline_utils import (
    parse_json_dict,
    stable_key,
)
from .models import ThemeExposureCandidate


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PROMPT_VERSION = "1.0"

_SYSTEM_PROMPT = """You are a supply-chain analyst. For each ticker, assess whether it has
genuine exposure to the given investment theme bottleneck.

Respond ONLY with this JSON structure:
{
  "scorings": [
    {
      "ticker": "EXAMPLE",
      "exposure_type": "direct",
      "exposure_confidence": 0.85,
      "why_it_matters": "One or two sentences citing specific evidence."
    }
  ]
}

Scoring guide:
  0.90+ → core business IS this bottleneck node
  0.70–0.89 → meaningful, partial exposure
  0.50–0.69 → indirect benefit
  < 0.50 → weak or speculative — lower than current seed

Return scorings for EVERY ticker provided. Be conservative; thin evidence
warrants a lower score. exposure_type must be one of: direct, indirect,
second_order."""


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ExposureScorer:
    """
    Step 4: LLM re-scoring of ThemeExposureCandidates.

    Groups candidates by theme_id and issues one LLM call per theme batch
    (≤15 calls for the full taxonomy).  Results are cached at theme level
    for 24 h so repeated runs within a day are free.
    """

    def __init__(self, llm=None, config=None):
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        candidates: List[ThemeExposureCandidate],
        trade_date: str,
        allow_llm_call: bool = False,
        cache_config: Optional[Dict] = None,
        metrics: Optional[Dict] = None,
    ) -> List[ThemeExposureCandidate]:
        """
        Re-score candidates with an LLM.

        Fast path: returns *input unchanged* when llm is None or
        allow_llm_call is False.

        Args:
            candidates:      Output of ThemeScanner.scan().
            trade_date:      ISO date string "YYYY-MM-DD".
            allow_llm_call:  Gate: must be True AND self.llm must be set
                             for LLM calls to occur.
            cache_config:    Cache configuration dict (passed to
                             pipeline_cache helpers).  Pass
                             {"enabled": False} to skip caching.
            metrics:         Optional dict updated with cache_hits /
                             cache_misses counts.

        Returns:
            List[ThemeExposureCandidate] — same length and order as input.
        """
        if not candidates:
            return candidates
        if self.llm is None or not allow_llm_call:
            return candidates

        # Group by theme_id preserving input order
        groups: Dict[str, List[int]] = {}  # theme_id → list of indices
        for idx, cand in enumerate(candidates):
            groups.setdefault(cand.theme_id, []).append(idx)

        result: List[ThemeExposureCandidate] = list(candidates)

        for theme_id, indices in groups.items():
            theme_batch = [candidates[i] for i in indices]
            scored_batch = self._score_theme_batch(
                theme_id=theme_id,
                candidates=theme_batch,
                trade_date=trade_date,
                cache_config=cache_config,
                metrics=metrics,
            )
            for orig_idx, scored_cand in zip(indices, scored_batch):
                result[orig_idx] = scored_cand

        return result

    # ------------------------------------------------------------------
    # Internal: per-theme batch
    # ------------------------------------------------------------------

    def _score_theme_batch(
        self,
        theme_id: str,
        candidates: List[ThemeExposureCandidate],
        trade_date: str,
        cache_config: Optional[Dict],
        metrics: Optional[Dict],
    ) -> List[ThemeExposureCandidate]:
        """Score one theme batch; return originals on any failure."""
        tickers_sorted = sorted(c.ticker for c in candidates)
        cache_key = stable_key({
            "type": "theme_exposure_scoring",
            "version": PROMPT_VERSION,
            "theme_id": theme_id,
            "trade_date": trade_date,
            "tickers_fingerprint": stable_key({"tickers": tickers_sorted}),
        })

        try:
            cached_value, hit = load_cache_value(
                namespace="theme_exposure_scoring",
                key=cache_key,
                cache_config=cache_config,
                metrics=metrics,
            )
            if hit and isinstance(cached_value, list):
                self.logger.debug(
                    "Step 4 cache hit for theme '%s' (%d tickers)",
                    theme_id,
                    len(candidates),
                )
                return self._apply_scorings(candidates, cached_value)
        except Exception as exc:
            self.logger.debug("Step 4 cache load error for '%s': %s", theme_id, exc)

        try:
            scorings = self._invoke_llm(theme_id, candidates)
        except Exception as exc:
            self.logger.warning(
                "Step 4 LLM call failed for theme '%s' (non-fatal): %s",
                theme_id,
                exc,
            )
            return list(candidates)

        try:
            save_cache_value(
                namespace="theme_exposure_scoring",
                key=cache_key,
                value=scorings,
                cache_config=cache_config,
            )
        except Exception as exc:
            self.logger.debug("Step 4 cache save error for '%s': %s", theme_id, exc)

        return self._apply_scorings(candidates, scorings)

    # ------------------------------------------------------------------
    # Internal: LLM invocation
    # ------------------------------------------------------------------

    def _invoke_llm(
        self,
        theme_id: str,
        candidates: List[ThemeExposureCandidate],
    ) -> List[Dict]:
        """
        Build prompt, call LLM, parse response.

        Returns a list of scoring dicts:
            [{"ticker": ..., "exposure_type": ...,
              "exposure_confidence": ..., "why_it_matters": ...}, ...]

        Raises ValueError on malformed or missing "scorings" key.
        """
        # Representative candidate for theme metadata
        first = candidates[0]
        theme_label = first.theme
        bottleneck = first.bottleneck

        ticker_entries = []
        for cand in candidates:
            entry = {
                "ticker": cand.ticker,
                "current_exposure_type": cand.exposure_type,
                "current_confidence": round(cand.exposure_confidence, 4),
                "node_label": cand.node_id,
                "evidence": list(cand.evidence[:5]),
                "company_summary": self._get_company_summary(cand.ticker),
            }
            ticker_entries.append(entry)

        payload = {
            "theme_label": theme_label,
            "bottleneck": bottleneck,
            "tickers": ticker_entries,
        }

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]

        response = self.llm.invoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)

        parsed = parse_json_dict(raw_content)
        if parsed is None:
            raise ValueError(
                f"Step 4: LLM returned non-JSON response for theme '{theme_id}': "
                f"{raw_content[:200]!r}"
            )

        scorings = parsed.get("scorings")
        if not isinstance(scorings, list):
            raise ValueError(
                f"Step 4: LLM response missing 'scorings' list for theme '{theme_id}'. "
                f"Got keys: {list(parsed.keys())}"
            )

        return scorings

    # ------------------------------------------------------------------
    # Internal: apply scorings
    # ------------------------------------------------------------------

    def _apply_scorings(
        self,
        candidates: List[ThemeExposureCandidate],
        scorings: List[Dict],
    ) -> List[ThemeExposureCandidate]:
        """
        Merge LLM scorings into candidates.

        Fields updated:   exposure_type, exposure_confidence, why_it_matters
        Fields preserved: evidence, theme_acceleration, freshness_date,
                          theme_id, node_id, theme, bottleneck, ticker
        Missing tickers (LLM skipped) fall back to the original candidate.
        """
        scoring_map: Dict[str, Dict] = {}
        for s in scorings:
            if isinstance(s, dict) and "ticker" in s:
                scoring_map[str(s["ticker"]).upper()] = s

        updated: List[ThemeExposureCandidate] = []
        for cand in candidates:
            s = scoring_map.get(cand.ticker.upper())
            if s is None:
                self.logger.debug(
                    "Step 4: no scoring returned for %s/%s — keeping original",
                    cand.ticker,
                    cand.theme_id,
                )
                updated.append(cand)
                continue

            raw_conf = s.get("exposure_confidence", cand.exposure_confidence)
            try:
                new_conf = max(0.0, min(1.0, float(raw_conf)))
            except (TypeError, ValueError):
                new_conf = cand.exposure_confidence

            new_type = str(s.get("exposure_type", cand.exposure_type)).strip()
            if new_type not in {"direct", "indirect", "second_order"}:
                new_type = cand.exposure_type

            raw_why = str(s.get("why_it_matters", cand.why_it_matters) or "")
            new_why = raw_why[:500]

            updated.append(
                ThemeExposureCandidate(
                    theme=cand.theme,
                    bottleneck=cand.bottleneck,
                    ticker=cand.ticker,
                    exposure_type=new_type,
                    exposure_confidence=new_conf,
                    evidence=list(cand.evidence),
                    why_it_matters=new_why,
                    theme_acceleration=cand.theme_acceleration,
                    freshness_date=cand.freshness_date,
                    theme_id=cand.theme_id,
                    node_id=cand.node_id,
                )
            )

        return updated

    # ------------------------------------------------------------------
    # Internal: company summary
    # ------------------------------------------------------------------

    def _get_company_summary(self, ticker: str) -> str:
        """Best-effort: fetch longBusinessSummary from yfinance."""
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            return str(info.get("longBusinessSummary", "") or "")[:500]
        except Exception:
            return ""
