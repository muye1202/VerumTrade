from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


DEFAULT_SCREENING_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "BAC", "GS", "MS", "V", "MA",
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY",
    "WMT", "HD", "MCD", "NKE", "SBUX", "COST",
    "XOM", "CVX", "CAT", "BA", "GE", "UPS",
    "AMD", "INTC", "AVGO", "QCOM", "MU",
]


@dataclass
class SectorSignal:
    """One sector's momentum reading."""
    sector: str
    etf: str
    return_30d: float = 0.0
    return_10d: float = 0.0
    relative_to_spy: float = 0.0
    momentum_rank: int = 0
    narrative: str = ""


@dataclass
class CatalystSignal:
    """A news-driven catalyst attached to a ticker or sector."""
    ticker: str = ""
    sector: str = ""
    catalyst_type: str = ""
    headline: str = ""
    sentiment_score: float = 0.0
    recency_days: int = 0
    actionability: str = "medium"


@dataclass
class TechnicalSignal:
    """Technical momentum reading for one ticker."""
    ticker: str
    price: float = 0.0
    vs_sma50_pct: float = 0.0
    vs_sma200_pct: float = 0.0
    momentum_20d: float = 0.0
    adx: float = 0.0
    obv_trend: str = "neutral"
    relative_strength_vs_spy: float = 0.0
    volume_ratio: float = 0.0
    composite_score: float = 0.0
    roc_20d: float = 0.0
    rs_vs_spy_20d: float = 0.0
    bollinger_pct_b: float = 0.0
    obv_slope_10d: float = 0.0
    avg_volume_20d: float = 0.0
    gate_passed: bool = False
    gate_fail_reasons: List[str] = field(default_factory=list)


@dataclass
class Stage1EnrichmentScorecard:
    """Stage 1 non-LLM enrichment output for one ticker."""
    ticker: str
    catalyst_window: str = ""
    price: float = 0.0
    roc_20d: float = 0.0
    rs_vs_spy_20d: float = 0.0
    adx: float = 0.0
    volume_ratio: float = 0.0
    vs_sma50_pct: float = 0.0
    vs_sma200_pct: float = 0.0
    bollinger_pct_b: float = 0.0
    obv_slope_10d: float = 0.0
    avg_dollar_volume_20d: float = 0.0
    vwap: float = 0.0
    vwap_distance_pct: float = 0.0
    earnings_beat_rate_4q: float = 0.0
    eps_consensus_current_q: float = 0.0
    options_unusual_score: float = 0.0
    options_call_put_notional_ratio: float = 0.0
    short_interest_pct_float: float = 0.0
    days_to_cover: float = 0.0
    finra_short_volume_ratio_latest: float = 0.0
    insider_signal: str = "neutral"
    data_quality_flags: List[str] = field(default_factory=list)


@dataclass
class IntelligenceResult:
    """Aggregated output of all three sub-agents."""
    sector_signals: List[SectorSignal] = field(default_factory=list)
    catalyst_signals: List[CatalystSignal] = field(default_factory=list)
    technical_signals: List[TechnicalSignal] = field(default_factory=list)
    stage1_scorecards: List[Stage1EnrichmentScorecard] = field(default_factory=list)
    stage0_metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    scan_date: str = ""
    scan_duration_secs: float = 0.0

    @property
    def hot_sectors(self) -> List[SectorSignal]:
        return sorted(self.sector_signals, key=lambda s: s.momentum_rank)[:3]

    @property
    def high_conviction_catalysts(self) -> List[CatalystSignal]:
        return [
            c for c in self.catalyst_signals
            if c.sentiment_score > 0.3 and c.actionability == "high"
        ]

    @property
    def breakout_candidates(self) -> List[TechnicalSignal]:
        return [
            t for t in self.technical_signals
            if t.vs_sma50_pct > 0
            and t.vs_sma200_pct > 0
            and t.adx > 20
            and t.momentum_20d > 3
        ]

    def tickers_with_multi_signal_alignment(self) -> List[str]:
        catalyst_tickers = {c.ticker for c in self.high_conviction_catalysts if c.ticker}
        breakout_tickers = {t.ticker for t in self.breakout_candidates}
        return sorted(catalyst_tickers & breakout_tickers)
