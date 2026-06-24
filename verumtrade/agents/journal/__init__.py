"""
Trade Journal Agent — closes the feedback loop between execution and learning.

The journal tracks every trade from thesis → execution → outcome, monitors
positions against their original thesis parameters, and generates structured
outcome records that can feed back into the reflection/memory system.

Core components:
    JournalStore        SQLite-backed persistence for theses, snapshots, alerts, outcomes
    ThesisExtractor     Pulls structured thesis parameters from agent analysis state
    PositionMonitor     Checks live positions against thesis (stop/target/time-stop)
    OutcomeRecorder     Computes structured P&L and thesis-accuracy when positions close
    JournalScheduler    APScheduler-based daemon that wakes the monitor periodically
    ReflectionAgent     LLM-powered analysis that extracts lessons from completed trades
    LessonMemory        ChromaDB-backed vector storage for semantic lesson retrieval
"""

from verumtrade.agents.journal.core.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    TradeOutcome,
    TradeLesson,
    AlertType,
    ThesisStatus,
    ActionDecisionType,
    ActionReasonCode,
    JournalActionDecision,
    JournalActionExecution,
)
from verumtrade.agents.journal.core.store import JournalStore
from verumtrade.agents.journal.portfolio.portfolio_sync import sync_missing_positions
from verumtrade.agents.journal.ingestion.report_import import import_scheduled_reports
from verumtrade.agents.journal.ingestion.thesis_extractor import ThesisExtractor
from verumtrade.agents.journal.monitoring.monitor import PositionMonitor
from verumtrade.agents.journal.monitoring.outcome import OutcomeRecorder
from verumtrade.agents.journal.evaluation.news_event_inference import (
    infer_event_flags,
    event_inference_enabled,
)

# Optional components may require extra runtime dependencies (e.g., tzdata/chromadb).
try:  # pragma: no cover
    from verumtrade.agents.journal.monitoring.scheduler import JournalScheduler
except Exception:  # pragma: no cover
    JournalScheduler = None  # type: ignore[assignment]

try:  # pragma: no cover
    from verumtrade.agents.journal.learning.reflection_agent import (
        ReflectionAgent,
        create_reflection_callback,
    )
except Exception:  # pragma: no cover
    ReflectionAgent = None  # type: ignore[assignment]
    create_reflection_callback = None  # type: ignore[assignment]

try:  # pragma: no cover
    from verumtrade.agents.journal.learning.lesson_memory import LessonMemory
except Exception:  # pragma: no cover
    LessonMemory = None  # type: ignore[assignment]

try:  # pragma: no cover
    from verumtrade.agents.journal.execution.execution_advisor import (
        JournalExecutionAdvisor,
        ActionContext,
    )
except Exception:  # pragma: no cover
    JournalExecutionAdvisor = None  # type: ignore[assignment]
    ActionContext = None  # type: ignore[assignment]

try:  # pragma: no cover
    from verumtrade.agents.journal.execution.execution_policy import (
        JournalExecutionPolicy,
        PolicyResult,
    )
except Exception:  # pragma: no cover
    JournalExecutionPolicy = None  # type: ignore[assignment]
    PolicyResult = None  # type: ignore[assignment]

__all__ = [
    "TradeThesis",
    "PositionSnapshot",
    "JournalAlert",
    "TradeOutcome",
    "TradeLesson",
    "AlertType",
    "ThesisStatus",
    "ActionDecisionType",
    "ActionReasonCode",
    "JournalActionDecision",
    "JournalActionExecution",
    "JournalStore",
    "sync_missing_positions",
    "import_scheduled_reports",
    "ThesisExtractor",
    "PositionMonitor",
    "OutcomeRecorder",
    "infer_event_flags",
    "event_inference_enabled",
    "JournalScheduler",
    "ReflectionAgent",
    "LessonMemory",
    "create_reflection_callback",
    "JournalExecutionAdvisor",
    "ActionContext",
    "JournalExecutionPolicy",
    "PolicyResult",
]
