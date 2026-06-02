"""
OpenTrace agent factory exports.

This module intentionally avoids importing heavy optional dependencies at import time.
Downstream code can still use `from opentrace.agents import create_market_analyst`
and friends; symbols are loaded lazily on first access.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "create_msg_delete": ("opentrace.agents.utils.agent_runtime.agent_utils", "create_msg_delete"),
    "create_force_finalize": ("opentrace.agents.utils.agent_runtime.agent_utils", "create_force_finalize"),
    "AgentState": ("opentrace.agents.utils.agent_runtime.agent_states", "AgentState"),
    "InvestDebateState": ("opentrace.agents.utils.agent_runtime.agent_states", "InvestDebateState"),
    "RiskDebateState": ("opentrace.agents.utils.agent_runtime.agent_states", "RiskDebateState"),
    "FinancialSituationMemory": ("opentrace.agents.utils.memory.memory", "FinancialSituationMemory"),
    "create_fundamentals_analyst": ("opentrace.agents.analysts.fundamentals_analyst", "create_fundamentals_analyst"),
    "create_catalyst_event_analyst": ("opentrace.agents.analysts.catalyst_event_analyst", "create_catalyst_event_analyst"),
    "create_market_analyst": ("opentrace.agents.analysts.market_analyst", "create_market_analyst"),
    "create_news_analyst": ("opentrace.agents.analysts.news_analyst", "create_news_analyst"),
    "create_social_media_analyst": ("opentrace.agents.analysts.social_media_analyst", "create_social_media_analyst"),
    "create_bear_researcher": ("opentrace.agents.researchers.bear_researcher", "create_bear_researcher"),
    "create_bull_researcher": ("opentrace.agents.researchers.bull_researcher", "create_bull_researcher"),
    "create_risky_debator": ("opentrace.agents.risk_mgmt.aggresive_debator", "create_risky_debator"),
    "create_safe_debator": ("opentrace.agents.risk_mgmt.conservative_debator", "create_safe_debator"),
    "create_neutral_debator": ("opentrace.agents.risk_mgmt.neutral_debator", "create_neutral_debator"),
    "create_research_manager": ("opentrace.agents.managers.research_manager", "create_research_manager"),
    "create_risk_manager": ("opentrace.agents.managers.risk_manager", "create_risk_manager"),
    "create_trader": ("opentrace.agents.trader.trader", "create_trader"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr = _EXPORTS[name]
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(_EXPORTS.keys())))


