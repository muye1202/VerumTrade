"""
Data models for the Trade Journal system.

All models are plain dataclasses with to_dict/from_dict for SQLite serialization.
No ORM — we keep it lightweight and explicit.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlertType(str, enum.Enum):
    """Categories of journal alerts."""

    STOP_HIT = "stop_hit"  # Price breached stop-loss level
    TARGET_HIT = "target_hit"  # Price reached a take-profit target
    TIME_STOP = "time_stop"  # Holding period exceeded planned horizon
    TRAILING_TIGHTENED = "trailing_tightened"  # Trailing stop auto-tightened
    THESIS_INVALIDATED = "thesis_invalidated"  # Catalyst/setup broke down
    GAP_RISK = "gap_risk"  # Large overnight gap detected
    POSITION_CLOSED = "position_closed"  # Brokerage shows position gone
    CUSTOM = "custom"


class ThesisStatus(str, enum.Enum):
    """Lifecycle of a trade thesis."""

    ACTIVE = "active"  # Position is open, being monitored
    STOPPED_OUT = "stopped_out"  # Exit triggered by stop-loss
    TARGET_REACHED = "target_reached"  # Exited at a profit target
    TIME_STOPPED = "time_stopped"  # Exited because holding period expired
    MANUALLY_CLOSED = "manually_closed"  # User or agent closed early
    INVALIDATED = "invalidated"  # Thesis premise broke before entry or during hold
    CLOSED = "closed"  # Generic closed (position no longer held)


class ActionDecisionType(str, enum.Enum):
    """Supported journal action decisions."""

    NO_ACTION = "no_action"
    REDUCE_POSITION = "reduce_position"
    EXIT_POSITION = "exit_position"
    TAKE_PROFIT_PARTIAL = "take_profit_partial"
    TAKE_PROFIT_FULL = "take_profit_full"


class ActionReasonCode(str, enum.Enum):
    """Reason codes for action decisions."""

    STOP_BREACH = "stop_breach"
    TIME_STOP_EXPIRED = "time_stop_expired"
    TARGET1_REACHED = "target1_reached"
    TARGET2_REACHED = "target2_reached"
    GAP_ADVERSE = "gap_adverse"
    VOLATILITY_SPIKE = "volatility_spike"
    RELATIVE_STRENGTH_BREAKDOWN = "relative_strength_breakdown"
    NONE = "none"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


@dataclass
class TradeThesis:
    """
    The structured thesis captured at trade entry time.

    Extracted from the multi-agent analysis state — entry zone, stop, targets,
    time-stop, catalyst, and the raw rationale from each agent stage.
    """

    # Identity
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ticker: str = ""
    trade_date: str = ""  # ISO date the analysis was run

    # Decision
    action: str = "HOLD"  # BUY | SELL | HOLD
    conviction: Optional[float] = None  # 0-100 from agent

    # Entry parameters
    entry_price: Optional[float] = None  # Actual fill price (set after execution)
    entry_price_source: Optional[str] = None  # filled_avg_price|broker_avg_entry|submission_quote|unknown
    entry_price_pending: bool = False  # True until order fill reconciliation completes
    last_reconciled_at: Optional[str] = None  # ISO timestamp of last order reconciliation
    entry_zone_low: Optional[float] = None  # Planned entry zone lower bound
    entry_zone_high: Optional[float] = None  # Planned entry zone upper bound

    # Risk management
    stop_loss: Optional[float] = None  # Hard stop-loss price
    stop_loss_pct: Optional[float] = None  # Stop as % from entry (fallback)
    trailing_stop_pct: Optional[float] = None  # If trailing stop was specified

    # Targets
    target_1: Optional[float] = None  # First take-profit level
    target_2: Optional[float] = None  # Second take-profit level
    risk_reward_ratio: Optional[float] = None  # Computed R:R at entry

    # Time management
    time_horizon_label: Optional[str] = None  # e.g. "swing (1-3 weeks)"
    time_stop_date: Optional[str] = None  # ISO date: exit if still holding by this date
    holding_days_planned: Optional[int] = None  # Planned holding period in trading days

    # Thesis content
    catalyst: Optional[str] = None  # The specific catalyst or setup
    regime: Optional[str] = None  # Trend/range/breakout regime at entry
    key_risks: Optional[str] = None  # Top risks identified by agents
    invalidation_trigger: Optional[str] = None  # What would break the thesis

    # Order details (from execution)
    order_type: Optional[str] = None  # MARKET, LIMIT, STOP, etc.
    order_id: Optional[str] = None  # Alpaca order ID
    quantity: Optional[int] = None
    position_size_pct: Optional[float] = None  # % of portfolio

    # Agent reports (compressed summaries, not full text)
    market_analyst_summary: Optional[str] = None
    fundamentals_summary: Optional[str] = None
    news_summary: Optional[str] = None
    risk_judge_summary: Optional[str] = None
    final_decision_text: Optional[str] = None  # Raw FINAL TRADING DECISION block

    # Lifecycle
    status: str = ThesisStatus.ACTIVE.value
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeThesis":
        # Filter to only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class PositionSnapshot:
    """
    A point-in-time snapshot of a monitored position.

    Captured by the scheduler on each monitoring tick. Provides the time-series
    data needed to compute slippage, path dependency, and max adverse excursion.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thesis_id: str = ""  # FK to TradeThesis
    ticker: str = ""

    # Price data
    current_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    vwap: Optional[float] = None  # If available from intraday data

    # Position data (from brokerage)
    quantity: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pl: Optional[float] = None
    unrealized_pl_pct: Optional[float] = None
    cost_basis: Optional[float] = None

    # Thesis tracking
    distance_to_stop_pct: Optional[float] = None  # How far from stop-loss (%)
    distance_to_target1_pct: Optional[float] = None  # How far from target 1 (%)
    holding_days_elapsed: Optional[int] = None  # Trading days since entry
    holding_days_remaining: Optional[int] = None  # Trading days until time-stop

    # Market context
    spy_change_pct: Optional[float] = None  # SPY change since thesis entry
    relative_strength: Optional[float] = None  # Stock return - SPY return

    # Max adverse/favorable excursion (updated cumulatively)
    max_adverse_excursion_pct: Optional[float] = None  # Worst drawdown from entry
    max_favorable_excursion_pct: Optional[float] = None  # Best run-up from entry

    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PositionSnapshot":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class JournalAlert:
    """
    An alert generated when a monitored position hits a thesis parameter.

    Alerts are the journal's "output" — they inform the user and can trigger
    automated actions (exit orders, re-analysis, reflection).
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thesis_id: str = ""  # FK to TradeThesis
    ticker: str = ""
    alert_type: str = AlertType.CUSTOM.value
    severity: str = "info"  # info | warning | critical

    # What happened
    message: str = ""
    trigger_price: Optional[float] = None  # Price that triggered the alert
    threshold_price: Optional[float] = None  # The thesis level that was breached

    # Context
    current_price: Optional[float] = None
    unrealized_pl_pct: Optional[float] = None
    holding_days: Optional[int] = None

    # Action taken (or recommended)
    action_taken: Optional[str] = None  # e.g. "submitted SELL order", "flagged for review"
    action_recommended: Optional[str] = None  # e.g. "tighten stop to $X"

    acknowledged: bool = False
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JournalAlert":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class JournalActionDecision:
    """A per-tick action decision generated by the journal advisor."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thesis_id: str = ""
    ticker: str = ""
    tick_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    decision_type: str = ActionDecisionType.NO_ACTION.value
    reason_code: str = ActionReasonCode.NONE.value
    confidence: float = 0.0
    recommended_qty_pct: Optional[float] = None
    dry_run: bool = True
    gates_passed: bool = False
    gate_block_reasons: Optional[str] = None  # JSON-encoded list[str]
    context_summary: Optional[str] = None  # JSON-encoded dict
    linked_alert_ids: Optional[str] = None  # JSON-encoded list[str]
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JournalActionDecision":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class JournalActionExecution:
    """Execution attempt/result generated from a journal action decision."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    decision_id: str = ""
    thesis_id: str = ""
    ticker: str = ""
    submitted_signal: str = "SELL"
    submitted_qty: Optional[float] = None
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: str = "dry_run"  # dry_run|submitted|rejected|failed
    broker_order_id: Optional[str] = None
    error: Optional[str] = None
    raw_result_json: Optional[str] = None  # JSON-encoded broker response
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JournalActionExecution":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TradeOutcome:
    """
    Structured outcome record computed when a position closes.

    This is the primary input to the reflection/learning loop — it tells the
    system not just P&L but *why* the trade worked or didn't.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thesis_id: str = ""  # FK to TradeThesis
    ticker: str = ""

    # P&L
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    realized_pl: Optional[float] = None  # Dollar P&L
    realized_pl_pct: Optional[float] = None  # Percentage return
    holding_days: Optional[int] = None  # Actual trading days held

    # Execution quality
    entry_slippage_pct: Optional[float] = None  # Fill vs. planned entry midpoint
    exit_slippage_pct: Optional[float] = None  # Fill vs. thesis target/stop

    # Path analysis
    max_adverse_excursion_pct: Optional[float] = None  # Worst drawdown from entry
    max_favorable_excursion_pct: Optional[float] = None  # Best run-up from entry
    capture_ratio: Optional[float] = None  # realized / max_favorable (how much of the move you kept)

    # Benchmark comparison
    spy_return_pct: Optional[float] = None  # SPY return over same period
    alpha_pct: Optional[float] = None  # Stock return - SPY return

    # Thesis accuracy
    exit_reason: Optional[str] = None  # stop_hit | target_reached | time_stop | manual | invalidated
    thesis_correct: Optional[bool] = None  # Did the stock move in the predicted direction?
    catalyst_materialized: Optional[bool] = None  # Did the expected catalyst occur?
    target_reached: Optional[bool] = None  # Did price reach any target level?
    stop_triggered: Optional[bool] = None  # Was stop-loss hit?
    time_stop_triggered: Optional[bool] = None  # Did holding period expire?

    # Risk metrics
    risk_reward_actual: Optional[float] = None  # Actual R:R vs. planned
    risk_multiple: Optional[float] = None  # P&L expressed as multiples of risk (R)

    # Agent performance notes (for reflection)
    market_analyst_accuracy: Optional[str] = None  # Brief note on regime/levels accuracy
    news_analyst_accuracy: Optional[str] = None  # Were catalysts correctly identified?
    risk_judge_accuracy: Optional[str] = None  # Was sizing/risk assessment good?

    # Metadata
    closed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # --- Hook for reflection/learning loop ---
    # When implemented, this will be populated by the reflection system.
    # Left as None for now; the OutcomeRecorder sets the structured fields above,
    # and a future ReflectionAgent can consume this outcome to call
    # TradingAgentsGraph.reflect_and_remember() with structured returns_losses.
    reflection_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeOutcome":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TradeLesson:
    """
    Structured lesson extracted from a completed trade by the reflection agent.

    Combines machine-queryable tags with human-readable wisdom. Stored in ChromaDB
    for semantic retrieval during future trading decisions.
    """

    # Identity
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thesis_id: str = ""  # FK to TradeThesis
    outcome_id: str = ""  # FK to TradeOutcome
    ticker: str = ""
    trade_date: str = ""

    # Human-readable lesson
    lesson_text: str = ""  # 1-2 sentence summary suitable for memory retrieval

    # Structured analysis
    what_worked: List[str] = field(default_factory=list)  # Things agents got right
    what_failed: List[str] = field(default_factory=list)  # Things agents got wrong

    # Agent accuracy assessment
    agent_accuracy: Dict[str, str] = field(default_factory=dict)  # {agent: assessment}
    most_accurate_agent: Optional[str] = None  # market|fundamentals|news|risk_judge
    least_accurate_agent: Optional[str] = None

    # Thesis accuracy
    regime_correct: Optional[bool] = None  # Was regime classification correct?
    catalyst_materialized: Optional[bool] = None  # Did expected catalyst occur?

    # Categorization for retrieval
    category: str = ""  # e.g., "momentum_in_uptrend", "earnings_catalyst"
    tags: List[str] = field(default_factory=list)  # Searchable tags
    confidence: float = 50.0  # 0-100 confidence in this lesson

    # Trade context (copied for standalone retrieval)
    action: str = ""  # BUY | SELL
    realized_pl_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    risk_multiple: Optional[float] = None  # R-multiple

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeLesson":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_embedding_text(self) -> str:
        """Generate text for embedding/semantic search."""
        parts = [
            f"Ticker: {self.ticker}",
            f"Action: {self.action}",
            f"Category: {self.category}",
            f"Lesson: {self.lesson_text}",
        ]
        if self.what_worked:
            parts.append(f"What worked: {'; '.join(self.what_worked)}")
        if self.what_failed:
            parts.append(f"What failed: {'; '.join(self.what_failed)}")
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")
        return " | ".join(parts)
