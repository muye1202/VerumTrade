from __future__ import annotations
"""
Track A - Enrichment:
Deep enrichment track focusing on multi-dimensional analysis including fundamental, technical, analyst and sentiment data.
"""

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.agents.utils.market_data.options_flow_tools import (
    _analyze_chain_for_unusual,
    _get_options_chain,
)
from tradingagents.agents.utils.market_data.short_interest_tools import (
    _get_recent_short_volume,
    _get_yahoo_short_data,
)

from .pipeline_models import Stage1EnrichmentScorecard
from .pipeline_utils import (
    compute_obv_slope,
    compute_return_pct,
    extract_indicator_value,
    linear_regression_slope,
    parse_ohlc_rows,
    parse_price_volume_csv,
    safe_float,
)


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
        ohlcv_cache: Optional[Dict[str, str]] = None,
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
        spy_roc_20d, spy_prices = self._fetch_spy_context(
            trade_date=trade_date,
            ohlcv_cache=ohlcv_cache,
        )
        scorecards: List[Stage1EnrichmentScorecard] = []
        self._emit_progress(
            "stage1.start",
            {"trade_date": trade_date, "total": len(symbols), "workers": workers},
        )

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(
                    self._enrich_ticker,
                    symbol,
                    trade_date,
                    spy_roc_20d,
                    spy_prices,
                    ohlcv_cache,
                ): symbol
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
        spy_prices: Optional[List[float]] = None,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> Optional[Stage1EnrichmentScorecard]:
        cfg = self._settings()
        fail_open = bool(cfg.get("requirements", {}).get("fail_open", True))
        flags: List[str] = []

        try:
            technical = self._fetch_price_and_technical_block(
                ticker,
                trade_date,
                spy_roc_20d,
                spy_prices=spy_prices,
                ohlcv_cache=ohlcv_cache,
            )
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
            trend_quality_score=float(technical.get("trend_quality_score", 0.0)),
            rv5_pct=float(technical.get("rv5_pct", 0.0)),
            rv20_pct=float(technical.get("rv20_pct", 0.0)),
            whipsaw_count_20=int(technical.get("whipsaw_count_20", 0)),
            breakout_efficiency=float(technical.get("breakout_efficiency", 0.0)),
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
        return compute_obv_slope(prices, volumes, window=10)

    @staticmethod
    def _linear_regression_slope(values: List[float]) -> float:
        return linear_regression_slope(values)

    @staticmethod
    def _ema(values: List[float], period: int) -> List[float]:
        if not values or period <= 0:
            return []
        alpha = 2.0 / (float(period) + 1.0)
        out = [float(values[0])]
        for v in values[1:]:
            out.append(alpha * float(v) + (1.0 - alpha) * out[-1])
        return out

    @staticmethod
    def _realized_vol_pct(prices: List[float], window: int) -> Optional[float]:
        if window <= 1 or len(prices) < window + 1:
            return None
        rets: List[float] = []
        for i in range(len(prices) - window, len(prices)):
            prev = float(prices[i - 1])
            cur = float(prices[i])
            if prev <= 0 or cur <= 0:
                continue
            rets.append(math.log(cur / prev))
        if len(rets) < max(2, window - 1):
            return None
        mean = sum(rets) / float(len(rets))
        var = sum((x - mean) ** 2 for x in rets) / float(max(1, len(rets) - 1))
        return (var ** 0.5) * (252.0 ** 0.5) * 100.0

    @staticmethod
    def _parse_ohlc_rows(raw_csv: str) -> List[Dict[str, float]]:
        return parse_ohlc_rows(raw_csv)

    @staticmethod
    def _atr_pct(ohlc_rows: List[Dict[str, float]], window: int = 20) -> Optional[float]:
        if len(ohlc_rows) < window + 1:
            return None
        tr: List[float] = []
        for i in range(len(ohlc_rows) - window, len(ohlc_rows)):
            row = ohlc_rows[i]
            prev_close = ohlc_rows[i - 1]["close"] if i > 0 else row["close"]
            high = float(row["high"])
            low = float(row["low"])
            tr.append(max(abs(high - low), abs(high - prev_close), abs(low - prev_close)))
        if not tr:
            return None
        atr = sum(tr) / float(len(tr))
        close = float(ohlc_rows[-1]["close"])
        if close <= 0:
            return None
        return (atr / close) * 100.0

    @staticmethod
    def _extract_indicator_series(raw_text: str, max_points: int = 30) -> List[float]:
        values: List[float] = []
        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            candidate: Optional[float] = None
            if ":" in line:
                candidate = safe_float(line.split(":")[-1].strip())
            if candidate is None and "," in line:
                parts = [p.strip() for p in line.split(",") if p.strip()]
                for part in reversed(parts):
                    candidate = safe_float(part)
                    if candidate is not None:
                        break
            if candidate is None:
                candidate = safe_float(line)
            if candidate is not None:
                values.append(float(candidate))
        if max_points > 0:
            return values[-max_points:]
        return values

    @staticmethod
    def _whipsaw_count_around_ema(
        prices: List[float],
        ema20: List[float],
        ema50: List[float],
        lookback: int = 20,
    ) -> int:
        if not prices or not ema20 or not ema50:
            return 0
        n = min(len(prices), len(ema20), len(ema50), max(2, int(lookback)))
        p = prices[-n:]
        e20 = ema20[-n:]
        e50 = ema50[-n:]
        signs20 = [1 if px >= ma else -1 for px, ma in zip(p, e20)]
        signs50 = [1 if px >= ma else -1 for px, ma in zip(p, e50)]
        flips = 0
        for i in range(1, n):
            if signs20[i] != signs20[i - 1]:
                flips += 1
            if signs50[i] != signs50[i - 1]:
                flips += 1
        return flips

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
        roc, _ = self._fetch_spy_context(trade_date=trade_date, ohlcv_cache=None)
        return float(roc)

    def _fetch_spy_context(
        self,
        trade_date: str,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> Tuple[float, List[float]]:
        from tradingagents.dataflows.interface import route_to_vendor

        cache = ohlcv_cache if ohlcv_cache is not None else {}
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=120)).strftime("%Y-%m-%d")
        raw_csv = cache.get("SPY")
        if raw_csv is None:
            raw_csv = route_to_vendor("get_stock_data", "SPY", start, trade_date)
            cache["SPY"] = str(raw_csv or "")
        prices, _ = parse_price_volume_csv(str(raw_csv or ""))
        ret = compute_return_pct(prices, 20)
        if ret is None:
            return 0.0, prices
        return float(ret), prices

    def _fetch_price_and_technical_block(
        self,
        ticker: str,
        trade_date: str,
        spy_roc_20d: float,
        spy_prices: Optional[List[float]] = None,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        from tradingagents.dataflows.interface import route_to_vendor

        lookback_days = int(self._settings().get("price_lookback_days", 90))
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=max(60, lookback_days))).strftime("%Y-%m-%d")

        cache = ohlcv_cache if ohlcv_cache is not None else {}
        raw_csv = cache.get(ticker)
        if raw_csv is None:
            raw_csv = route_to_vendor("get_stock_data", ticker, start_date, trade_date)
            cache[ticker] = str(raw_csv or "")
        prices, volumes = parse_price_volume_csv(raw_csv)
        ohlc_rows = self._parse_ohlc_rows(raw_csv)
        vwap_series = self._parse_vwap_series(raw_csv)
        if len(prices) < 30 or len(volumes) < 20:
            raise RuntimeError("insufficient_price_history")

        price = float(prices[-1])
        roc_20d_raw = compute_return_pct(prices, 20)
        if roc_20d_raw is None:
            raise RuntimeError("insufficient_price_history")
        roc_20d = float(roc_20d_raw)
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

        # --- Noise-aware trend quality stack ---
        ema20 = self._ema(prices, 20)
        ema50 = self._ema(prices, 50)
        atr_pct = self._atr_pct(ohlc_rows, 20) or 0.0
        log_ema20 = [math.log(max(1e-9, x)) for x in ema20[-20:]] if len(ema20) >= 20 else []
        ema_log_slope = self._linear_regression_slope(log_ema20)
        slope_norm = 0.0
        if atr_pct > 0:
            slope_norm = max(0.0, min(100.0, ((ema_log_slope * 100.0) / atr_pct + 1.0) * 40.0))

        trend_persistence = 0.0
        if len(prices) >= 15 and len(ema20) >= 15:
            closes_15 = prices[-15:]
            ema20_15 = ema20[-15:]
            trend_persistence = sum(1 for px, ma in zip(closes_15, ema20_15) if px > ma) / 15.0

        adx_series = self._extract_indicator_series(
            route_to_vendor("get_indicators", ticker, "adx", trade_date, 25),
            max_points=20,
        )
        adx_persistence = 0.0
        if len(adx_series) >= 10:
            window = adx_series[-15:] if len(adx_series) >= 15 else adx_series
            adx_persistence = sum(1 for v in window if v > 22.0) / float(len(window))
        else:
            adx_persistence = 1.0 if float(adx or 0.0) > 22.0 else 0.0

        rs_slope_norm = 50.0
        if spy_prices and len(spy_prices) >= 40 and len(prices) >= 40:
            span = min(len(spy_prices), len(prices), 40)
            rs_line = []
            for stock_px, spy_px in zip(prices[-span:], spy_prices[-span:]):
                if spy_px > 0:
                    rs_line.append(stock_px / spy_px)
            if len(rs_line) >= 10:
                rs_slope = self._linear_regression_slope([math.log(max(1e-9, x)) for x in rs_line])
                rs_slope_norm = max(0.0, min(100.0, ((rs_slope * 10000.0) + 50.0)))

        breakout_eff = 0.0
        breakout_eff_norm = 0.0
        if len(prices) >= 56 and atr_pct > 0:
            prior_high_55 = max(prices[-56:-1])
            if price > prior_high_55:
                breakout_eff = (price - prior_high_55) / max(1e-9, (atr_pct / 100.0) * price)
                breakout_eff_norm = max(0.0, min(100.0, (breakout_eff / 2.0) * 100.0))

        rv5 = self._realized_vol_pct(prices, 5) or 0.0
        rv20 = self._realized_vol_pct(prices, 20) or 0.0
        whipsaw_count_20 = self._whipsaw_count_around_ema(prices, ema20, ema50, lookback=20)

        trend_quality = (
            slope_norm * 0.25
            + (trend_persistence * 100.0) * 0.25
            + (adx_persistence * 100.0) * 0.20
            + rs_slope_norm * 0.20
            + breakout_eff_norm * 0.10
        )

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
            "trend_quality_score": round(trend_quality, 2),
            "rv5_pct": round(rv5, 2),
            "rv20_pct": round(rv20, 2),
            "whipsaw_count_20": int(whipsaw_count_20),
            "breakout_efficiency": round(breakout_eff, 4),
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
