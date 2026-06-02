from __future__ import annotations

from typing import Any, Sequence


def bind_tools_parallel_safe(llm: Any, tools: Sequence[Any]):
    """Bind tools with provider-safe parallel tool-call preference."""
    try:
        return llm.bind_tools(tools, parallel_tool_calls=True)
    except TypeError:
        return llm.bind_tools(tools)
    except Exception:
        # Some providers reject unknown kwargs at runtime.
        return llm.bind_tools(tools)

