# TradingAgents/graph/__init__.py

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trading_graph import TradingAgentsGraph
    from .conditional_logic import ConditionalLogic
    from .setup import GraphSetup
    from .propagation import Propagator
    from .reflection import Reflector
    from .signal_processing import SignalProcessor

__all__ = [
    "TradingAgentsGraph",
    "ConditionalLogic",
    "GraphSetup",
    "Propagator",
    "Reflector",
    "SignalProcessor",
]


def __getattr__(name: str):
    if name == "TradingAgentsGraph":
        from .trading_graph import TradingAgentsGraph as value

        return value
    if name == "ConditionalLogic":
        from .conditional_logic import ConditionalLogic as value

        return value
    if name == "GraphSetup":
        from .setup import GraphSetup as value

        return value
    if name == "Propagator":
        from .propagation import Propagator as value

        return value
    if name == "Reflector":
        from .reflection import Reflector as value

        return value
    if name == "SignalProcessor":
        from .signal_processing import SignalProcessor as value

        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
