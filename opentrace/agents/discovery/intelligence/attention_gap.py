from __future__ import annotations

"""Attention-gap scoring for discovery candidates."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class AttentionGapSignal:
    ticker: str
    attention_gap_score: float = 0.0
    business_inflection_strength: float = 0.0
    theme_exposure_strength: float = 0.0
    price_accumulation_from_low_base: float = 0.0
    consensus_penetration: float = 0.0
    media_saturation: float = 0.0
    crowding_score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "attention_gap_score": round(float(self.attention_gap_score), 2),
            "business_inflection_strength": round(float(self.business_inflection_strength), 2),
            "theme_exposure_strength": round(float(self.theme_exposure_strength), 2),
            "price_accumulation_from_low_base": round(
                float(self.price_accumulation_from_low_base),
                2,
            ),
            "consensus_penetration": round(float(self.consensus_penetration), 2),
            "media_saturation": round(float(self.media_saturation), 2),
            "crowding_score": round(float(self.crowding_score), 2),
            "reasons": list(self.reasons or []),
        }


class AttentionGapDetector:
    """Score whether improving evidence is still under-recognized."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def score(
        self,
        *,
        scorecards: Iterable[Any],
        theme_candidates: Iterable[Any],
        inflection_signals: Iterable[Any],
    ) -> List[AttentionGapSignal]:
        theme_strength = self._theme_strength_by_ticker(theme_candidates)
        inflection_strength = {
            str(signal.ticker).upper(): float(getattr(signal, "inflection_score", 0.0))
            for signal in (inflection_signals or [])
            if str(getattr(signal, "ticker", "")).strip()
        }

        out: List[AttentionGapSignal] = []
        for sc in scorecards or []:
            ticker = str(getattr(sc, "ticker", "")).strip().upper()
            if not ticker:
                continue

            business = inflection_strength.get(ticker, 0.0)
            theme = theme_strength.get(ticker, 0.0)
            accumulation = self._price_accumulation_score(sc)
            consensus = self._consensus_penetration_score(sc)
            media = self._media_saturation_score(sc)
            crowding = self._crowding_score(sc)

            raw_score = (
                0.35 * business
                + 0.30 * theme
                + 0.20 * accumulation
                + 0.10 * max(0.0, 100.0 - consensus)
                + 0.05 * max(0.0, 100.0 - media)
                - 0.25 * crowding
            )
            score = max(0.0, min(100.0, raw_score))
            reasons = self._reasons(
                business=business,
                theme=theme,
                accumulation=accumulation,
                consensus=consensus,
                media=media,
                crowding=crowding,
            )
            if score <= 0 and not reasons:
                continue
            out.append(
                AttentionGapSignal(
                    ticker=ticker,
                    attention_gap_score=round(score, 2),
                    business_inflection_strength=round(business, 2),
                    theme_exposure_strength=round(theme, 2),
                    price_accumulation_from_low_base=round(accumulation, 2),
                    consensus_penetration=round(consensus, 2),
                    media_saturation=round(media, 2),
                    crowding_score=round(crowding, 2),
                    reasons=reasons,
                )
            )

        out.sort(key=lambda item: item.attention_gap_score, reverse=True)
        return out

    @staticmethod
    def _theme_strength_by_ticker(theme_candidates: Iterable[Any]) -> Dict[str, float]:
        strengths: Dict[str, float] = {}
        for cand in theme_candidates or []:
            ticker = str(getattr(cand, "ticker", "")).strip().upper()
            if not ticker:
                continue
            confidence = float(getattr(cand, "exposure_confidence", 0.0) or 0.0) * 100.0
            strengths[ticker] = max(
                strengths.get(ticker, 0.0),
                min(100.0, confidence),
            )
        return strengths

    @staticmethod
    def _price_accumulation_score(scorecard: Any) -> float:
        roc = float(getattr(scorecard, "roc_20d", 0.0) or 0.0)
        accum = float(getattr(scorecard, "accum_distrib_ratio_20d", 0.0) or 0.0)
        rs = float(getattr(scorecard, "rs_vs_spy_20d", 0.0) or 0.0)
        return max(0.0, min(100.0, roc * 4.0 + max(0.0, accum - 1.0) * 18.0 + max(0.0, rs) * 3.0))

    @staticmethod
    def _consensus_penetration_score(scorecard: Any) -> float:
        breadth = float(getattr(scorecard, "eps_revision_breadth_30d", 0.0) or 0.0)
        magnitude = abs(float(getattr(scorecard, "eps_revision_magnitude_30d", 0.0) or 0.0))
        revenue_direction = float(getattr(scorecard, "revenue_revision_direction", 0.0) or 0.0)
        return max(0.0, min(100.0, breadth + magnitude * 4.0 + max(0.0, revenue_direction) * 15.0))

    @staticmethod
    def _media_saturation_score(scorecard: Any) -> float:
        sentiment = abs(float(getattr(scorecard, "finnhub_sentiment_score", 0.0) or 0.0))
        return max(0.0, min(100.0, sentiment * 100.0))

    @staticmethod
    def _crowding_score(scorecard: Any) -> float:
        options = float(getattr(scorecard, "options_unusual_score", 0.0) or 0.0)
        squeeze = float(getattr(scorecard, "short_interest_pct_float", 0.0) or 0.0)
        return max(0.0, min(100.0, options * 0.5 + squeeze * 0.8))

    @staticmethod
    def _reasons(
        *,
        business: float,
        theme: float,
        accumulation: float,
        consensus: float,
        media: float,
        crowding: float,
    ) -> List[str]:
        reasons: List[str] = []
        if business >= 70.0:
            reasons.append("Strong business inflection")
        if theme >= 70.0:
            reasons.append("Strong theme exposure")
        if accumulation >= 35.0:
            reasons.append("Price accumulation from a low base")
        if consensus <= 25.0:
            reasons.append("Low consensus penetration")
        if media <= 25.0:
            reasons.append("Low media saturation")
        if crowding >= 60.0:
            reasons.append("Crowding risk is elevated")
        return reasons
