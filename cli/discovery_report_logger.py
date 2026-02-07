"""
Markdown report logging for discovery mode and discovery deep analysis mode.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def ensure_discovery_dirs(results_root: str | Path, trade_date: str) -> dict[str, Path]:
    """Ensure discovery result/report directories exist for a trade date."""
    results_dir = Path(results_root) / "discovery" / trade_date
    reports_dir = results_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return {"results_dir": results_dir, "reports_dir": reports_dir}


def write_discovery_report(
    *,
    results_root: str | Path,
    trade_date: str,
    result: Any,
    llm_provider: str,
    deep_think_model: str,
) -> Path:
    """Write stock discovery markdown report and return saved path."""
    dirs = ensure_discovery_dirs(results_root, trade_date)
    report_path = dirs["reports_dir"] / "stock_discovery_report.md"

    status = "SUCCESS" if bool(getattr(result, "success", False)) else "FAILED"
    tickers = list(getattr(result, "tickers", []) or [])
    iterations = getattr(result, "iterations", 0)
    error = getattr(result, "error", None)
    recommendation_report = getattr(result, "report", "") or ""

    lines: list[str] = []
    lines.append("# Stock Discovery Report")
    lines.append("")
    lines.append(f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Trade date: `{trade_date}`")
    lines.append(f"- LLM provider: `{llm_provider}`")
    lines.append(f"- Deep think model: `{deep_think_model}`")
    lines.append(f"- Status: `{status}`")
    lines.append(f"- Iterations: `{iterations}`")
    if error:
        lines.append(f"- Error: `{error}`")
    lines.append("")
    lines.append("## Discovered Tickers")
    lines.append("")
    if tickers:
        for ticker in tickers:
            lines.append(f"- `{ticker}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Recommendation Report")
    lines.append("")
    if recommendation_report:
        lines.append(recommendation_report)
    else:
        lines.append("No recommendation report content generated.")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_deep_analysis_report(
    *,
    results_root: str | Path,
    trade_date: str,
    selected_tickers: list[str],
    analysis_results: list[dict],
    time_horizon: str | None,
) -> Path:
    """Write deep analysis markdown report and return saved path."""
    dirs = ensure_discovery_dirs(results_root, trade_date)
    report_path = dirs["reports_dir"] / "deep_analysis_report.md"

    results = list(analysis_results or [])

    decision_counts = {"BUY": 0, "SELL": 0, "HOLD": 0, "UNKNOWN": 0}
    for item in results:
        decision = str(item.get("decision", "UNKNOWN")).upper()
        if decision not in decision_counts:
            decision = "UNKNOWN"
        decision_counts[decision] += 1

    lines: list[str] = []
    lines.append("# Discovery Deep Analysis Report")
    lines.append("")
    lines.append(f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Trade date: `{trade_date}`")
    lines.append(f"- Time horizon: `{time_horizon or 'N/A'}`")
    lines.append(f"- Selected tickers: `{', '.join(selected_tickers) if selected_tickers else 'None'}`")
    lines.append(f"- Analyzed count: `{len(results)}`")
    lines.append(f"- BUY: `{decision_counts['BUY']}`")
    lines.append(f"- HOLD: `{decision_counts['HOLD']}`")
    lines.append(f"- SELL: `{decision_counts['SELL']}`")
    lines.append(f"- UNKNOWN: `{decision_counts['UNKNOWN']}`")
    lines.append("")

    if not results:
        lines.append("## Results")
        lines.append("")
        lines.append("No analysis results generated.")
    else:
        lines.append("## Per-Ticker Analysis")
        lines.append("")
        for item in results:
            ticker = item.get("ticker") or "UNKNOWN"
            decision = str(item.get("decision", "UNKNOWN")).upper()
            conviction_raw = item.get("conviction_score", 0)
            try:
                conviction_score = float(conviction_raw)
            except Exception:
                conviction_score = 0.0

            final_decision = item.get("final_decision") or ""
            if not final_decision:
                final_state = item.get("final_state") or {}
                if isinstance(final_state, dict):
                    final_decision = final_state.get("final_trade_decision", "") or ""

            lines.append(f"### {ticker}")
            lines.append("")
            lines.append(f"- Decision: `{decision}`")
            lines.append(f"- Conviction score: `{conviction_score:.1f}`")
            lines.append("")
            lines.append("#### Final Decision")
            lines.append("")
            if final_decision:
                lines.append(final_decision)
            else:
                lines.append("No final decision text available.")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
