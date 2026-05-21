from __future__ import annotations
"""
Theme Engine — Stage -1 Theme Scanner

Orchestrates theme-first discovery: taxonomy seeds → optional evidence
enrichment → ranked List[ThemeExposureCandidate].

Scan modes (config["theme_engine"]["scan_mode"]):
  "seed_only"     — taxonomy seeds only, no network calls (fast, deterministic)
  "with_evidence" — enriches seed candidates with fresh headline evidence (default)

LLM is NOT called here. LLM scoring is reserved for Step 4 (future work).
"""

import logging
from typing import List, Optional

from .evidence_collector import ThemeEvidenceCollector
from .models import ThemeExposureCandidate
from .taxonomy import ThemeTaxonomyLoader, _why_it_matters


class ThemeScanner:
    """
    Stage -1: Theme-first discovery orchestrator.

    Scan modes (controlled by config["theme_engine"]["scan_mode"]):
      "seed_only"   — use taxonomy seed data only, no network calls (fast, deterministic)
      "with_evidence" — fetch fresh news/RSS evidence per ticker (default)

    In both modes, LLM is NOT called. LLM scoring is Step 4 (not yet built).
    The exposure_confidence values come directly from taxonomy seeds.
    The theme_acceleration field is derived from evidence volume in "with_evidence" mode.
    """

    def __init__(self, llm=None, config=None):
        self.config = config or {}
        self.llm = llm  # reserved for Step 4
        self.taxonomy_loader = ThemeTaxonomyLoader(config=config)
        te_cfg = dict((config or {}).get("theme_engine") or {})
        self._scan_mode = str(te_cfg.get("scan_mode", "with_evidence")).strip().lower()
        self._min_confidence = float(te_cfg.get("min_seed_confidence", 0.0))
        self._evidence_collector = ThemeEvidenceCollector(config=config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def scan(
        self,
        trade_date: str,
        universe_hint: Optional[List[str]] = None,
    ) -> List[ThemeExposureCandidate]:
        """
        Run Stage -1 theme discovery.

        Returns a ranked list of ThemeExposureCandidates sorted by:
          1. Bottleneck exposure first
          2. exposure_confidence descending
          3. theme_acceleration descending (evidence volume proxy)
        """
        chains = self.taxonomy_loader.load()
        candidates: List[ThemeExposureCandidate] = []

        for chain in chains:
            bottleneck_label = next(
                (n.label for n in chain.bottleneck_nodes), chain.theme_label
            )

            # Pre-fetch evidence for the whole theme when in with_evidence mode.
            theme_evidence: dict = {}
            if self._scan_mode == "with_evidence":
                try:
                    theme_evidence = self._evidence_collector.collect_for_theme(
                        chain=chain,
                        trade_date=trade_date,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Evidence collection failed for theme '%s': %s",
                        chain.theme_id,
                        exc,
                    )

            for exp in chain.ticker_exposures:
                if exp.exposure_confidence < self._min_confidence:
                    continue

                node = chain.node_by_id(exp.node_id)
                why = _why_it_matters(chain, node, exp)

                candidate = ThemeExposureCandidate(
                    theme=chain.theme_label,
                    bottleneck=bottleneck_label,
                    ticker=exp.ticker,
                    exposure_type=exp.exposure_type,
                    exposure_confidence=exp.exposure_confidence,
                    evidence=list(exp.evidence),
                    why_it_matters=why,
                    theme_id=chain.theme_id,
                    node_id=exp.node_id,
                    freshness_date=trade_date,
                )

                if self._scan_mode == "with_evidence":
                    fresh_items = theme_evidence.get(exp.ticker, [])
                    # Sort by relevance, take top 3 headlines
                    fresh_items_sorted = sorted(
                        fresh_items, key=lambda i: i.relevance_score, reverse=True
                    )
                    for item in fresh_items_sorted[:3]:
                        if item.headline and item.headline not in candidate.evidence:
                            candidate.evidence.append(item.headline)
                    candidate.theme_acceleration = _acceleration_signal(fresh_items)

                candidates.append(candidate)

        candidates.sort(
            key=lambda c: (
                not _is_bottleneck_exposure(c, chains),
                -c.exposure_confidence,
                -c.theme_acceleration,
            )
        )

        top_tickers = [c.ticker for c in candidates[:10]]
        self.logger.info(
            "Stage -1 scan complete: %d themes, %d candidates produced. Top tickers: %s",
            len(chains),
            len(candidates),
            top_tickers,
        )
        return candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _acceleration_signal(items: list) -> float:
    """Proxy for theme acceleration: normalize evidence count to 0-1."""
    return min(1.0, len(items) / 10.0)


def _is_bottleneck_exposure(candidate: ThemeExposureCandidate, chains) -> bool:
    """Return True if the candidate's node is a bottleneck in its theme chain."""
    for chain in chains:
        if chain.theme_id == candidate.theme_id:
            return candidate.node_id in chain.bottleneck_node_ids
    return False
