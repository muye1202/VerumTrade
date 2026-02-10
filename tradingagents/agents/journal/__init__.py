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

from tradingagents.agents.journal.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    TradeOutcome,
    TradeLesson,
    AlertType,
    ThesisStatus,
)
from tradingagents.agents.journal.store import JournalStore
from tradingagents.agents.journal.thesis_extractor import ThesisExtractor
from tradingagents.agents.journal.monitor import PositionMonitor
from tradingagents.agents.journal.outcome import OutcomeRecorder
from tradingagents.agents.journal.scheduler import JournalScheduler
from tradingagents.agents.journal.reflection_agent import ReflectionAgent, create_reflection_callback
from tradingagents.agents.journal.lesson_memory import LessonMemory

__all__ = [
    "TradeThesis",
    "PositionSnapshot",
    "JournalAlert",
    "TradeOutcome",
    "TradeLesson",
    "AlertType",
    "ThesisStatus",
    "JournalStore",
    "ThesisExtractor",
    "PositionMonitor",
    "OutcomeRecorder",
    "JournalScheduler",
    "ReflectionAgent",
    "LessonMemory",
    "create_reflection_callback",
]

