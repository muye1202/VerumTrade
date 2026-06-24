from __future__ import annotations

"""
Business inflection extraction for discovery.

This module is deterministic and LLM-free. It converts parsed fundamentals
packets into compact inflection signals that can feed attention-gap and future
thesis-aware scoring.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class BusinessInflectionSignal:
    ticker: str
    inflection_score: float = 0.0
    inflection_types: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    source_type: str = "fundamentals"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "inflection_score": round(float(self.inflection_score), 2),
            "inflection_types": list(self.inflection_types or []),
            "evidence": list(self.evidence or []),
            "metrics": dict(self.metrics or {}),
            "confidence": round(float(self.confidence), 4),
            "source_type": self.source_type,
        }


class BusinessInflectionExtractor:
    """Extract deterministic business inflection signals from fundamentals."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def extract_from_fundamentals(
        self,
        tickers: Iterable[str],
        *,
        fundamentals_by_ticker: Dict[str, Dict[str, Any]],
    ) -> List[BusinessInflectionSignal]:
        signals: List[BusinessInflectionSignal] = []
        for raw_ticker in tickers:
            ticker = str(raw_ticker or "").strip().upper()
            if not ticker:
                continue
            packet = fundamentals_by_ticker.get(ticker) or fundamentals_by_ticker.get(raw_ticker)
            signal = self._signal_from_packet(ticker, packet or {})
            if signal and signal.inflection_score > 0:
                signals.append(signal)
        signals.sort(key=lambda item: item.inflection_score, reverse=True)
        return signals

    def extract_for_scorecards(
        self,
        scorecards,
        *,
        trade_date: str,
    ) -> List[BusinessInflectionSignal]:
        """Best-effort runtime extraction.

        Disabled by default to avoid adding vendor calls to existing discovery
        runs. Enable with config["discovery"]["business_inflection"]["enabled"].
        """
        cfg = ((self.config.get("discovery") or {}).get("business_inflection") or {})
        if not bool(cfg.get("enabled", False)):
            return []

        max_tickers = int(cfg.get("max_tickers", 25))
        tickers = [
            str(getattr(sc, "ticker", "")).strip().upper()
            for sc in (scorecards or [])[:max(0, max_tickers)]
            if str(getattr(sc, "ticker", "")).strip()
        ]
        fundamentals_by_ticker: Dict[str, Dict[str, Any]] = {}
        for ticker in tickers:
            packet = self._fetch_fundamentals_packet(ticker, trade_date)
            if packet:
                fundamentals_by_ticker[ticker] = packet
        return self.extract_from_fundamentals(
            tickers,
            fundamentals_by_ticker=fundamentals_by_ticker,
        )

    @staticmethod
    def _signal_from_packet(
        ticker: str,
        packet: Dict[str, Any],
    ) -> Optional[BusinessInflectionSignal]:
        periods = list(packet.get("latest_periods") or [])
        if len(periods) < 2:
            return None
        latest = periods[0] or {}
        prior = periods[1] or {}

        evidence: List[str] = []
        types: List[str] = []
        metrics: Dict[str, float] = {}
        score = 0.0

        revenue_growth = _pct_change(latest.get("total_revenue"), prior.get("total_revenue"))
        if revenue_growth is not None:
            metrics["revenue_growth_qoq_pct"] = round(revenue_growth, 2)
            if revenue_growth >= 25.0:
                score += 30.0
                types.append("revenue_acceleration")
                evidence.append(f"Revenue grew {revenue_growth:.1f}% sequentially")
            elif revenue_growth >= 10.0:
                score += 18.0
                types.append("revenue_growth")
                evidence.append(f"Revenue grew {revenue_growth:.1f}% sequentially")

        gross_margin_delta = _point_delta(latest.get("gross_margin"), prior.get("gross_margin"))
        if gross_margin_delta is not None:
            metrics["gross_margin_delta_pct_points"] = round(gross_margin_delta * 100.0, 2)
            if gross_margin_delta >= 0.05:
                score += 22.0
                types.append("margin_expansion")
                evidence.append(f"Gross margin expanded {gross_margin_delta * 100.0:.1f} points")

        operating_margin_delta = _point_delta(
            latest.get("operating_margin"),
            prior.get("operating_margin"),
        )
        if operating_margin_delta is not None:
            metrics["operating_margin_delta_pct_points"] = round(
                operating_margin_delta * 100.0,
                2,
            )
            if operating_margin_delta >= 0.05:
                score += 24.0
                if "margin_expansion" not in types:
                    types.append("margin_expansion")
                evidence.append(
                    f"Operating margin expanded {operating_margin_delta * 100.0:.1f} points"
                )

        cashflow_margin_delta = _point_delta(
            latest.get("operating_cashflow_margin"),
            prior.get("operating_cashflow_margin"),
        )
        if cashflow_margin_delta is not None:
            metrics["operating_cashflow_margin_delta_pct_points"] = round(
                cashflow_margin_delta * 100.0,
                2,
            )
            if cashflow_margin_delta >= 0.05:
                score += 18.0
                types.append("cashflow_margin_expansion")
                evidence.append(
                    "Operating cash flow margin expanded "
                    f"{cashflow_margin_delta * 100.0:.1f} points"
                )

        net_cash_delta = _numeric_delta(latest.get("net_cash"), prior.get("net_cash"))
        if net_cash_delta is not None:
            metrics["net_cash_delta"] = round(net_cash_delta, 2)
            if prior.get("net_cash") is not None and float(prior.get("net_cash")) < 0 <= float(latest.get("net_cash", 0)):
                score += 12.0
                types.append("balance_sheet_inflection")
                evidence.append("Net cash improved from negative to positive")
            elif net_cash_delta > 0:
                score += min(8.0, max(0.0, net_cash_delta / 10.0))

        if not types:
            return None

        score = min(100.0, score)
        confidence = min(1.0, 0.55 + 0.1 * len(types))
        return BusinessInflectionSignal(
            ticker=ticker,
            inflection_score=round(score, 2),
            inflection_types=list(dict.fromkeys(types)),
            evidence=evidence[:6],
            metrics=metrics,
            confidence=round(confidence, 4),
            source_type=str(latest.get("source") or packet.get("source") or "fundamentals"),
        )

    @staticmethod
    def _fetch_fundamentals_packet(ticker: str, trade_date: str) -> Dict[str, Any]:
        try:
            from verumtrade.agents.utils.market_data.fundamentals_parser import (
                parse_fundamentals_sections,
            )
            from verumtrade.dataflows.interface import route_to_vendor

            raw = {
                "fundamentals": route_to_vendor("get_fundamentals", ticker, trade_date),
                "income_statement": route_to_vendor(
                    "get_income_statement",
                    ticker,
                    "quarterly",
                    trade_date,
                ),
                "balance_sheet": route_to_vendor(
                    "get_balance_sheet",
                    ticker,
                    "quarterly",
                    trade_date,
                ),
                "cashflow": route_to_vendor("get_cashflow", ticker, "quarterly", trade_date),
            }
            return parse_fundamentals_sections(raw, symbol=ticker, curr_date=trade_date)
        except Exception:
            return {}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: Any, previous: Any) -> Optional[float]:
    cur = _to_float(current)
    prev = _to_float(previous)
    if cur is None or prev in {None, 0.0}:
        return None
    return ((cur - prev) / abs(prev)) * 100.0


def _point_delta(current: Any, previous: Any) -> Optional[float]:
    cur = _to_float(current)
    prev = _to_float(previous)
    if cur is None or prev is None:
        return None
    return cur - prev


def _numeric_delta(current: Any, previous: Any) -> Optional[float]:
    cur = _to_float(current)
    prev = _to_float(previous)
    if cur is None or prev is None:
        return None
    return cur - prev
