from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


ALLOWED_EVENT_RISK_RATINGS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ALLOWED_RECOMMENDED_ACTIONS = {
    "continue_analysis",
    "rerun_full_analysis",
    "risk_judge_review",
    "freeze_new_buys",
    "reduce_position",
    "exit_review",
    "watchlist_only",
    "ignore_low_materiality",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _float_default(value: Any, default: float = 0.0) -> float:
    parsed = _float_or_none(value)
    return default if parsed is None else parsed


def _clamp01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float_default(value, default)))


@dataclass
class EventRecord:
    event_id: str = ""
    ticker: str = ""
    event_type: str = "other"
    event_time: str | None = None
    detected_at: str = ""
    source: str = ""
    title: str = ""
    summary: str = ""
    url: str | None = None
    materiality_score: float = 0.0
    novelty_score: float = 0.0
    sentiment_score: float | None = None
    confidence: float = 0.0
    relevance_score: float = 0.0
    matched_aliases: list[str] = field(default_factory=list)
    mentioned_tickers: list[str] = field(default_factory=list)
    contamination_flags: list[str] = field(default_factory=list)
    quarantine_reason: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "EventRecord":
        d = _as_dict(data)
        return cls(
            event_id=str(d.get("event_id") or ""),
            ticker=str(d.get("ticker") or ""),
            event_type=str(d.get("event_type") or "other"),
            event_time=_str_or_none(d.get("event_time")),
            detected_at=str(d.get("detected_at") or ""),
            source=str(d.get("source") or ""),
            title=str(d.get("title") or ""),
            summary=str(d.get("summary") or ""),
            url=_str_or_none(d.get("url")),
            materiality_score=_clamp01(d.get("materiality_score")),
            novelty_score=_clamp01(d.get("novelty_score")),
            sentiment_score=_float_or_none(d.get("sentiment_score")),
            confidence=_clamp01(d.get("confidence")),
            relevance_score=_clamp01(d.get("relevance_score")),
            matched_aliases=_str_list(d.get("matched_aliases")),
            mentioned_tickers=_str_list(d.get("mentioned_tickers")),
            contamination_flags=_str_list(d.get("contamination_flags")),
            quarantine_reason=_str_or_none(d.get("quarantine_reason")),
        )


@dataclass
class FilingRecord:
    accession_number: str = ""
    cik: str = ""
    form_type: str = ""
    filing_date: str = ""
    report_date: str | None = None
    primary_document_url: str = ""
    filing_summary: str | None = None
    extracted_signals: list[str] = field(default_factory=list)
    materiality_score: float = 0.0

    @classmethod
    def from_dict(cls, data: Any) -> "FilingRecord":
        d = _as_dict(data)
        return cls(
            accession_number=str(d.get("accession_number") or ""),
            cik=str(d.get("cik") or ""),
            form_type=str(d.get("form_type") or ""),
            filing_date=str(d.get("filing_date") or ""),
            report_date=_str_or_none(d.get("report_date")),
            primary_document_url=str(d.get("primary_document_url") or ""),
            filing_summary=_str_or_none(d.get("filing_summary")),
            extracted_signals=_str_list(d.get("extracted_signals")),
            materiality_score=_clamp01(d.get("materiality_score")),
        )


@dataclass
class MacroEventRecord:
    event_name: str = ""
    release_time: str = ""
    series_or_release_id: str | None = None
    actual: float | None = None
    consensus: float | None = None
    previous: float | None = None
    surprise_score: float | None = None
    affected_sectors: list[str] = field(default_factory=list)
    relevance_to_ticker: float = 0.0

    @classmethod
    def from_dict(cls, data: Any) -> "MacroEventRecord":
        d = _as_dict(data)
        return cls(
            event_name=str(d.get("event_name") or ""),
            release_time=str(d.get("release_time") or ""),
            series_or_release_id=_str_or_none(d.get("series_or_release_id")),
            actual=_float_or_none(d.get("actual")),
            consensus=_float_or_none(d.get("consensus")),
            previous=_float_or_none(d.get("previous")),
            surprise_score=_float_or_none(d.get("surprise_score")),
            affected_sectors=_str_list(d.get("affected_sectors")),
            relevance_to_ticker=_clamp01(d.get("relevance_to_ticker")),
        )


