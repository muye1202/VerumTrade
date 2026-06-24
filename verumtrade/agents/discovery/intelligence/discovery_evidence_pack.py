from __future__ import annotations

"""Build normalized evidence packs for thesis-aware discovery scoring."""

from typing import Any, Dict, Iterable, List, Optional

from .pipeline_models import (
    DiscoveryEvidencePack,
    Stage1EnrichmentScorecard,
    Stage2ScoredCandidate,
)


class DiscoveryEvidencePackBuilder:
    """Merge theme, inflection, attention, Stage 1, and Stage 2 data by ticker."""

    def build(
        self,
        *,
        stage1_scorecards: Iterable[Stage1EnrichmentScorecard] = (),
        stage2_candidates: Iterable[Stage2ScoredCandidate] = (),
        theme_candidates: Iterable[Any] = (),
        business_inflection_signals: Iterable[Any] = (),
        attention_gap_signals: Iterable[Any] = (),
    ) -> List[DiscoveryEvidencePack]:
        scorecards = self._index_by_ticker(stage1_scorecards)
        stage2 = self._index_by_ticker(stage2_candidates)
        themes = self._best_theme_by_ticker(theme_candidates)
        inflections = self._index_by_ticker(business_inflection_signals)
        attention = self._index_by_ticker(attention_gap_signals)

        tickers = sorted(set(scorecards) | set(stage2) | set(themes) | set(inflections) | set(attention))
        packs: List[DiscoveryEvidencePack] = []
        for ticker in tickers:
            sc = scorecards.get(ticker)
            s2 = stage2.get(ticker)
            theme = themes.get(ticker)
            infl = inflections.get(ticker)
            gap = attention.get(ticker)

            evidence_score = self._clamp(getattr(s2, "composite_score", 0.0) if s2 else 0.0)
            theme_score = self._theme_score(theme)
            bottleneck_score = self._bottleneck_score(theme)
            inflection_score = self._clamp(getattr(infl, "inflection_score", 0.0) if infl else 0.0)
            attention_score = self._clamp(getattr(gap, "attention_gap_score", 0.0) if gap else 0.0)
            momentum_score = self._momentum_score(sc, s2)
            catalyst_score = self._catalyst_score(sc)
            risk_penalty = self._risk_penalty(sc, gap)
            evidence = self._evidence_bullets(theme, infl, gap)

            packs.append(
                DiscoveryEvidencePack(
                    ticker=ticker,
                    evidence_score=evidence_score,
                    theme_score=theme_score,
                    bottleneck_score=bottleneck_score,
                    business_inflection_score=inflection_score,
                    attention_gap_score=attention_score,
                    momentum_confirmation_score=momentum_score,
                    catalyst_proximity_score=catalyst_score,
                    risk_penalty=risk_penalty,
                    primary_theme=str(getattr(theme, "theme", "") or ""),
                    primary_bottleneck=str(getattr(theme, "bottleneck", "") or ""),
                    exposure_type=str(getattr(theme, "exposure_type", "") or ""),
                    evidence_bullets=evidence,
                    attention_reasons=list(getattr(gap, "reasons", []) or []),
                    scorecard=sc,
                    stage2_candidate=s2,
                )
            )
        packs.sort(key=lambda item: item.ticker)
        return packs

    @staticmethod
    def _index_by_ticker(items: Iterable[Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for item in items or []:
            ticker = str(getattr(item, "ticker", "")).strip().upper()
            if ticker:
                out[ticker] = item
        return out

    @classmethod
    def _best_theme_by_ticker(cls, items: Iterable[Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for item in items or []:
            ticker = str(getattr(item, "ticker", "")).strip().upper()
            if not ticker:
                continue
            if cls._theme_score(item) > cls._theme_score(out.get(ticker)):
                out[ticker] = item
        return out

    @staticmethod
    def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
        try:
            return round(max(low, min(high, float(value or 0.0))), 2)
        except Exception:
            return 0.0

    @classmethod
    def _theme_score(cls, theme: Optional[Any]) -> float:
        if theme is None:
            return 0.0
        confidence = cls._clamp(float(getattr(theme, "exposure_confidence", 0.0) or 0.0) * 100.0)
        acceleration = cls._clamp(float(getattr(theme, "theme_acceleration", 0.0) or 0.0) * 100.0)
        return cls._clamp(max(confidence, confidence * 0.85 + acceleration * 0.15))

    @classmethod
    def _bottleneck_score(cls, theme: Optional[Any]) -> float:
        if theme is None:
            return 0.0
        base = cls._theme_score(theme)
        exposure_type = str(getattr(theme, "exposure_type", "") or "").lower()
        multiplier = 1.0 if exposure_type == "direct" else 0.75 if exposure_type == "indirect" else 0.55
        has_bottleneck = bool(str(getattr(theme, "bottleneck", "") or "").strip())
        return cls._clamp(base * multiplier if has_bottleneck else base * 0.5)

    @classmethod
    def _momentum_score(
        cls,
        scorecard: Optional[Stage1EnrichmentScorecard],
        stage2: Optional[Stage2ScoredCandidate],
    ) -> float:
        if stage2 and getattr(stage2, "technical_momentum_score", 0.0):
            return cls._clamp(getattr(stage2, "technical_momentum_score", 0.0))
        if not scorecard:
            return 0.0
        alignment = float(getattr(scorecard, "momentum_alignment_score", 0.0) or 0.0)
        roc = max(0.0, float(getattr(scorecard, "roc_20d", 0.0) or 0.0) * 4.0)
        rs = max(0.0, float(getattr(scorecard, "rs_vs_spy_20d", 0.0) or 0.0) * 3.0)
        accum = max(0.0, float(getattr(scorecard, "accum_distrib_ratio_20d", 0.0) or 0.0) - 1.0) * 15.0
        return cls._clamp(max(alignment, roc + rs + accum))

    @classmethod
    def _catalyst_score(cls, scorecard: Optional[Stage1EnrichmentScorecard]) -> float:
        if not scorecard:
            return 0.0
        window = str(getattr(scorecard, "catalyst_window", "") or "").lower()
        if any(token in window for token in ["this week", "0-7", "near", "imminent"]):
            return 80.0
        if any(token in window for token in ["30", "month", "soon"]):
            return 55.0
        return 0.0

    @classmethod
    def _risk_penalty(cls, scorecard: Optional[Stage1EnrichmentScorecard], gap: Optional[Any]) -> float:
        penalty = 0.0
        if scorecard:
            penalty += max(0.0, float(getattr(scorecard, "options_unusual_score", 0.0) or 0.0) * 0.05)
            penalty += max(0.0, float(getattr(scorecard, "short_interest_pct_float", 0.0) or 0.0) * 0.45)
            penalty += min(10.0, len(getattr(scorecard, "data_quality_flags", []) or []) * 2.5)
        if gap:
            penalty += max(0.0, float(getattr(gap, "crowding_score", 0.0) or 0.0) * 0.1)
        return cls._clamp(penalty, high=35.0)

    @staticmethod
    def _evidence_bullets(theme: Optional[Any], inflection: Optional[Any], gap: Optional[Any]) -> List[str]:
        bullets: List[str] = []
        for source in (
            list(getattr(theme, "evidence", []) or []) if theme else [],
            list(getattr(inflection, "evidence", []) or []) if inflection else [],
            list(getattr(gap, "reasons", []) or []) if gap else [],
        ):
            for item in source:
                text = str(item or "").strip()
                if text and text not in bullets:
                    bullets.append(text)
        return bullets[:8]
