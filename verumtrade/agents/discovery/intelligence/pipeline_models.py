from __future__ import annotations
"""
Pipeline Models:
Data structures and types representing the discovery pipeline intelligence models.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from verumtrade.agents.discovery.intelligence.attention_gap import AttentionGapSignal
from verumtrade.agents.discovery.intelligence.business_inflection import (
    BusinessInflectionSignal,
)
from verumtrade.agents.discovery.theme_engine.models import ThemeExposureCandidate


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
    trend_quality_score: float = 0.0
    rv5_pct: float = 0.0
    rv20_pct: float = 0.0
    whipsaw_count_20: int = 0
    breakout_efficiency: float = 0.0
    earnings_beat_rate_4q: float = 0.0
    eps_consensus_current_q: float = 0.0
    options_unusual_score: float = 0.0
    options_call_put_notional_ratio: float = 0.0
    short_interest_pct_float: float = 0.0
    days_to_cover: float = 0.0
    finra_short_volume_ratio_latest: float = 0.0
    insider_signal: str = "neutral"
    finnhub_sentiment_score: float = 0.0
    
    # NEW: Estimate revision tracking
    eps_revision_breadth_30d: float = 0.0      # % of analysts revising up (0-100)
    eps_revision_magnitude_30d: float = 0.0    # % change in consensus EPS
    revenue_revision_direction: float = 0.0    # +1 / 0 / -1
    
    # NEW: Breakout persistence
    distance_from_52w_high_pct: float = 0.0   # 0 = at high, -10 = 10% below
    new_high_count_20d: int = 0                # days in last 20 that set a 20d new high
    breakout_persistence_days: int = 0         # consecutive days above prior 52w high
    
    # NEW: Accumulation / distribution
    accum_distrib_ratio_20d: float = 0.0  # accumulation days / distribution days
    
    # NEW: Multi-timeframe momentum
    roc_5d: float = 0.0
    roc_60d: float = 0.0
    momentum_alignment_score: float = 0.0  # 0-100, how well aligned are all timeframes
    
    # NEW: Beat magnitude tracking
    earnings_surprise_magnitudes: List[float] = field(default_factory=list)  # last 4 quarters
    earnings_surprise_trend_slope: float = 0.0  # positive = accelerating beats

    data_quality_flags: List[str] = field(default_factory=list)


@dataclass
class Stage2ScoredCandidate:
    """Stage 2: scored and filtered candidate with full numeric scorecard."""
    ticker: str
    composite_score: float = 0.0
    # Factor sub-scores (each 0-100, pre-weight)
    earnings_surprise_score: float = 0.0   # 15% weight
    technical_momentum_score: float = 0.0  # 20% weight
    options_flow_score: float = 0.0        # 10% weight
    sector_momentum_score: float = 0.0     # 5% weight
    short_squeeze_score: float = 0.0       # 5% weight
    estimate_revision_score: float = 0.0   # 20% weight (NEW)
    breakout_persistence_score: float = 0.0# 15% weight (NEW)
    accum_distrib_score: float = 0.0       # 10% weight (NEW)
    # Hard-filter metadata
    hard_filter_passed: bool = True
    hard_filter_fail_reasons: List[str] = field(default_factory=list)
    # Carry-through from Stage 1
    stage1_scorecard: Optional[Stage1EnrichmentScorecard] = None


@dataclass
class DiscoveryEvidencePack:
    """Normalized evidence bundle for thesis-aware discovery scoring."""
    ticker: str
    evidence_score: float = 0.0
    theme_score: float = 0.0
    bottleneck_score: float = 0.0
    business_inflection_score: float = 0.0
    attention_gap_score: float = 0.0
    momentum_confirmation_score: float = 0.0
    catalyst_proximity_score: float = 0.0
    risk_penalty: float = 0.0
    primary_theme: str = ""
    primary_bottleneck: str = ""
    exposure_type: str = ""
    evidence_bullets: List[str] = field(default_factory=list)
    attention_reasons: List[str] = field(default_factory=list)
    scorecard: Optional[Stage1EnrichmentScorecard] = None
    stage2_candidate: Optional[Stage2ScoredCandidate] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "evidence_score": round(float(self.evidence_score), 2),
            "theme_score": round(float(self.theme_score), 2),
            "bottleneck_score": round(float(self.bottleneck_score), 2),
            "business_inflection_score": round(float(self.business_inflection_score), 2),
            "attention_gap_score": round(float(self.attention_gap_score), 2),
            "momentum_confirmation_score": round(float(self.momentum_confirmation_score), 2),
            "catalyst_proximity_score": round(float(self.catalyst_proximity_score), 2),
            "risk_penalty": round(float(self.risk_penalty), 2),
            "primary_theme": self.primary_theme,
            "primary_bottleneck": self.primary_bottleneck,
            "exposure_type": self.exposure_type,
            "evidence_bullets": list(self.evidence_bullets or []),
            "attention_reasons": list(self.attention_reasons or []),
        }


@dataclass
class TwoLayerScoredCandidate:
    """Final thesis-aware discovery score and tier for one ticker."""
    ticker: str
    discovery_score: float = 0.0
    evidence_score: float = 0.0
    thesis_score: float = 0.0
    momentum_confirmation_score: float = 0.0
    attention_gap_score: float = 0.0
    catalyst_proximity_score: float = 0.0
    risk_penalty: float = 0.0
    tier: str = "rejected"
    action: str = "reject"
    tier_reasons: List[str] = field(default_factory=list)
    evidence_pack: Optional[DiscoveryEvidencePack] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "discovery_score": round(float(self.discovery_score), 2),
            "evidence_score": round(float(self.evidence_score), 2),
            "thesis_score": round(float(self.thesis_score), 2),
            "momentum_confirmation_score": round(float(self.momentum_confirmation_score), 2),
            "attention_gap_score": round(float(self.attention_gap_score), 2),
            "catalyst_proximity_score": round(float(self.catalyst_proximity_score), 2),
            "risk_penalty": round(float(self.risk_penalty), 2),
            "tier": self.tier,
            "action": self.action,
            "tier_reasons": list(self.tier_reasons or []),
            "evidence_pack": self.evidence_pack.to_dict() if self.evidence_pack else {},
        }


@dataclass
class ThesisCard:
    """Evidence-backed thesis card for a final discovery candidate."""
    ticker: str
    status: str = "reject"
    bull_thesis: str = ""
    theme_exposure: str = ""
    business_inflection: str = ""
    momentum_confirmation: str = ""
    attention_gap: str = ""
    catalysts: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    kill_conditions: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "bull_thesis": self.bull_thesis,
            "theme_exposure": self.theme_exposure,
            "business_inflection": self.business_inflection,
            "momentum_confirmation": self.momentum_confirmation,
            "attention_gap": self.attention_gap,
            "catalysts": list(self.catalysts or []),
            "evidence": list(self.evidence or []),
            "risks": list(self.risks or []),
            "kill_conditions": list(self.kill_conditions or []),
            "confidence": round(float(self.confidence), 4),
        }


@dataclass
class MomentumScanHit:
    """One triggered momentum anomaly signal from Track B scans."""
    ticker: str
    scan_name: str  # "momentum_acceleration" | "volatility_breakout" | "rs_divergence" | "stealth_accumulation"
    signal_value: float  # Primary signal metric
    trigger_details: Dict[str, float] = field(default_factory=dict)
    # Optional normalized signal used for cross-scan ranking.
    normalized_strength: float = 0.0
    # Optional raw signal for explainability when signal_value is transformed.
    raw_value: float = 0.0
    # Expected direction of the signal; "up" for bullish anomalies.
    direction: str = "up"


@dataclass
class FeatureRow:
    """Canonical per-ticker feature row for discovery stages."""
    ticker: str
    prices: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    indicators: Dict[str, float] = field(default_factory=dict)
    data_quality_flags: List[str] = field(default_factory=list)


@dataclass
class ScanSignal:
    """Unified cross-scan signal schema for ranking."""
    ticker: str
    scan_name: str
    raw_value: float = 0.0
    normalized_strength: float = 0.0
    direction: str = "up"


@dataclass
class PreStage0Snapshot:
    """Computed market intelligence snapshot before Stage 0 universe build."""
    trade_date: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    cache_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMBiasProfile:
    """Bounded bias profile generated from pre-Stage-0 snapshot."""
    regime_label: str = "NEUTRAL"
    risk_posture: str = "NEUTRAL"
    preferred_tracks: List[str] = field(default_factory=list)
    stage0_overrides: Dict[str, Any] = field(default_factory=dict)
    stage2_weight_tilts: Dict[str, float] = field(default_factory=dict)
    scan_notes: str = ""


@dataclass
class IntelligenceResult:
    """Aggregated output of all three sub-agents."""
    sector_signals: List[SectorSignal] = field(default_factory=list)
    catalyst_signals: List[CatalystSignal] = field(default_factory=list)
    technical_signals: List[TechnicalSignal] = field(default_factory=list)
    stage1_scorecards: List[Stage1EnrichmentScorecard] = field(default_factory=list)
    stage2_candidates: List[Stage2ScoredCandidate] = field(default_factory=list)
    momentum_scan_hits: List[MomentumScanHit] = field(default_factory=list)
    pre_stage0_snapshot: Dict[str, Any] = field(default_factory=dict)
    llm_bias_profile: Dict[str, Any] = field(default_factory=dict)
    indicator_availability: Dict[str, Any] = field(default_factory=dict)
    stage0_metrics: Dict[str, Any] = field(default_factory=dict)
    vendor_calls_by_stage: Dict[str, Any] = field(default_factory=dict)
    data_quality_summary: Dict[str, Any] = field(default_factory=dict)
    filter_relaxations_applied: List[str] = field(default_factory=list)
    theme_candidates: List[ThemeExposureCandidate] = field(default_factory=list)
    business_inflection_signals: List[BusinessInflectionSignal] = field(default_factory=list)
    attention_gap_signals: List[AttentionGapSignal] = field(default_factory=list)
    evidence_packs: List[DiscoveryEvidencePack] = field(default_factory=list)
    two_layer_candidates: List[TwoLayerScoredCandidate] = field(default_factory=list)
    thesis_cards: List[ThesisCard] = field(default_factory=list)
    discovery_track: str = "enricher"  # "enricher" | "anomaly_scan" | "dual_track"
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
