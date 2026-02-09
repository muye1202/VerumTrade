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
"""

from tradingagents.journal.models import (
    TradeThesis,
    PositionSnapshot,
    JournalAlert,
    TradeOutcome,
    AlertType,
    ThesisStatus,
)
from tradingagents.journal.store import JournalStore
from tradingagents.journal.thesis_extractor import ThesisExtractor
from tradingagents.journal.monitor import PositionMonitor
from tradingagents.journal.outcome import OutcomeRecorder
from tradingagents.journal.scheduler import JournalScheduler

__all__ = [
    "TradeThesis",
    "PositionSnapshot",
    "JournalAlert",
    "TradeOutcome",
    "AlertType",
    "ThesisStatus",
    "JournalStore",
    "ThesisExtractor",
    "PositionMonitor",
    "OutcomeRecorder",
    "JournalScheduler",
]
