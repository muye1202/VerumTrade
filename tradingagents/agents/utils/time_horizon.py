from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TimeHorizonSpec:
    key: str
    label: str
    weeks_range: tuple[int, int]
    trading_days_range: tuple[int, int]
    company_news_lookback_days: int
    global_news_lookback_days: int
    sentiment_lookback_days: int


DEFAULT_TIME_HORIZON_KEY = "1-2 weeks"


_SPECS: dict[str, TimeHorizonSpec] = {
    "1-2 weeks": TimeHorizonSpec(
        key="1-2 weeks",
        label="1–2 weeks",
        weeks_range=(1, 2),
        trading_days_range=(5, 10),
        company_news_lookback_days=14,
        global_news_lookback_days=5,
        sentiment_lookback_days=14,
    ),
    "2-4 weeks": TimeHorizonSpec(
        key="2-4 weeks",
        label="2–4 weeks",
        weeks_range=(2, 4),
        trading_days_range=(10, 20),
        company_news_lookback_days=21,
        global_news_lookback_days=7,
        sentiment_lookback_days=21,
    ),
    "1-2 months": TimeHorizonSpec(
        key="1-2 months",
        label="1–2 months",
        weeks_range=(4, 8),
        trading_days_range=(20, 42),
        company_news_lookback_days=30,
        global_news_lookback_days=10,
        sentiment_lookback_days=30,
    ),
    "2-3 months": TimeHorizonSpec(
        key="2-3 months",
        label="2–3 months",
        weeks_range=(8, 12),
        trading_days_range=(42, 63),
        company_news_lookback_days=45,
        global_news_lookback_days=14,
        sentiment_lookback_days=45,
    ),
}


def _normalize_time_horizon(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Normalize unicode dashes to ASCII hyphen.
    s = s.replace("–", "-").replace("—", "-")
    # Normalize internal whitespace.
    s = " ".join(s.split())
    return s.lower()


def get_time_horizon_spec(value: Optional[str]) -> TimeHorizonSpec:
    """
    Resolve a user-selected time horizon into a stable spec.

    Accepts either the stored key (ASCII, e.g. "1-2 weeks") or the
    human label (e.g. "1–2 weeks"). Unknown values fall back to the
    DEFAULT_TIME_HORIZON_KEY spec.
    """
    normalized = _normalize_time_horizon(value)
    if not normalized:
        return _SPECS[DEFAULT_TIME_HORIZON_KEY]

    # Build a lookup that accepts both keys and labels.
    for spec in _SPECS.values():
        if normalized == _normalize_time_horizon(spec.key):
            return spec
        if normalized == _normalize_time_horizon(spec.label):
            return spec

    return _SPECS[DEFAULT_TIME_HORIZON_KEY]
