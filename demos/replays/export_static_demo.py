"""Export replay recordings as static files for the backend-free web demo.

The hosted demo on GitHub Pages has no FastAPI server, so the frontend plays
recordings straight from JSON. This script writes, into ``--out``:

* ``index.json``: the same listing ``GET /api/replays`` returns.
* ``<replay_id>.json``: the full event stream ``/api/ws/analyze`` would send,
  including the opening REPLAY MODE notice and the derived reasoning trace.

Usage:
    python demos/replays/export_static_demo.py --out frontend/dist-demo/replays
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPLAY_DIR = REPO_ROOT / "demos" / "replays"

sys.path.insert(0, str(REPO_ROOT))

from verumtrade.graph.reasoning_trace import build_agent_reasoning_trace  # noqa: E402


def static_events(replay: dict) -> list[dict]:
    events = [{
        "delay_ms": 0,
        "payload": {
            "event": "system",
            "content": (
                f"LIVE DEMO: playing back a recorded run of {replay.get('ticker')} "
                f"from {replay.get('analysis_date')} in your browser. No server or API "
                "keys are involved; install VerumTrade locally to analyze any ticker."
            ),
        },
    }]
    events.extend(replay.get("events", []))

    reports: dict = {}
    for event in replay.get("events", []):
        reports.update(event.get("payload", {}).get("reports") or {})
    events.append({
        "delay_ms": 0,
        "payload": {
            "event": "chunk",
            "updates": [],
            "reports": {"agent_reasoning_trace": build_agent_reasoning_trace(reports)},
        },
    })
    events.append({
        "delay_ms": 0,
        "payload": {"event": "completed", "ticker": replay.get("ticker")},
    })
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    listing = []
    for path in sorted(REPLAY_DIR.glob("*.json")):
        replay = json.loads(path.read_text(encoding="utf-8"))
        replay_id = replay.get("replay_id", path.stem)
        listing.append({
            "replay_id": replay_id,
            "ticker": replay.get("ticker"),
            "analysis_date": replay.get("analysis_date"),
            "time_horizon": replay.get("time_horizon"),
            "description": replay.get("description"),
        })
        out = {key: value for key, value in replay.items() if key != "events"}
        out["events"] = static_events(replay)
        (args.out / f"{replay_id}.json").write_text(
            json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    (args.out / "index.json").write_text(
        json.dumps({"demo_mode": True, "static": True, "replays": listing}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Exported {len(listing)} replay(s) to {args.out}")


if __name__ == "__main__":
    main()
