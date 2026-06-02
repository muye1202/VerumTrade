from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, Iterable

from langchain_core.messages import ToolMessage

from opentrace.dataflows.interface import pop_vendor_telemetry


def _normalize_cache_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _normalize_cache_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_cache_value(v) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                number = Decimal(stripped)
            except InvalidOperation:
                return stripped
            if number == number.to_integral_value():
                return int(number)
            return float(number)
        return stripped
    return value


def make_tool_cache_key(tool_name: str, args: Any) -> str:
    payload = {
        "tool": str(tool_name or "").strip(),
        "args": _normalize_cache_value(args or {}),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _tool_map(tools: Iterable[Any]) -> Dict[str, Any]:
    mapped: Dict[str, Any] = {}
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name:
            mapped[str(name)] = tool
    return mapped


def _latest_tool_calls(state: Dict[str, Any]) -> list[dict]:
    messages = state.get("messages") or []
    if not messages:
        return []
    latest = messages[-1]
    calls = getattr(latest, "tool_calls", None)
    if calls:
        return [dict(call) for call in calls if isinstance(call, dict)]
    if isinstance(latest, dict) and latest.get("tool_calls"):
        return [dict(call) for call in latest.get("tool_calls") or [] if isinstance(call, dict)]
    return []


def _content_from_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _merge_cache_metrics(existing: Any, *, hit: bool, latency_ms: float) -> dict:
    metrics = dict(existing or {})
    metrics["hits"] = int(metrics.get("hits", 0) or 0) + (1 if hit else 0)
    metrics["misses"] = int(metrics.get("misses", 0) or 0) + (0 if hit else 1)
    metrics["tool_executions"] = int(metrics.get("tool_executions", 0) or 0) + (0 if hit else 1)
    metrics["vendor_calls_avoided"] = int(metrics.get("vendor_calls_avoided", 0) or 0) + (1 if hit else 0)
    metrics["latency_ms_saved_estimate"] = round(
        float(metrics.get("latency_ms_saved_estimate", 0.0) or 0.0)
        + (float(latency_ms or 0.0) if hit else 0.0),
        3,
    )
    return metrics


def create_cache_aware_tool_node(tools: Iterable[Any]) -> Callable[[Dict[str, Any]], Awaitable[dict]]:
    tools_by_name = _tool_map(tools)

    async def cache_aware_tool_node(state: Dict[str, Any]) -> dict:
        cache = dict(state.get("tool_result_cache") or {})
        metrics = dict(state.get("tool_cache_metrics") or {})
        telemetry = list(state.get("vendor_telemetry") or [])
        messages: list[ToolMessage] = []

        for call in _latest_tool_calls(state):
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = str(call.get("id") or f"cached-{len(messages) + 1}")
            cache_key = make_tool_cache_key(name, args)
            cached = cache.get(cache_key)

            if isinstance(cached, dict) and "content" in cached:
                messages.append(
                    ToolMessage(
                        content=str(cached.get("content") or ""),
                        name=name,
                        tool_call_id=call_id,
                    )
                )
                metrics = _merge_cache_metrics(
                    metrics,
                    hit=True,
                    latency_ms=float(cached.get("latency_ms", 0.0) or 0.0),
                )
                continue

            tool = tools_by_name.get(name)
            if tool is None:
                content = f"Error: {name} is not a valid tool."
                messages.append(
                    ToolMessage(content=content, name=name, tool_call_id=call_id, status="error")
                )
                metrics = _merge_cache_metrics(metrics, hit=False, latency_ms=0.0)
                continue

            pop_vendor_telemetry()
            start = time.perf_counter()
            status = "success"
            try:
                if hasattr(tool, "ainvoke"):
                    result = await tool.ainvoke(args)
                else:
                    result = tool.invoke(args)
                content = _content_from_result(result)
            except Exception as exc:
                status = "error"
                content = f"ToolError[{name}]: {type(exc).__name__}: {exc}"
            latency_ms = round((time.perf_counter() - start) * 1000.0, 3)
            vendor_events = pop_vendor_telemetry()
            telemetry.extend(vendor_events)

            messages.append(
                ToolMessage(
                    content=content,
                    name=name,
                    tool_call_id=call_id,
                    status=status,
                )
            )
            cache[cache_key] = {
                "tool": name,
                "args": _normalize_cache_value(args),
                "content": content,
                "status": status,
                "latency_ms": latency_ms,
                "vendor_telemetry": vendor_events,
                "created_at_monotonic": time.monotonic(),
            }
            metrics = _merge_cache_metrics(metrics, hit=False, latency_ms=latency_ms)

        return {
            "messages": messages,
            "tool_result_cache": cache,
            "tool_cache_metrics": metrics,
            "vendor_telemetry": telemetry,
        }

    return cache_aware_tool_node