@dataclass
class MarketContext:
    last_close: float | None = None
    one_day_return_pct: float | None = None
    five_day_return_pct: float | None = None
    volume_ratio: float | None = None
    price_volume_shock: bool = False
    summary: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "MarketContext":
        d = _as_dict(data)
        return cls(
            last_close=_float_or_none(d.get("last_close")),
            one_day_return_pct=_float_or_none(d.get("one_day_return_pct")),
            five_day_return_pct=_float_or_none(d.get("five_day_return_pct")),
            volume_ratio=_float_or_none(d.get("volume_ratio")),
            price_volume_shock=bool(d.get("price_volume_shock", False)),
            summary=str(d.get("summary") or ""),
        )


@dataclass
class PositionContext:
    has_position: bool = False
    position_size_pct: float | None = None
    cost_basis: float | None = None
    unrealized_pnl_pct: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    max_position_size_pct: float | None = None
    holding_period: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "PositionContext":
        d = _as_dict(data)
        return cls(
            has_position=bool(d.get("has_position", False)),
            position_size_pct=_float_or_none(d.get("position_size_pct")),
            cost_basis=_float_or_none(d.get("cost_basis")),
            unrealized_pnl_pct=_float_or_none(d.get("unrealized_pnl_pct")),
            stop_loss=_float_or_none(d.get("stop_loss")),
            target_price=_float_or_none(d.get("target_price")),
            max_position_size_pct=_float_or_none(d.get("max_position_size_pct")),
            holding_period=_str_or_none(d.get("holding_period")),
        )


@dataclass
class PriorThesis:
    decision: str = ""
    thesis_summary: str = ""
    bull_points: list[str] = field(default_factory=list)
    bear_points: list[str] = field(default_factory=list)
    thesis_dependencies: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    time_horizon: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "PriorThesis":
        d = _as_dict(data)
        return cls(
            decision=str(d.get("decision") or ""),
            thesis_summary=str(d.get("thesis_summary") or ""),
            bull_points=_str_list(d.get("bull_points")),
            bear_points=_str_list(d.get("bear_points")),
            thesis_dependencies=_str_list(d.get("thesis_dependencies")),
            invalidation_conditions=_str_list(d.get("invalidation_conditions")),
            time_horizon=str(d.get("time_horizon") or ""),
            created_at=str(d.get("created_at") or ""),
        )


@dataclass
class CatalystEventBundle:
    ticker: str
    company_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    as_of: str = ""
    recent_events: list[EventRecord] = field(default_factory=list)
    quarantined_events: list[EventRecord] = field(default_factory=list)
    dropped_event_count: int = 0
    upcoming_events: list[EventRecord] = field(default_factory=list)
    recent_filings: list[FilingRecord] = field(default_factory=list)
    macro_events: list[MacroEventRecord] = field(default_factory=list)
    market_context: MarketContext = field(default_factory=MarketContext)
    position_context: PositionContext | None = None
    prior_thesis: PriorThesis | None = None
    source_quality: dict[str, Any] = field(default_factory=dict)
    bundle_quality: dict[str, Any] = field(default_factory=dict)
    data_freshness: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "CatalystEventBundle":
        d = _as_dict(data)
        position = d.get("position_context")
        thesis = d.get("prior_thesis")
        return cls(
            ticker=str(d.get("ticker") or d.get("symbol") or ""),
            company_name=_str_or_none(d.get("company_name")),
            aliases=_str_list(d.get("aliases")),
            as_of=str(d.get("as_of") or d.get("date") or ""),
            recent_events=[EventRecord.from_dict(item) for item in _as_list(d.get("recent_events"))],
            quarantined_events=[EventRecord.from_dict(item) for item in _as_list(d.get("quarantined_events"))],
            dropped_event_count=int(_float_default(d.get("dropped_event_count"), 0.0)),
            upcoming_events=[EventRecord.from_dict(item) for item in _as_list(d.get("upcoming_events"))],
            recent_filings=[FilingRecord.from_dict(item) for item in _as_list(d.get("recent_filings"))],
            macro_events=[MacroEventRecord.from_dict(item) for item in _as_list(d.get("macro_events"))],
            market_context=MarketContext.from_dict(d.get("market_context")),
            position_context=PositionContext.from_dict(position) if isinstance(position, dict) else None,
            prior_thesis=PriorThesis.from_dict(thesis) if isinstance(thesis, dict) else None,
            source_quality=_as_dict(d.get("source_quality")),
            bundle_quality=_as_dict(d.get("bundle_quality")),
            data_freshness=_as_dict(d.get("data_freshness")),
        )

    @classmethod
    def from_json(cls, payload: str) -> "CatalystEventBundle":
        return cls.from_dict(json.loads(payload))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass
