"""
Theme Engine — Stage -1: Theme Graph and Supply-Chain Exposure Engine

Discovers candidates by reasoning from emerging themes → constrained
supply-chain nodes → exposed public tickers, before traditional factor
scores confirm a stock.

Modules:
    models    — core dataclasses (ThemeNode, ThemeChain, ThemeExposureCandidate, …)
    taxonomy  — YAML taxonomy loader and validator
"""

from .models import (
    ThemeChain,
    ThemeExposureCandidate,
    ThemeNode,
    TickerExposure,
)
from .taxonomy import ThemeTaxonomyLoader, load_taxonomy

__all__ = [
    "ThemeNode",
    "TickerExposure",
    "ThemeChain",
    "ThemeExposureCandidate",
    "ThemeTaxonomyLoader",
    "load_taxonomy",
]
