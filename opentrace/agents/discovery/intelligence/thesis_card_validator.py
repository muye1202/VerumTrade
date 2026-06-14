from __future__ import annotations

"""Build deterministic thesis cards and validation statuses for finalists."""

from typing import Iterable, List

from .pipeline_models import ThesisCard, TwoLayerScoredCandidate


class ThesisCardValidator:
    """Generate evidence-backed thesis cards from two-layer scored candidates."""

    def validate(self, candidates: Iterable[TwoLayerScoredCandidate]) -> List[ThesisCard]:
        cards: List[ThesisCard] = []
        for candidate in candidates or []:
            pack = candidate.evidence_pack
            if pack is None:
                continue
            cards.append(
                ThesisCard(
                    ticker=candidate.ticker,
                    status=candidate.action,
                    bull_thesis=self._bull_thesis(candidate),
                    theme_exposure=self._theme_exposure(candidate),
                    business_inflection=self._business_inflection(candidate),
                    momentum_confirmation=self._momentum_confirmation(candidate),
                    attention_gap=self._attention_gap(candidate),
                    catalysts=self._catalysts(candidate),
                    evidence=list(pack.evidence_bullets or [])[:6],
                    risks=self._risks(candidate),
                    kill_conditions=self._kill_conditions(candidate),
                    confidence=round(candidate.discovery_score / 100.0, 4),
                )
            )
        return cards

    @staticmethod
    def _bull_thesis(candidate: TwoLayerScoredCandidate) -> str:
        pack = candidate.evidence_pack
        theme = pack.primary_theme or "an identified growth theme"
        if pack.primary_bottleneck:
            return f"{candidate.ticker} has {pack.exposure_type or 'potential'} exposure to {theme} through {pack.primary_bottleneck}."
        return f"{candidate.ticker} has {pack.exposure_type or 'potential'} exposure to {theme}."

    @staticmethod
    def _theme_exposure(candidate: TwoLayerScoredCandidate) -> str:
        pack = candidate.evidence_pack
        if not pack.primary_theme:
            return "No clear theme exposure detected."
        return f"{pack.primary_theme}: {pack.theme_score:.1f}/100 theme score."

    @staticmethod
    def _business_inflection(candidate: TwoLayerScoredCandidate) -> str:
        pack = candidate.evidence_pack
        if pack.business_inflection_score <= 0:
            return "No business inflection signal detected."
        return f"Business inflection score {pack.business_inflection_score:.1f}/100."

    @staticmethod
    def _momentum_confirmation(candidate: TwoLayerScoredCandidate) -> str:
        pack = candidate.evidence_pack
        if pack.momentum_confirmation_score >= 45:
            return f"Momentum confirmation score {pack.momentum_confirmation_score:.1f}/100."
        return f"Momentum confirmation incomplete at {pack.momentum_confirmation_score:.1f}/100."

    @staticmethod
    def _attention_gap(candidate: TwoLayerScoredCandidate) -> str:
        pack = candidate.evidence_pack
        if pack.attention_gap_score <= 0:
            return "No attention-gap signal detected."
        reasons = "; ".join(pack.attention_reasons[:2])
        suffix = f" ({reasons})" if reasons else ""
        return f"Attention-gap score {pack.attention_gap_score:.1f}/100{suffix}."

    @staticmethod
    def _catalysts(candidate: TwoLayerScoredCandidate) -> List[str]:
        pack = candidate.evidence_pack
        if pack.catalyst_proximity_score >= 70:
            return ["Near-term catalyst window detected."]
        if pack.catalyst_proximity_score >= 40:
            return ["Potential catalyst support within the current review window."]
        return ["Catalyst support not yet confirmed."]

    @staticmethod
    def _risks(candidate: TwoLayerScoredCandidate) -> List[str]:
        pack = candidate.evidence_pack
        risks: List[str] = []
        if pack.risk_penalty >= 15:
            risks.append("Crowding, short interest, or data-quality risk is elevated.")
        if pack.momentum_confirmation_score < 45:
            risks.append("Momentum confirmation is incomplete.")
        if pack.catalyst_proximity_score < 40:
            risks.append("Near-term catalyst support is weak or missing.")
        return risks or ["No dominant deterministic risk flag detected."]

    @staticmethod
    def _kill_conditions(candidate: TwoLayerScoredCandidate) -> List[str]:
        pack = candidate.evidence_pack
        conditions = [
            "Theme exposure evidence weakens or proves indirect.",
            "Price breaks down while relative strength deteriorates.",
        ]
        if pack.business_inflection_score > 0:
            conditions.append("Revenue or margin inflection reverses in the next update.")
        else:
            conditions.append("No business inflection evidence appears in the next update.")
        if pack.attention_gap_score > 0:
            conditions.append("Consensus and media attention become crowded without new evidence.")
        return conditions
