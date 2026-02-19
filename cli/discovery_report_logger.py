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


def load_tickers_from_discovery_report(
    results_root: str | Path,
    trade_date: str,
) -> tuple[list[str], Path]:
    """Load the saved ticker list from a previous discovery report.

    Parses ``results/discovery/<trade_date>/reports/stock_discovery_report.md``
    and returns ``(tickers, report_path)``.

    Parsing strategy (in order):
    1. Lines inside the ``## Discovered Tickers`` section that match the
       pattern ``- `TICKER` `` (backtick-quoted).
    2. Fallback: the ``## Top Pick Summary`` line, which contains a
       comma-separated list of tickers.

    Raises:
        FileNotFoundError: if the report file does not exist.
        ValueError: if no tickers can be parsed from the file.
    """
    import re

    dirs = ensure_discovery_dirs(results_root, trade_date)
    report_path = dirs["reports_dir"] / "stock_discovery_report.md"

    if not report_path.exists():
        raise FileNotFoundError(
            f"Discovery report not found: {report_path}\n"
            f"Run a fresh discovery for date '{trade_date}' first."
        )

    text = report_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- Strategy 1: parse the ## Discovered Tickers section ---
    tickers: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Discovered Tickers":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("##"):
                break  # next section started
            m = re.match(r"^-\s+`([A-Z0-9.\-]+)`\s*$", stripped)
            if m:
                tickers.append(m.group(1))

    if tickers:
        return tickers, report_path

    # --- Strategy 2: fallback — Top Pick Summary line ---
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Top Pick Summary"):
            # The summary may be on the same line or the next non-empty line
            rest = stripped[len("## Top Pick Summary"):].strip().lstrip(":- ").strip()
            if rest:
                tickers = [t.strip() for t in rest.split(",") if t.strip()]
                break
        # Also handle the case where the summary is on the very next line
        # after the heading (already handled by the loop continuing)

    # Second pass: look for a bare comma-separated line after the heading
    if not tickers:
        found_heading = False
        for line in lines:
            stripped = line.strip()
            if stripped == "## Top Pick Summary":
                found_heading = True
                continue
            if found_heading and stripped and not stripped.startswith("#"):
                tickers = [t.strip() for t in stripped.split(",") if t.strip()]
                break

    if not tickers:
        raise ValueError(
            f"Could not parse any tickers from discovery report: {report_path}"
        )

    return tickers, report_path


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
    metadata = getattr(result, "metadata", None) or {}
    stage0 = metadata.get("stage0", {}) if isinstance(metadata, dict) else {}
    stage1 = metadata.get("stage1", {}) if isinstance(metadata, dict) else {}

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

    if isinstance(stage0, dict) and stage0:
        lines.append("")
        lines.append("## Stage 0 Metrics")
        lines.append("")
        lines.append(f"- assets_fetch_s: `{float(stage0.get('assets_fetch_s', 0.0)):.2f}`")
        lines.append(f"- earnings_filter_s: `{float(stage0.get('earnings_filter_s', 0.0)):.2f}`")
        lines.append(f"- adv_filter_s: `{float(stage0.get('adv_filter_s', 0.0)):.2f}`")
        lines.append(f"- cache_hits: `{int(stage0.get('cache_hits', 0))}`")
        lines.append(f"- cache_misses: `{int(stage0.get('cache_misses', 0))}`")
        lines.append(f"- vendor_calls_estimate: `{int(stage0.get('vendor_calls_estimate', 0))}`")

    if isinstance(stage1, dict) and stage1:
        lines.append("")
        lines.append("## Stage 1 Metadata")
        lines.append("")
        lines.append(f"- Enriched tickers: `{stage1.get('count', 0)}`")
        lines.append(f"- Full coverage (%): `{stage1.get('coverage_pct', 0.0)}`")
        scorecards = stage1.get("scorecards", []) or []
        if scorecards:
            lines.append("")
            lines.append("| Ticker | Earnings Beat 4Q | Options Score | Short % Float | Insider | Flags |")
            lines.append("|---|---:|---:|---:|---|---|")
            for row in scorecards:
                ticker = row.get("ticker", "")
                beat = row.get("earnings_beat_rate_4q", 0.0)
                options = row.get("options_unusual_score", 0.0)
                short_pct = row.get("short_interest_pct_float", 0.0)
                insider = row.get("insider_signal", "neutral")
                flags = ",".join(row.get("data_quality_flags", []) or []) or "-"
                lines.append(
                    f"| {ticker} | {float(beat):.1f} | {float(options):.1f} | {float(short_pct):.1f} | {insider} | {flags} |"
                )

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
    lines.append(
        "- Note: For BUY decisions, `quantity` may be `null` when `position_size_pct` is provided; "
        "the executor computes shares at execution time."
    )
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
            final_state = item.get("final_state") or {}
            structured = final_state.get("final_trade_decision_structured") if isinstance(final_state, dict) else {}
            execution_result = item.get("execution_result")

            lines.append("#### Decision Sizing")
            lines.append("")
            lines.append(f"- Decision quantity: `{(structured or {}).get('quantity')}`")
            lines.append(f"- Decision position_size_pct: `{(structured or {}).get('position_size_pct')}`")
            resolved_qty = None
            if isinstance(execution_result, dict):
                resolved_qty = execution_result.get("qty")
            lines.append(f"- Resolved/executed quantity: `{resolved_qty}`")
            lines.append("")

            if isinstance(execution_result, dict):
                lines.append("#### Execution")
                lines.append("")
                lines.append(f"- Executed: `{bool(execution_result.get('executed'))}`")
                lines.append(f"- Signal: `{execution_result.get('signal')}`")
                if execution_result.get("message"):
                    lines.append(f"- Message: `{execution_result.get('message')}`")
                if execution_result.get("error"):
                    lines.append(f"- Error: `{execution_result.get('error')}`")
                if execution_result.get("qty") is not None:
                    lines.append(f"- Qty: `{execution_result.get('qty')}`")
                if execution_result.get("price") is not None:
                    lines.append(f"- Price: `{execution_result.get('price')}`")
                order = execution_result.get("order") or {}
                if isinstance(order, dict) and order:
                    if order.get("id"):
                        lines.append(f"- Order ID: `{order.get('id')}`")
                    if order.get("status"):
                        lines.append(f"- Order status: `{order.get('status')}`")
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
