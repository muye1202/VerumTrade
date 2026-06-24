# verumtrade/agents/discovery/__init__.py
"""
Stock discovery agents (prefilter + technical pipeline).

This module provides tools for discovering promising stocks using:
- Universe prefiltering (tradable US equities, liquidity, earnings window)
- Technical screening and ranking
"""

from .intelligence import (
    IntelligenceScanner,
    IntelligenceResult,
    TechnicalSignal,
)
from .intelligence_integration import IntelligenceDrivenRecommender

__all__ = [
    "IntelligenceScanner",
    "IntelligenceResult",
    "IntelligenceDrivenRecommender",
    "TechnicalSignal",
]
