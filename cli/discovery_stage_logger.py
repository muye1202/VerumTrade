from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)


@dataclass
class DiscoveryStageProgressLogger:
    """
    Live Stage 0/1 progress logger for discovery mode.

    Exposes a callback interface consumed by discovery scanners via config:
    config["discovery_progress_callback"] = logger.callback
    """

    console: Console
    progress: Optional[Progress] = None
    stage0_task: Optional[TaskID] = None
    stage1_task: Optional[TaskID] = None
    stage0_done: bool = False
    stage1_total: int = 0
    stage1_done: int = 0
    stage0_metrics: Optional[Dict[str, Any]] = None
    enabled: bool = True

    def start(self) -> None:
        # Test doubles may not implement Rich console APIs required by Progress.
        if not hasattr(self.console, "get_time"):
            self.enabled = False
            return
        self.enabled = True
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=28),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )
        self.progress.start()
        self.stage0_task = self.progress.add_task("Stage 0: Universe + catalyst prefilter", total=1)
        self.stage1_task = self.progress.add_task("Stage 1: Batch enrichment", total=1)
        # Keep Stage 1 pending until we know the ticker count.
        self.progress.update(self.stage1_task, completed=0, description="Stage 1: Waiting for Stage 0")

    def stop(self) -> None:
        if self.progress:
            self.progress.stop()
            self.progress = None

    def callback(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled or not self.progress:
            return
        data = payload or {}

        if event == "stage0.start":
            if self.stage0_task is not None:
                self.progress.update(
                    self.stage0_task,
                    completed=0,
                    total=1,
                    description="Stage 0: Universe + catalyst prefilter (running)",
                )
            return

        if event == "stage0.complete":
            if self.stage0_task is not None:
                base = int(data.get("base_universe", 0))
                filtered = int(data.get("filtered_universe", 0))
                mode = str(data.get("mode", "daily_calendar"))
                elapsed_s = None
                if isinstance(self.stage0_metrics, dict):
                    elapsed_s = (
                        float(self.stage0_metrics.get("assets_fetch_s", 0.0))
                        + float(self.stage0_metrics.get("earnings_filter_s", 0.0))
                        + float(self.stage0_metrics.get("adv_filter_s", 0.0))
                    )
                suffix = f", t~{elapsed_s:.1f}s" if elapsed_s is not None else ""
                self.progress.update(
                    self.stage0_task,
                    completed=1,
                    total=1,
                    description=f"Stage 0: complete ({filtered}/{base}, mode={mode}{suffix})",
                )
            self.stage0_done = True
            return

        if event == "stage0.metrics":
            self.stage0_metrics = dict(data)
            return

        if event == "stage1.start":
            self.stage1_total = max(0, int(data.get("total", 0)))
            self.stage1_done = 0
            if self.stage1_task is not None:
                total = max(1, self.stage1_total)
                self.progress.update(
                    self.stage1_task,
                    completed=0,
                    total=total,
                    description=f"Stage 1: Enriching {self.stage1_total} tickers",
                )
            return

        if event == "stage1.ticker_done":
            self.stage1_done += 1
            if self.stage1_task is not None:
                ticker = str(data.get("ticker", "")).upper()
                total = max(1, self.stage1_total)
                completed = min(self.stage1_done, total)
                self.progress.update(
                    self.stage1_task,
                    completed=completed,
                    total=total,
                    description=f"Stage 1: Enriched {completed}/{self.stage1_total} (last {ticker})",
                )
            return

        if event == "stage1.complete":
            if self.stage1_task is not None:
                count = int(data.get("count", self.stage1_done))
                total = max(1, self.stage1_total or count)
                self.progress.update(
                    self.stage1_task,
                    completed=min(total, max(count, self.stage1_done)),
                    total=total,
                    description=f"Stage 1: complete ({count} scorecards)",
                )
            return
