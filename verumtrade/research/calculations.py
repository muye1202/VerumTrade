"""Small, inspectable calculations with identified operands.

Every operand is either a value read from an identified record (evidence ID,
market snapshot, price history) or a declared assumption with its text. A
calculation that is missing an operand is not performed; it is returned as
incomplete with the missing operand named, so no number is ever produced from
a placeholder.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Literal, TypedDict

CALCULATION_VERSION = "calc-v1"

OperandSource = Literal["evidence", "market_snapshot", "price_history", "calculation", "assumption"]


class Operand(TypedDict, total=False):
    name: str
    value: float | None
    unit: str
    source: OperandSource
    ref: str
    assumption: str


class Calculation(TypedDict, total=False):
    calc_id: str
    formula: str
    expression: str
    operands: list[Operand]
    result: float | None
    unit: str
    status: Literal["complete", "incomplete"]
    missing: list[str]
    notes: list[str]
    version: str


class CalculationError(ValueError):
    pass


def operand(name: str, value: Any, *, source: OperandSource, ref: str = "", unit: str = "", assumption: str = "") -> Operand:
    parsed = _to_float(value)
    out: Operand = {"name": name, "value": parsed, "unit": unit, "source": source, "ref": ref}
    if source == "assumption":
        if not assumption.strip():
            raise CalculationError(f"assumption operand '{name}' must state the assumption")
        out["assumption"] = assumption.strip()
    return out


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _incomplete(calc_id: str, formula: str, operands: list[Operand], unit: str, missing: list[str]) -> Calculation:
    return {
        "calc_id": calc_id,
        "formula": formula,
        "expression": "",
        "operands": operands,
        "result": None,
        "unit": unit,
        "status": "incomplete",
        "missing": missing,
        "notes": [],
        "version": CALCULATION_VERSION,
    }


def _run(
    calc_id: str,
    formula: str,
    operands: list[Operand],
    unit: str,
    fn: Callable[..., float],
    expression: str,
    *,
    notes: Iterable[str] = (),
    digits: int = 4,
) -> Calculation:
    missing = [op["name"] for op in operands if op.get("value") is None]
    if missing:
        return _incomplete(calc_id, formula, operands, unit, missing)
    values = [op["value"] for op in operands]
    try:
        result = fn(*values)
    except ZeroDivisionError:
        return {**_incomplete(calc_id, formula, operands, unit, []), "notes": ["division by zero"]}
    return {
        "calc_id": calc_id,
        "formula": formula,
        "expression": expression.format(*values),
        "operands": operands,
        "result": round(result, digits),
        "unit": unit,
        "status": "complete",
        "missing": [],
        "notes": list(notes),
        "version": CALCULATION_VERSION,
    }


# --- Named formulas -------------------------------------------------------


def pct_change(calc_id: str, old: Operand, new: Operand) -> Calculation:
    return _run(calc_id, "pct_change", [old, new], "fraction", lambda a, b: (b - a) / a, "({1} - {0}) / {0}")


def difference(calc_id: str, a: Operand, b: Operand, unit: str = "") -> Calculation:
    return _run(calc_id, "difference", [a, b], unit or a.get("unit", ""), lambda x, y: x - y, "{0} - {1}")


def ratio(calc_id: str, numerator: Operand, denominator: Operand, unit: str = "fraction") -> Calculation:
    return _run(calc_id, "ratio", [numerator, denominator], unit, lambda n, d: n / d, "{0} / {1}")


def price_at_return(calc_id: str, reference_price: Operand, ret: Operand) -> Calculation:
    return _run(
        calc_id, "price_at_return", [reference_price, ret], "price",
        lambda p, r: p * (1.0 + r), "{0} * (1 + {1})", digits=2,
    )


def reward_risk(calc_id: str, entry: Operand, target: Operand, stop: Operand) -> Calculation:
    def fn(e: float, t: float, s: float) -> float:
        risk = e - s
        if risk <= 0:
            raise ZeroDivisionError
        return (t - e) / risk

    return _run(calc_id, "reward_risk", [entry, target, stop], "ratio", fn, "({1} - {0}) / ({0} - {2})", digits=2)


def sum_values(calc_id: str, parts: list[Operand], unit: str = "") -> Calculation:
    expr = " + ".join("{%d}" % i for i in range(len(parts)))
    return _run(calc_id, "sum", parts, unit, lambda *v: sum(v), expr)


FORMULAS: dict[str, tuple[Callable[..., Calculation], tuple[str, ...]]] = {
    "pct_change": (pct_change, ("old", "new")),
    "difference": (difference, ("a", "b")),
    "ratio": (ratio, ("numerator", "denominator")),
    "price_at_return": (price_at_return, ("reference_price", "ret")),
}


# --- Price history context ------------------------------------------------


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("empty sample")
    pos = (len(sorted_values) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


QUANTILES = {"p05": 0.05, "p10": 0.10, "p25": 0.25, "p50": 0.50, "p75": 0.75, "p90": 0.90, "p95": 0.95}


def holding_return_distribution(
    closes: list[float],
    horizon_days: int,
    *,
    dates: list[str] | None = None,
    min_samples: int = 60,
) -> dict[str, Any]:
    """Empirical distribution of ``horizon_days``-trading-day returns over the supplied history.

    This is an unconditional base rate for the stock, not a forecast for the next event:
    overlapping windows are serially dependent, and the sample spans whatever regimes the
    window contains. The selection rule and sample size are reported with the quantiles.
    """
    clean = [float(c) for c in closes if _to_float(c) is not None and float(c) > 0]
    returns = [clean[i + horizon_days] / clean[i] - 1.0 for i in range(len(clean) - horizon_days)]
    out: dict[str, Any] = {
        "calc_id": f"price_dist_{horizon_days}d",
        "formula": "holding_return_distribution",
        "horizon_trading_days": horizon_days,
        "sample_size": len(returns),
        "selection_rule": f"all overlapping {horizon_days}-trading-day windows in the supplied close history",
        "window_start": (dates or [None])[0] if dates else None,
        "window_end": (dates or [None])[-1] if dates else None,
        "caveats": [
            "unconditional base rate, not conditioned on the upcoming event",
            "overlapping windows are serially dependent; effective sample size is smaller",
        ],
        "version": CALCULATION_VERSION,
    }
    if len(returns) < min_samples:
        out.update({"status": "incomplete", "missing": [f"at least {min_samples} return windows"], "quantiles": {}})
        return out
    ordered = sorted(returns)
    out["status"] = "complete"
    out["quantiles"] = {name: round(_quantile(ordered, q), 4) for name, q in QUANTILES.items()}
    daily = [clean[i + 1] / clean[i] - 1.0 for i in range(len(clean) - 1)]
    mean = sum(daily) / len(daily)
    out["daily_volatility"] = round(math.sqrt(sum((d - mean) ** 2 for d in daily) / (len(daily) - 1)), 5)
    return out


def parse_closes_csv(text: str) -> tuple[list[str], list[float]]:
    """Parse a vendor OHLCV CSV (Date + Close/Adj Close columns), skipping comment lines."""
    rows = [line for line in str(text or "").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not rows:
        return [], []
    header = [h.strip().lower() for h in rows[0].split(",")]
    close_col = next((header.index(c) for c in ("close", "adj close") if c in header), None)
    date_col = next((i for i, h in enumerate(header) if h in {"date", "datetime", "timestamp", ""}), 0)
    if close_col is None:
        return [], []
    dates, closes = [], []
    for line in rows[1:]:
        cells = line.split(",")
        if len(cells) <= close_col:
            continue
        value = _to_float(cells[close_col])
        if value is None:
            continue
        dates.append(re.sub(r"\s.*$", "", cells[date_col].strip()))
        closes.append(value)
    return dates, closes
