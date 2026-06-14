from __future__ import annotations

"""Thesis-aware two-layer discovery scoring."""

from typing import Any, Dict, Iterable, List, Optional

from .pipeline_models import DiscoveryEvidencePack, TwoLayerScoredCandidate


class TwoLayerDiscoveryScorer:
    """Score candidates by separating market evidence from thesis quality."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def score(self, evidence_packs: Iterable[DiscoveryEvidencePack]) -> List[TwoLayerScoredCandidate]:
        scored: List[TwoLayerScoredCandidate] = []
        for pack in evidence_packs or []:
            thesis_score = self._thesis_score(pack)
            discovery_score = self._discovery_score(pack, thesis_score)
            tier, action, reasons = self._tier(pack, thesis_score, discovery_score)
            scored.append(
                TwoLayerScoredCandidate(
                    ticker=pack.ticker,
                    discovery_score=discovery_score,
                    evidence_score=pack.evidence_score,
                    thesis_score=thesis_score,
                    momentum_confirmation_score=pack.momentum_confirmation_score,
                    attention_gap_score=pack.attention_gap_score,
                    catalyst_proximity_score=pack.catalyst_proximity_score,
                    risk_penalty=pack.risk_penalty,
                    tier=tier,
                    action=action,
                    tier_reasons=reasons,
                    evidence_pack=pack,
                )
            )
        scored.sort(key=lambda item: item.discovery_score, reverse=True)
        return scored

    @staticmethod
    def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
        try:
            return round(max(low, min(high, float(value or 0.0))), 2)
        except Exception:
            return 0.0

    @classmethod
    def _thesis_score(cls, pack: DiscoveryEvidencePack) -> float:
        evidence_presence = 70.0 if pack.evidence_bullets else 0.0
        raw = (
            0.23 * pack.theme_score
            + 0.12 * pack.bottleneck_score
            + 0.25 * pack.business_inflection_score
            + 0.22 * pack.attention_gap_score
            + 0.10 * pack.catalyst_proximity_score
            + 0.08 * evidence_presence
        )
        return cls._clamp(raw)

    @classmethod
    def _discovery_score(cls, pack: DiscoveryEvidencePack, thesis_score: float) -> float:
        raw = (
            0.32 * pack.evidence_score
            + 0.34 * thesis_score
            + 0.18 * pack.momentum_confirmation_score
            + 0.10 * pack.attention_gap_score
            + 0.06 * pack.catalyst_proximity_score
            - pack.risk_penalty
        )
        return cls._clamp(raw)

    @classmethod
    def _tier(
        cls,
        pack: DiscoveryEvidencePack,
        thesis_score: float,
        discovery_score: float,
    ) -> tuple[str, str, List[str]]:
        reasons: List[str] = []
        if thesis_score >= 65.0:
            reasons.append("Strong thesis quality")
        if pack.momentum_confirmation_score >= 45.0:
            reasons.append("Momentum confirmation present")
        else:
            reasons.append("Momentum confirmation incomplete")
        if pack.risk_penalty >= 20.0:
            reasons.append("Risk penalty elevated")

        if (
            discovery_score >= 52.0
            and thesis_score >= 62.0
            and pack.momentum_confirmation_score >= 45.0
            and pack.risk_penalty < 22.0
        ):
            return "actionable", "actionable", reasons
        if thesis_score >= 52.0 and discovery_score >= 35.0:
            return "watchlist", "watchlist", reasons
        if pack.theme_score >= 50.0 or pack.business_inflection_score >= 50.0:
            return "theme_candidate", "monitor", reasons
        return "rejected", "reject", reasons
