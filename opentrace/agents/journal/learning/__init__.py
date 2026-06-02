"""Reflection & learning: LLM-powered trade post-mortems and semantic lesson memory."""

try:  # pragma: no cover
    from opentrace.agents.journal.learning.reflection_agent import (
        ReflectionAgent,
        create_reflection_callback,
    )
except Exception:  # pragma: no cover
    ReflectionAgent = None  # type: ignore[assignment]
    create_reflection_callback = None  # type: ignore[assignment]

try:  # pragma: no cover
    from opentrace.agents.journal.learning.lesson_memory import LessonMemory
except Exception:  # pragma: no cover
    LessonMemory = None  # type: ignore[assignment]

__all__ = [
    "ReflectionAgent",
    "create_reflection_callback",
    "LessonMemory",
]
