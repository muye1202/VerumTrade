from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional

from langchain_core.callbacks.base import BaseCallbackHandler


def _extract_model_name(serialized: Optional[dict], **kwargs) -> str:
    serialized = serialized or {}
    skw = serialized.get("kwargs") or {}
    for key in ("model_name", "model", "deployment_name"):
        value = skw.get(key) or kwargs.get(key)
        if value:
            return str(value)

    invocation = kwargs.get("invocation_params") or {}
    for key in ("model", "model_name"):
        value = invocation.get(key)
        if value:
            return str(value)

    return "unknown"


@dataclass
class _Snapshot:
    total: int
    by_model: Dict[str, int]


class _LLMCallCounter:
    def __init__(self):
        self._lock = Lock()
        self._total = 0
        self._by_model: Dict[str, int] = {}
        self._seen_run_ids: set[str] = set()

    def record_call(self, model_name: str, run_id: Any = None) -> None:
        run_key = str(run_id) if run_id is not None else None
        with self._lock:
            if run_key:
                if run_key in self._seen_run_ids:
                    return
                self._seen_run_ids.add(run_key)
                if len(self._seen_run_ids) > 200000:
                    # Keep memory bounded for long-lived processes.
                    self._seen_run_ids.clear()

            self._total += 1
            self._by_model[model_name] = self._by_model.get(model_name, 0) + 1

    def snapshot(self) -> _Snapshot:
        with self._lock:
            return _Snapshot(
                total=self._total,
                by_model=dict(self._by_model),
            )


_COUNTER = _LLMCallCounter()


class LLMMetricsCallbackHandler(BaseCallbackHandler):
    """Global callback handler that tracks exact LLM API-call counts."""

    ignore_chain = True
    ignore_agent = True
    ignore_tool = True
    ignore_retriever = True
    raise_error = False

    def on_llm_start(self, serialized: dict, prompts, **kwargs: Any) -> None:
        model_name = _extract_model_name(serialized, **kwargs)
        _COUNTER.record_call(model_name, run_id=kwargs.get("run_id"))

    def on_chat_model_start(self, serialized: dict, messages, **kwargs: Any) -> None:
        model_name = _extract_model_name(serialized, **kwargs)
        _COUNTER.record_call(model_name, run_id=kwargs.get("run_id"))


_METRICS_HANDLER = LLMMetricsCallbackHandler()


def attach_llm_metrics_handler(llm: Any) -> None:
    callbacks = list(getattr(llm, "callbacks", None) or [])
    if not any(cb is _METRICS_HANDLER for cb in callbacks):
        callbacks.append(_METRICS_HANDLER)
        llm.callbacks = callbacks


def snapshot_llm_api_calls() -> Dict[str, Any]:
    snap = _COUNTER.snapshot()
    return {
        "total": snap.total,
        "by_model": snap.by_model,
    }


def diff_llm_api_calls(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_total = int(before.get("total", 0) or 0)
    after_total = int(after.get("total", 0) or 0)
    before_models = before.get("by_model", {}) or {}
    after_models = after.get("by_model", {}) or {}

    model_keys = set(before_models.keys()) | set(after_models.keys())
    delta_models = {
        key: int(after_models.get(key, 0) or 0) - int(before_models.get(key, 0) or 0)
        for key in model_keys
    }
    delta_models = {k: v for k, v in delta_models.items() if v != 0}

    return {
        "llm_api_calls_total": max(0, after_total - before_total),
        "llm_api_calls_by_model": delta_models,
    }
