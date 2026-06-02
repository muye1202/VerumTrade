from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


_ET_TZ_NAME = "America/New_York"


@dataclass(frozen=True)
class UsMarketSession:
    session_label: str
    is_regular_open: bool
    execution_window_note: str
    now_et_iso: str


def get_us_eastern_tzinfo():
    """
    Return a tzinfo for US/Eastern.

    Prefers `zoneinfo` (requires tzdata on Windows), falls back to `pytz` if installed.
    Returns None if neither backend is available.
    """
    if ZoneInfo is not None:
        try:
            return ZoneInfo(_ET_TZ_NAME)
        except Exception:
            pass
    try:
        import pytz  # type: ignore

        return pytz.timezone(_ET_TZ_NAME)
    except Exception:
        return None


def _now_et(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    tz = get_us_eastern_tzinfo()
    if tz is None:
        return now
    try:
        return now.astimezone(tz)
    except Exception:
        return now


def describe_us_market_session(now: Optional[datetime] = None) -> dict[str, Any]:
    """
    Describe the current US stock market session using US/Eastern baseline windows.

    Baseline windows (ET, weekday-aware; does not detect US market holidays):
    - Pre-market: 04:00â€“09:30
    - Regular-hours: 09:30â€“16:00
    - After-market: 16:00â€“20:00
    - Overnight: 20:00â€“04:00
    - Weekend: Sat/Sun
    """
    et_tz = get_us_eastern_tzinfo()
    dt = _now_et(now)
    t = dt.timetz().replace(tzinfo=None)
    weekday = dt.weekday()  # 0=Mon ... 6=Sun

    if weekday >= 5:
        session_label = "WEEKEND"
        is_regular_open = False
        window = "Sat/Sun"
        note = "Weekend: no regular trading; orders wonâ€™t fill until the next eligible session."
    else:
        pre_start = time(4, 0)
        reg_start = time(9, 30)
        reg_end = time(16, 0)
        aft_end = time(20, 0)

        if pre_start <= t < reg_start:
            session_label = "PRE-MARKET"
            is_regular_open = False
            window = "04:00â€“09:30 ET"
            note = "Pre-market session: regular market is closed."
        elif reg_start <= t < reg_end:
            session_label = "REGULAR-HOURS"
            is_regular_open = True
            window = "09:30â€“16:00 ET"
            note = "Regular session: market orders can execute immediately; conditional orders may fill later."
        elif reg_end <= t < aft_end:
            session_label = "AFTER-MARKET"
            is_regular_open = False
            window = "16:00â€“20:00 ET"
            note = "After-hours session: regular market is closed."
        else:
            session_label = "OVERNIGHT"
            is_regular_open = False
            window = "20:00â€“04:00 ET"
            note = "Overnight: no extended-hours liquidity until the next pre-market session."

    now_iso = dt.isoformat()
    desc = UsMarketSession(
        session_label=session_label,
        is_regular_open=bool(is_regular_open),
        execution_window_note=note,
        now_et_iso=now_iso,
    )
    out = asdict(desc)
    out["session_window"] = window
    if et_tz is None:
        out["timezone"] = str(getattr(dt, "tzinfo", None) or "local")
        out["timezone_note"] = (
            "US/Eastern tzinfo unavailable; session timing computed using local timezone. "
            "Install `tzdata` (Windows) or `pytz` to ensure America/New_York handling."
        )
    else:
        out["timezone"] = _ET_TZ_NAME
        out["timezone_note"] = None
    out["holiday_note"] = "Baseline time-window logic only; does not detect US market holidays."
    return out


now_et = _now_et  # public alias â€” import as: from opentrace.utils.market_session import now_et


def format_market_session_context(desc: dict[str, Any]) -> str:
    """
    Format a concise context block describing the current execution session.

    Intended to be injected into agent prompts.
    """
    label = str(desc.get("session_label") or "UNKNOWN").upper()
    window = str(desc.get("session_window") or "")
    is_open = bool(desc.get("is_regular_open"))
    tz = str(desc.get("timezone") or _ET_TZ_NAME)
    tz_note = str(desc.get("timezone_note") or "").strip()
    now_iso = str(desc.get("now_et_iso") or "")
    holiday_note = str(desc.get("holiday_note") or "")

    if is_open:
        exec_line = "Trade execution will occur during REGULAR-HOURS."
        constraint_line = "MARKET is allowed; LIMIT/STOP orders are also allowed."
    else:
        exec_line = "Regular market is CLOSED. If executing now, the system will submit EXTENDED-HOURS orders."
        constraint_line = "EXTENDED-HOURS constraint: use LIMIT + TIME_IN_FORCE=DAY (MARKET/GTC will be rejected)."

    return (
        "CURRENT MARKET SESSION CONTEXT (US stocks):\n"
        f"- Now (ET): {now_iso} ({tz})\n"
        f"- Session: {label} ({window})\n"
        f"- {exec_line}\n"
        f"- {constraint_line}\n"
        f"- Note: {holiday_note}{(' ' + tz_note) if tz_note else ''}\n"
    )
