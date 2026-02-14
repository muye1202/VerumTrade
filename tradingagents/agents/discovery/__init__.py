# tradingagents/agents/discovery/__init__.py
"""
Stock discovery agents and intelligence sub-agents.

This module provides tools for discovering promising stocks using:
- Sector performance analysis
- News catalyst detection
- Technical breakout screening
- Multi-signal intelligence synthesis
"""

from .intelligence_sub_agents import (
    IntelligenceScanner,
    IntelligenceResult,
    SectorSignal,
    CatalystSignal,
    TechnicalSignal,
)
from .intelligence_integration import IntelligenceDrivenRecommender

# Optional legacy imports (can require extra market-data dependencies).
try:
    from .stock_recommender import StockRecommenderAgent, create_stock_recommender
    from .stock_screener import (
        scan_sector_performance,
        screen_technical_breakouts,
        scan_news_catalysts,
    )
except Exception:  # pragma: no cover - optional dependency path
    StockRecommenderAgent = None
    create_stock_recommender = None
    scan_sector_performance = None
    screen_technical_breakouts = None
    scan_news_catalysts = None

__all__ = [
    # Intelligence architecture (new)
    "IntelligenceScanner",
    "IntelligenceResult",
    "IntelligenceDrivenRecommender",
    # Data classes
    "SectorSignal",
    "CatalystSignal",
    "TechnicalSignal",
    # Legacy recommender (for backward compat in tests)
    "StockRecommenderAgent",
    "create_stock_recommender",
    # Screening tools
    "scan_sector_performance",
    "screen_technical_breakouts",
    "scan_news_catalysts",
]
