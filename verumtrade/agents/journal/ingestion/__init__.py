"""Trade ingestion: extracting and importing trade theses into the journal."""

from verumtrade.agents.journal.ingestion.thesis_extractor import ThesisExtractor
from verumtrade.agents.journal.ingestion.hooks import (
    capture_trade_thesis,
    capture_from_propagate_and_execute,
    refresh_active_thesis_from_portfolio_analysis,
)
from verumtrade.agents.journal.ingestion.report_import import import_scheduled_reports

__all__ = [
    "ThesisExtractor",
    "capture_trade_thesis",
    "capture_from_propagate_and_execute",
    "refresh_active_thesis_from_portfolio_analysis",
    "import_scheduled_reports",
]
