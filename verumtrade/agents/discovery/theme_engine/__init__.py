"""
Theme Engine — Stage -1: Theme Graph and Supply-Chain Exposure Engine

Discovers candidates by reasoning from emerging themes → constrained
supply-chain nodes → exposed public tickers, before traditional factor
scores confirm a stock.

Modules:
    models             — core dataclasses (ThemeNode, ThemeChain, ThemeExposureCandidate, …)
    taxonomy           — YAML taxonomy loader and validator
    evidence_collector — fetches fresh news evidence for ticker-theme pairs
    theme_scanner      — Stage -1 orchestrator
"""

from .models import (
    EvidenceItem,
    ThemeChain,
    ThemeExposureCandidate,
    ThemeNode,
    TickerExposure,
)
from .taxonomy import ThemeTaxonomyLoader, load_taxonomy
from .evidence_collector import ThemeEvidenceCollector
from .theme_scanner import ThemeScanner
from .exposure_scorer import ExposureScorer

__all__ = [
    "ThemeNode",
    "TickerExposure",
    "ThemeChain",
    "ThemeExposureCandidate",
    "EvidenceItem",
    "ThemeTaxonomyLoader",
    "load_taxonomy",
    "ThemeEvidenceCollector",
    "ThemeScanner",
    "ExposureScorer",
]
