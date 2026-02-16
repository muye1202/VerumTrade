from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tradingagents.agents.utils.market_data.options_flow_tools import (
    _analyze_chain_for_unusual,
    _get_options_chain,
)
from tradingagents.agents.utils.market_data.short_interest_tools import (
    _get_recent_short_volume,
    _get_yahoo_short_data,
)

from .models import Stage1EnrichmentScorecard
from .utils import extract_indicator_value, parse_price_volume_csv, safe_float


class Stage1BatchEnricher:
    """
    Stage 1: batch enrichment for Stage 0 catalyst-universe tickers.

    This stage is deterministic and must not call LLMs.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _emit_progress(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        cb = self.config.get("discovery_progress_callback")
        if callable(cb):
            try:
                cb(event, payload or {})
            except Exception:
                pass

    def _settings(self) -> Dict[str, Any]:
        defaults = {
            "enabled": True,
            "max_workers": 8,
            "price_lookback_days": 90,
            "requirements": {"fail_open": True},
            "options": {"max_expirations": 6, "vol_oi_threshold": 2.0},
        }
        override = self.config.get("stage1", {}) or {}
        req_override = override.get("requirements", {}) or {}
        opt_override = override.get("options", {}) or {}
        return {
            **defaults,
            **override,
            "requirements": {**defaults["requirements"], **req_override},
            "options": {**defaults["options"], **opt_override},
        }

    def enrich_universe(
        self,
        universe: List[str],
        trade_date: str,
        max_workers: Optional[int] = None,
    ) -> List[Stage1EnrichmentScorecard]:
        cfg = self._settings()
        if not bool(cfg.get("enabled", True)):
            return []

        symbols = sorted(
            {
                str(s).strip().upper()
                for s in (universe or [])
                if str(s).strip()
            }
        )
        if not symbols:
            return []

        workers = int(max_workers or cfg.get("max_workers", 8))
        spy_roc_20d = self._fetch_spy_roc_20d(trade_date)
        scorecards: List[Stage1EnrichmentScorecard] = []
        self._emit_progress(
            "stage1.start",
            {"trade_date": trade_date, "total": len(symbols), "workers": workers},
        )

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self._enrich_ticker, symbol, trade_date, spy_roc_20d): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    scorecard = future.result()
                except Exception as e:
                    self.logger.warning(f"Stage1 enrichment failed for {symbol}: {e}")
                    self._emit_progress(
                        "stage1.ticker_done",
                        {"ticker": symbol, "ok": False, "error": type(e).__name__},
                    )
                    continue
                if scorecard:
                    scorecards.append(scorecard)
                self._emit_progress("stage1.ticker_done", {"ticker": symbol, "ok": bool(scorecard)})

        scorecards.sort(key=lambda s: s.ticker)
        self._emit_progress(
            "stage1.complete",
            {
                "trade_date": trade_date,
                "count": len(scorecards),
                "total": len(symbols),
            },
        )
        return scorecards

    def _enrich_ticker(
        self,
        ticker: str,
        trade_date: str,
        spy_roc_20d: float,
    ) -> Optional[Stage1EnrichmentScorecard]:
        cfg = self._settings()
        fail_open = bool(cfg.get("requirements", {}).get("fail_open", True))
        flags: List[str] = []

        try:
            technical = self._fetch_price_and_technical_block(ticker, trade_date, spy_roc_20d)
        except Exception as e:
            if not fail_open:
                raise
            flags.append(f"price_block_error:{type(e).__name__}")
            technical = {}

        try:
            earnings = self._fetch_earnings_block(ticker)
        except Exception as e:
            if not fail_open:
                raise
            flags.append(f"earnings_block_error:{type(e).__name__}")
            earnings = {}

        try:
            options = self._fetch_options_block(ticker)
        except Exception as e:
            if not fail_open:
                raise
            flags.append(f"options_block_error:{type(e).__name__}")
            options = {}

        try:
            shorts = self._fetch_short_interest_block(ticker, trade_date)
        except Exception as e:
            if not fail_open:
                raise
            flags.append(f"short_interest_block_error:{type(e).__name__}")
            shorts = {}

        try:
            insider_signal = self._fetch_insider_signal(ticker, trade_date)
        except Exception as e:
            if not fail_open:
                raise
            flags.append(f"insider_block_error:{type(e).__name__}")
            insider_signal = "neutral"

        scorecard = Stage1EnrichmentScorecard(
            ticker=ticker,
            catalyst_window=f"{trade_date} to {(datetime.strptime(trade_date, '%Y-%m-%d') + timedelta(days=7)).strftime('%Y-%m-%d')}",
            price=float(technical.get("price", 0.0)),
            roc_20d=float(technical.get("roc_20d", 0.0)),
            rs_vs_spy_20d=float(technical.get("rs_vs_spy_20d", 0.0)),
            adx=float(technical.get("adx", 0.0)),
            volume_ratio=float(technical.get("volume_ratio", 0.0)),
            vs_sma50_pct=float(technical.get("vs_sma50_pct", 0.0)),
            vs_sma200_pct=float(technical.get("vs_sma200_pct", 0.0)),
            bollinger_pct_b=float(technical.get("bollinger_pct_b", 0.0)),
            obv_slope_10d=float(technical.get("obv_slope_10d", 0.0)),
            avg_dollar_volume_20d=float(technical.get("avg_dollar_volume_20d", 0.0)),
            vwap=float(technical.get("vwap", 0.0)),
            vwap_distance_pct=float(technical.get("vwap_distance_pct", 0.0)),
            earnings_beat_rate_4q=float(earnings.get("earnings_beat_rate_4q", 0.0)),
            eps_consensus_current_q=float(earnings.get("eps_consensus_current_q", 0.0)),
            options_unusual_score=float(options.get("options_unusual_score", 0.0)),
            options_call_put_notional_ratio=float(options.get("options_call_put_notional_ratio", 0.0)),
            short_interest_pct_float=float(shorts.get("short_interest_pct_float", 0.0)),
            days_to_cover=float(shorts.get("days_to_cover", 0.0)),
            finra_short_volume_ratio_latest=float(shorts.get("finra_short_volume_ratio_latest", 0.0)),
            insider_signal=insider_signal,
            data_quality_flags=flags,
        )
        return scorecard

    @staticmethod
    def _compute_obv_slope_10d(prices: List[float], volumes: List[float]) -> float:
        if len(prices) < 10 or len(volumes) < 10:
            return 0.0
        p = prices[-10:]
        v = volumes[-10:]
        obv = [0.0]
        for i in range(1, len(p)):
            if p[i] > p[i - 1]:
                obv.append(obv[-1] + v[i])
            elif p[i] < p[i - 1]:
                obv.append(obv[-1] - v[i])
            else:
                obv.append(obv[-1])

        n = float(len(obv))
        x_mean = (n - 1.0) / 2.0
        y_mean = sum(obv) / n
        numerator = 0.0
        denominator = 0.0
        for i, y in enumerate(obv):
            dx = i - x_mean
            numerator += dx * (y - y_mean)
            denominator += dx * dx
        if denominator == 0:
            return 0.0
        return numerator / denominator

    @staticmethod
    def _parse_vwap_series(raw_csv: str) -> List[float]:
        lines = [l for l in str(raw_csv).split("\n") if l.strip() and not l.startswith("#")]
        if len(lines) < 2:
            return []
        header = [h.strip() for h in lines[0].split(",")]
        vwap_idx = None
        for i, col in enumerate(header):
            if col.lower() == "vwap":
                vwap_idx = i
                break
        if vwap_idx is None:
            return []

        out: List[float] = []
        for line in lines[1:]:
            parts = [p.strip() for p in line.split(",")]
            if vwap_idx >= len(parts):
                continue
            val = safe_float(parts[vwap_idx])
            if val is not None:
                out.append(float(val))
        return out

    @staticmethod
    def _extract_indicator_value(raw_text: str) -> Optional[float]:
        return extract_indicator_value(raw_text)

    def _fetch_spy_roc_20d(self, trade_date: str) -> float:
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")
        raw_csv = route_to_vendor("get_stock_data", "SPY", start, trade_date)
        prices, _ = parse_price_volume_csv(raw_csv)
        if len(prices) < 20:
            return 0.0
        return ((prices[-1] - prices[-20]) / prices[-20]) * 100.0

    def _fetch_price_and_technical_block(
        self,
        ticker: str,
        trade_date: str,
        spy_roc_20d: float,
    ) -> Dict[str, float]:
        from tradingagents.dataflows.interface import route_to_vendor

        lookback_days = int(self._settings().get("price_lookback_days", 90))
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=max(60, lookback_days))).strftime("%Y-%m-%d")

        raw_csv = route_to_vendor("get_stock_data", ticker, start_date, trade_date)
        prices, volumes = parse_price_volume_csv(raw_csv)
        vwap_series = self._parse_vwap_series(raw_csv)
        if len(prices) < 30 or len(volumes) < 20:
            raise RuntimeError("insufficient_price_history")

        price = float(prices[-1])
        roc_20d = ((price - prices[-20]) / prices[-20]) * 100.0
        avg_vol_20d = sum(volumes[-20:]) / 20.0
        avg_vol_5d = sum(volumes[-5:]) / 5.0
        volume_ratio = (avg_vol_5d / avg_vol_20d) if avg_vol_20d > 0 else 0.0
        avg_dollar_volume_20d = sum((prices[-20 + i] * volumes[-20 + i]) for i in range(20)) / 20.0

        sma50 = self._extract_indicator_value(
            route_to_vendor("get_indicators", ticker, "close_50_sma", trade_date, 3)
        )
        sma200 = self._extract_indicator_value(
            route_to_vendor("get_indicators", ticker, "close_200_sma", trade_date, 3)
        )
        adx = self._extract_indicator_value(
            route_to_vendor("get_indicators", ticker, "adx", trade_date, 3)
        )
        boll_ub = self._extract_indicator_value(
            route_to_vendor("get_indicators", ticker, "boll_ub", trade_date, 3)
        )
        boll_lb = self._extract_indicator_value(
            route_to_vendor("get_indicators", ticker, "boll_lb", trade_date, 3)
        )

        vs_sma50 = ((price - sma50) / sma50) * 100.0 if sma50 and sma50 > 0 else 0.0
        vs_sma200 = ((price - sma200) / sma200) * 100.0 if sma200 and sma200 > 0 else 0.0
        band = (boll_ub - boll_lb) if (boll_ub is not None and boll_lb is not None) else None
        pct_b = ((price - boll_lb) / band) if band and band != 0 else 0.0
        obv_slope_10d = self._compute_obv_slope_10d(prices, volumes)
        rs_vs_spy_20d = roc_20d - spy_roc_20d

        latest_vwap = float(vwap_series[-1]) if vwap_series else 0.0
        vwap_distance = ((price - latest_vwap) / latest_vwap) * 100.0 if latest_vwap > 0 else 0.0

        return {
            "price": round(price, 2),
            "roc_20d": round(roc_20d, 2),
            "rs_vs_spy_20d": round(rs_vs_spy_20d, 2),
            "adx": round(float(adx or 0.0), 2),
            "volume_ratio": round(volume_ratio, 4),
            "vs_sma50_pct": round(vs_sma50, 2),
            "vs_sma200_pct": round(vs_sma200, 2),
            "bollinger_pct_b": round(pct_b, 4),
            "obv_slope_10d": round(obv_slope_10d, 4),
            "avg_dollar_volume_20d": round(avg_dollar_volume_20d, 2),
            "vwap": round(latest_vwap, 4),
            "vwap_distance_pct": round(vwap_distance, 2),
        }

    @staticmethod
    def _find_numeric_from_mapping(mapping: Dict[str, Any], preferred_keys: List[str]) -> float:
        for key in preferred_keys:
            for col_name, val in mapping.items():
                if str(col_name).strip().lower() == key:
                    num = safe_float(val)
                    if num is not None:
                        return float(num)
        for val in mapping.values():
            num = safe_float(val)
            if num is not None:
                return float(num)
        return 0.0

    def _fetch_earnings_block(self, ticker: str) -> Dict[str, float]:
        import yfinance as yf  # type: ignore

        ticker_obj = yf.Ticker(ticker)

        beat_rate = 0.0
        earnings_history = getattr(ticker_obj, "earnings_history", None)
        if hasattr(earnings_history, "empty") and not earnings_history.empty:
            cols = {str(c).strip().lower(): c for c in earnings_history.columns}
            estimate_col = cols.get("epsestimate")
            actual_col = cols.get("epsactual")
            if estimate_col is not None and actual_col is not None:
                recent = earnings_history.tail(4)
                beats = 0
                total = 0
                for _, row in recent.iterrows():
                    est = safe_float(row.get(estimate_col))
                    act = safe_float(row.get(actual_col))
                    if est is None or act is None:
                        continue
                    total += 1
                    if act > est:
                        beats += 1
                if total > 0:
                    beat_rate = (beats / total) * 100.0

        eps_consensus = 0.0
        earnings_estimate = getattr(ticker_obj, "earnings_estimate", None)
        if hasattr(earnings_estimate, "empty") and not earnings_estimate.empty:
            candidate_rows = []
            try:
                for idx, row in earnings_estimate.iterrows():
                    if "0q" in str(idx).lower() or "current" in str(idx).lower():
                        candidate_rows.append(row.to_dict())
            except Exception:
                candidate_rows = []
            if not candidate_rows:
                try:
                    candidate_rows = [earnings_estimate.iloc[0].to_dict()]
                except Exception:
                    candidate_rows = []
            for row_map in candidate_rows:
                eps_consensus = self._find_numeric_from_mapping(
                    row_map,
                    ["avg", "avgestimate", "estimate", "epsestimate"],
                )
                if eps_consensus != 0.0:
                    break

        return {
            "earnings_beat_rate_4q": round(beat_rate, 2),
            "eps_consensus_current_q": round(eps_consensus, 4),
        }

    def _fetch_options_block(self, ticker: str) -> Dict[str, float]:
        opts = self._settings().get("options", {}) or {}
        max_expirations = int(opts.get("max_expirations", 6))
        vol_oi_threshold = float(opts.get("vol_oi_threshold", 2.0))

        chain_data = _get_options_chain(ticker)
        if not chain_data:
            return {
                "options_unusual_score": 0.0,
                "options_call_put_notional_ratio": 0.0,
            }

        ticker_obj = chain_data["ticker"]
        expirations = list(chain_data.get("expirations", []))[:max_expirations]
        info = chain_data.get("info", {}) or {}
        current_price = safe_float(info.get("regularMarketPrice", info.get("previousClose", 0))) or 0.0

        unusual_contracts = []
        for expiry in expirations:
            unusual_contracts.extend(
                _analyze_chain_for_unusual(
                    ticker=ticker_obj,
                    expiry=expiry,
                    current_price=float(current_price),
                    vol_oi_threshold=vol_oi_threshold,
                )
            )

        if not unusual_contracts:
            return {
                "options_unusual_score": 0.0,
                "options_call_put_notional_ratio": 0.0,
            }

        call_notional = sum(c.notional_value for c in unusual_contracts if c.contract_type == "call")
        put_notional = sum(c.notional_value for c in unusual_contracts if c.contract_type == "put")
        total_notional = call_notional + put_notional
        avg_ratio = sum(float(c.vol_oi_ratio) for c in unusual_contracts) / float(len(unusual_contracts))

        call_put_ratio = (call_notional / put_notional) if put_notional > 0 else (10.0 if call_notional > 0 else 0.0)
        unusual_score = min(
            100.0,
            (min(avg_ratio, 10.0) / 10.0) * 45.0
            + min(total_notional / 1_000_000.0, 4.0) * 12.5
            + min(len(unusual_contracts), 8) * 2.5,
        )

        return {
            "options_unusual_score": round(unusual_score, 2),
            "options_call_put_notional_ratio": round(call_put_ratio, 4),
        }

    def _fetch_short_interest_block(self, ticker: str, trade_date: str) -> Dict[str, float]:
        yahoo_data = _get_yahoo_short_data(ticker) or {}
        short_pct = safe_float(yahoo_data.get("short_pct_float")) or 0.0
        days_cover = safe_float(yahoo_data.get("short_ratio")) or 0.0

        dt = datetime.strptime(trade_date, "%Y-%m-%d")
        finra_rows = _get_recent_short_volume(ticker, dt)
        latest_ratio = 0.0
        if finra_rows:
            row = finra_rows[0]
            short_volume = safe_float(row.get("short_volume")) or 0.0
            total_volume = safe_float(row.get("total_volume")) or 0.0
            if total_volume > 0:
                latest_ratio = (short_volume / total_volume) * 100.0

        return {
            "short_interest_pct_float": round(short_pct, 2),
            "days_to_cover": round(days_cover, 2),
            "finra_short_volume_ratio_latest": round(latest_ratio, 2),
        }

    @staticmethod
    def _score_insider_text(raw: str) -> str:
        text = str(raw or "")
        if not text.strip():
            return "neutral"

        buy_hits = len(re.findall(r"\b(acquisition|purchase|code[:\s]+A)\b", text, flags=re.IGNORECASE))
        sell_hits = len(re.findall(r"\b(disposition|sale|code[:\s]+D|transaction code[:\s]+S)\b", text, flags=re.IGNORECASE))

        if buy_hits > sell_hits:
            return "bullish"
        if sell_hits > buy_hits:
            return "bearish"
        return "neutral"

    def _fetch_insider_signal(self, ticker: str, trade_date: str) -> str:
        from tradingagents.dataflows.interface import route_to_vendor

        raw: Any
        try:
            raw = route_to_vendor("get_insider_transactions", ticker)
        except Exception:
            raw = route_to_vendor("get_insider_transactions", ticker, trade_date)

        if isinstance(raw, dict):
            try:
                raw_text = json.dumps(raw, ensure_ascii=False)
            except Exception:
                raw_text = str(raw)
        else:
            raw_text = str(raw)

        return self._score_insider_text(raw_text)
