"""Replay mode: stream a recorded VerumTrade run without LLM or data-vendor keys.

Recordings live in ``demos/replays/*.json`` and are produced by
``demos/replays/build_replay_from_report.py``. Each recording holds the same
``system`` / ``chunk`` events the live analysis WebSocket sends, so the web UI
renders a replay exactly like a live run.

Environment flags:
    VERUMTRADE_DEMO_MODE=1        Serve every analysis request from a replay
                                  (for hosted demos or keyless Docker runs).
    VERUMTRADE_REPLAY_SPEED=2.0   Playback speed multiplier (default 1.0).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from verumtrade.graph.reasoning_trace import build_agent_reasoning_trace

REPLAY_DIR = Path(__file__).resolve().parents[1] / "demos" / "replays"
DEFAULT_REPLAY_ID = "MU-2026-06-23"
_REPLAY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TRUTHY = {"1", "true", "yes", "on"}


def demo_mode_enabled() -> bool:
    return os.getenv("VERUMTRADE_DEMO_MODE", "").strip().lower() in _TRUTHY


def replay_speed() -> float:
    try:
        speed = float(os.getenv("VERUMTRADE_REPLAY_SPEED", "1.0"))
    except ValueError:
        return 1.0
    return speed if speed > 0 else 1.0


def _replay_path(replay_id: str) -> Path | None:
    if not _REPLAY_ID_RE.match(replay_id or ""):
        return None
    path = REPLAY_DIR / f"{replay_id}.json"
    return path if path.is_file() else None


@lru_cache(maxsize=8)
def load_replay(replay_id: str) -> dict[str, Any]:
    path = _replay_path(replay_id)
    if path is None:
        raise ValueError(f"Unknown replay '{replay_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_replays() -> list[dict[str, Any]]:
    replays = []
    for path in sorted(REPLAY_DIR.glob("*.json")):
        try:
            replay = load_replay(path.stem)
        except (ValueError, json.JSONDecodeError):
            continue
        replays.append({
            "replay_id": replay.get("replay_id", path.stem),
            "ticker": replay.get("ticker"),
            "analysis_date": replay.get("analysis_date"),
            "time_horizon": replay.get("time_horizon"),
            "description": replay.get("description"),
        })
    return replays


def replay_final_reports(replay: dict[str, Any]) -> dict[str, Any]:
    """Merge every report fragment in the recording, plus the derived reasoning trace."""
    reports: dict[str, Any] = {}
    for event in replay.get("events", []):
        reports.update(event.get("payload", {}).get("reports") or {})
    reports["agent_reasoning_trace"] = build_agent_reasoning_trace(reports)
    return reports


async def stream_replay(replay: dict[str, Any], websocket: WebSocket) -> dict[str, Any]:
    """Send a recording over the WebSocket with its original pacing."""
    speed = replay_speed()
    await websocket.send_json({
        "event": "system",
        "content": (
            f"REPLAY MODE: playing back a recorded run of {replay.get('ticker')} "
            f"from {replay.get('analysis_date')}. No API keys are used; "
            "add an LLM key to analyze any ticker live."
        ),
    })
    for event in replay.get("events", []):
        await asyncio.sleep(max(event.get("delay_ms", 0), 0) / 1000 / speed)
        await websocket.send_json(event["payload"])

    final_reports = replay_final_reports(replay)
    await websocket.send_json({
        "event": "chunk",
        "updates": [],
        "reports": {"agent_reasoning_trace": final_reports["agent_reasoning_trace"]},
    })
    return final_reports