class EvidenceItem:
    source: str = ""
    event_type: str = ""
    date: str = ""
    claim: str = ""
    thesis_impact: str = ""
    confidence: float = 0.0
    url: str | None = None
    source_event_id: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceItem":
        d = _as_dict(data)
        return cls(
            source=str(d.get("source") or ""),
            event_type=str(d.get("event_type") or ""),
            date=str(d.get("date") or ""),
            claim=str(d.get("claim") or ""),
            thesis_impact=str(d.get("thesis_impact") or ""),
            confidence=_clamp01(d.get("confidence")),
            url=_str_or_none(d.get("url")),
            source_event_id=_str_or_none(d.get("source_event_id") or d.get("event_id")),
        )


@dataclass
class CatalystEventReport:
    ticker: str
    as_of: str
    event_risk_rating: str = "MEDIUM"
    catalyst_score: float = 0.0
    thesis_break_score: float = 0.0
    thesis_support_score: float = 0.0
    near_term_catalysts: list[str] = field(default_factory=list)
    recent_material_events: list[str] = field(default_factory=list)
    thesis_supporting_events: list[str] = field(default_factory=list)
    thesis_breaking_events: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    recommended_action: str = "continue_analysis"
    action_rationale: str = ""
    risk_controls: list[str] = field(default_factory=list)
    evidence_table: list[EvidenceItem] = field(default_factory=list)
    fallback_mode: str = ""
    data_quality_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "CatalystEventReport":
        d = _as_dict(data)
        rating = str(d.get("event_risk_rating") or "MEDIUM").strip().upper()
        if rating not in ALLOWED_EVENT_RISK_RATINGS:
            rating = "MEDIUM"
        action = str(d.get("recommended_action") or "continue_analysis").strip()
        if action not in ALLOWED_RECOMMENDED_ACTIONS:
            action = "continue_analysis"
        return cls(
            ticker=str(d.get("ticker") or ""),
            as_of=str(d.get("as_of") or ""),
            event_risk_rating=rating,
            catalyst_score=_clamp01(d.get("catalyst_score")),
            thesis_break_score=_clamp01(d.get("thesis_break_score")),
            thesis_support_score=_clamp01(d.get("thesis_support_score")),
            near_term_catalysts=_str_list(d.get("near_term_catalysts")),
            recent_material_events=_str_list(d.get("recent_material_events")),
            thesis_supporting_events=_str_list(d.get("thesis_supporting_events")),
            thesis_breaking_events=_str_list(d.get("thesis_breaking_events")),
            unresolved_questions=_str_list(d.get("unresolved_questions")),
            recommended_action=action,
            action_rationale=str(d.get("action_rationale") or ""),
            risk_controls=_str_list(d.get("risk_controls")),
            evidence_table=[EvidenceItem.from_dict(item) for item in _as_list(d.get("evidence_table"))],
            fallback_mode=str(d.get("fallback_mode") or ""),
            data_quality_notes=_str_list(d.get("data_quality_notes")),
        )

    @classmethod
    def from_json(cls, payload: str) -> "CatalystEventReport":
        return cls.from_dict(json.loads(payload))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
