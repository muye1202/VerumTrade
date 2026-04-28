from __future__ import annotations
"""
Market Context Snapshot:
Computes macro-level indicators, volatility indices, and broader market trend data to establish a "market regime" snapshot.
"""

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.config import get_config

from .pipeline_cache import load_cache_value, save_cache_value, stable_key
from .universe_prefilters import _fetch_daily_earnings_symbols_from_yahoo
from .pipeline_utils import parse_ohlcv_rows


class PreStage0IntelligenceBuilder:
    """Compute strict-scope, pre-Stage-0 market intelligence snapshot."""

    _VERSION = "v1"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._metrics: Dict[str, int] = {
            "cache_hits": 0,
            "cache_misses": 0,
            "vendor_calls_estimate": 0,
            "llm_calls": 0,
        }

    def _cache_cfg(self, ttl_hours: int) -> Dict[str, Any]:
        base = {
            "enabled": True,
            "ttl_hours": int(ttl_hours),
            "force_refresh": False,
            "dir": None,
        }
        numeric_cfg = (
            (self.config.get("numeric_filter") or {})
            .get("stage0_cache", {}) or {}
        )
        pre_cfg = (self.config.get("pre_stage0_intelligence") or {}).get("cache", {}) or {}
        merged = {**base, **numeric_cfg, **pre_cfg}
        return merged

    @staticmethod
    def _parse_ohlcv(raw_csv: str) -> List[Dict[str, Any]]:
        return parse_ohlcv_rows(raw_csv)

    @staticmethod
    def _safe_pct(a: float, b: float) -> float:
        if b == 0:
            return 0.0
        return (a / b - 1.0) * 100.0

    @staticmethod
    def _sma(closes: List[float], window: int) -> Optional[float]:
        if len(closes) < window or window <= 0:
            return None
        seg = closes[-window:]
        return sum(seg) / float(window)

    @staticmethod
    def _realized_vol(closes: List[float], window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        r: List[float] = []
        for i in range(len(closes) - window, len(closes)):
            prev = closes[i - 1]
            cur = closes[i]
            if prev <= 0 or cur <= 0:
                continue
            r.append(math.log(cur / prev))
        if len(r) < max(2, window - 1):
            return None
        mean = sum(r) / len(r)
        var = sum((x - mean) ** 2 for x in r) / max(1, (len(r) - 1))
        return (var ** 0.5) * (252.0 ** 0.5) * 100.0

    @staticmethod
    def _atr(rows: List[Dict[str, Any]], window: int = 20) -> Optional[float]:
        if len(rows) < window + 1:
            return None
        tr: List[float] = []
        for i in range(len(rows) - window, len(rows)):
            high = rows[i].get("High")
            low = rows[i].get("Low")
            prev_close = rows[i - 1].get("Close") if i > 0 else None
            if high is None or low is None:
                continue
            if prev_close is None:
                tr.append(abs(high - low))
            else:
                tr.append(max(abs(high - low), abs(high - prev_close), abs(low - prev_close)))
        if not tr:
            return None
        return sum(tr) / float(len(tr))

    @staticmethod
    def _return_n(closes: List[float], n: int) -> Optional[float]:
        if len(closes) < n + 1 or n <= 0:
            return None
        prev = closes[-(n + 1)]
        cur = closes[-1]
        if prev == 0:
            return None
        return ((cur - prev) / prev) * 100.0

    @staticmethod
    def _ytd_return(rows: List[Dict[str, Any]], trade_date: str) -> Optional[float]:
        if not rows:
            return None
        year = int(trade_date[:4])
        first = None
        for row in rows:
            dt = row.get("Date", "")
            if not dt.startswith(f"{year}-"):
                continue
            first = row.get("Close")
            if isinstance(first, (float, int)):
                break
        last = rows[-1].get("Close")
        if not isinstance(first, (float, int)) or not isinstance(last, (float, int)) or first == 0:
            return None
        return ((float(last) - float(first)) / float(first)) * 100.0

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def _fetch_series(self, symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        route_fn = self.config.get("_route_to_vendor")
        if route_fn is None:
            from tradingagents.dataflows.interface import route_to_vendor
            route_fn = route_to_vendor

        cfg = self._cache_cfg(ttl_hours=24)
        key = stable_key({
            "v": self._VERSION,
            "type": "symbol_series",
            "symbol": symbol,
            "start": start_date,
            "end": end_date,
        })
        cached, hit = load_cache_value(
            namespace="pre_stage0_symbol_series",
            key=key,
            cache_config=cfg,
            metrics=self._metrics,
        )
        if hit and isinstance(cached, list):
            return cached

        self._metrics["vendor_calls_estimate"] = int(self._metrics.get("vendor_calls_estimate", 0)) + 1
        raw = route_fn("get_stock_data", symbol, start_date, end_date)
        rows = self._parse_ohlcv(raw)
        if rows:
            save_cache_value(
                namespace="pre_stage0_symbol_series",
                key=key,
                value=rows,
                cache_config=cfg,
            )
        return rows

    def _index_regime_block(self, trade_date: str) -> Dict[str, Any]:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=430)).strftime("%Y-%m-%d")
        out: Dict[str, Any] = {"indices": {}}
        for symbol in ["SPY", "QQQ", "IWM", "DIA"]:
            rows = self._fetch_series(symbol, start, trade_date)
            if len(rows) < 220:
                continue
            closes = [float(r["Close"]) for r in rows if isinstance(r.get("Close"), (int, float))]
            if len(closes) < 220:
                continue
            cur = closes[-1]
            prev = closes[-2]
            open_today = rows[-1].get("Open")
            atr20 = self._atr(rows, 20)
            sma20 = self._sma(closes, 20)
            sma50 = self._sma(closes, 50)
            sma200 = self._sma(closes, 200)
            rv5 = self._realized_vol(closes, 5)
            rv20 = self._realized_vol(closes, 20)
            ret1d = self._return_n(closes, 1)
            ret5d = self._return_n(closes, 5)
            ret20d = self._return_n(closes, 20)
            ret63d = self._return_n(closes, 63)
            ytd = self._ytd_return(rows, trade_date)
            gap_pct = None
            gap_atr_units = None
            if isinstance(open_today, (float, int)) and isinstance(prev, (float, int)) and prev != 0:
                gap_pct = ((float(open_today) - float(prev)) / float(prev)) * 100.0
                if isinstance(atr20, (float, int)) and atr20 and atr20 > 0:
                    gap_atr_units = (float(open_today) - float(prev)) / float(atr20)

            above_all = bool(
                sma20 is not None and sma50 is not None and sma200 is not None
                and cur > sma20 and cur > sma50 and cur > sma200
            )
            dist = {
                "to_20dma_pct": self._safe_pct(cur, sma20) if sma20 else None,
                "to_50dma_pct": self._safe_pct(cur, sma50) if sma50 else None,
                "to_200dma_pct": self._safe_pct(cur, sma200) if sma200 else None,
            }
            # Simple "whipsaw around 20DMA": multiple sign changes in last 10 sessions.
            whipsaw_count = 0
            if sma20:
                diffs = []
                for px in closes[-10:]:
                    diffs.append(1 if px >= sma20 else -1)
                for i in range(1, len(diffs)):
                    if diffs[i] != diffs[i - 1]:
                        whipsaw_count += 1
            intraday_ranges = []
            for row in rows[-10:]:
                hi = row.get("High")
                lo = row.get("Low")
                cl = row.get("Close")
                if isinstance(hi, (float, int)) and isinstance(lo, (float, int)) and isinstance(cl, (float, int)) and cl:
                    intraday_ranges.append(((float(hi) - float(lo)) / float(cl)) * 100.0)
            avg_intraday_range_10d = (sum(intraday_ranges) / len(intraday_ranges)) if intraday_ranges else None
            trend_flag = bool((ret20d or 0.0) > 0 and sma50 is not None and cur > sma50 and (rv5 or 0) <= (rv20 or 1e9) * 1.2)
            mean_rev_flag = bool(whipsaw_count >= 3 and (avg_intraday_range_10d or 0.0) >= 1.25)
            out["indices"][symbol] = {
                "price": round(cur, 4),
                "overnight_gap_pct": round(gap_pct, 4) if gap_pct is not None else None,
                "overnight_gap_atr20_units": round(gap_atr_units, 4) if gap_atr_units is not None else None,
                "returns_pct": {
                    "1d": round(ret1d, 4) if ret1d is not None else None,
                    "5d": round(ret5d, 4) if ret5d is not None else None,
                    "20d": round(ret20d, 4) if ret20d is not None else None,
                    "63d": round(ret63d, 4) if ret63d is not None else None,
                    "ytd": round(ytd, 4) if ytd is not None else None,
                },
                "distance_to_dma_pct": {k: (round(v, 4) if isinstance(v, float) else None) for k, v in dist.items()},
                "above_all_smas": above_all,
                "realized_vol_annualized_pct": {
                    "5d": round(rv5, 4) if rv5 is not None else None,
                    "20d": round(rv20, 4) if rv20 is not None else None,
                },
                "regime_flags": {
                    "TRENDING": trend_flag,
                    "MEAN_REVERTING": mean_rev_flag,
                },
                "whipsaw_count_10d": whipsaw_count,
                "avg_intraday_range_pct_10d": round(avg_intraday_range_10d, 4) if avg_intraday_range_10d is not None else None,
            }
        return out

    def _simple_return_block(self, trade_date: str, symbols: List[str], lookback_days: int = 90) -> Dict[str, Dict[str, Any]]:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        out: Dict[str, Dict[str, Any]] = {}
        for s in symbols:
            rows = self._fetch_series(s, start, trade_date)
            closes = [float(r["Close"]) for r in rows if isinstance(r.get("Close"), (int, float))]
            if len(closes) < 22:
                continue
            ret1 = self._return_n(closes, 1)
            ret5 = self._return_n(closes, 5)
            ret20 = self._return_n(closes, 20)
            out[s] = {
                "level": round(closes[-1], 4),
                "returns_pct": {
                    "1d": round(ret1, 4) if ret1 is not None else None,
                    "5d": round(ret5, 4) if ret5 is not None else None,
                    "20d": round(ret20, 4) if ret20 is not None else None,
                },
            }
        return out

    def _vol_options_block(self, trade_date: str, spy_rv20: Optional[float]) -> Dict[str, Any]:
        base = self._simple_return_block(trade_date, ["^VIX"], lookback_days=40)
        vix = base.get("^VIX", {})
        vix_level = vix.get("level")
        rv_spread = None
        if isinstance(spy_rv20, (int, float)) and isinstance(vix_level, (int, float)):
            rv_spread = float(spy_rv20) - float(vix_level)
        return {
            "vix": vix,
            "rv20_minus_vix": round(rv_spread, 4) if rv_spread is not None else None,
        }

    def _rates_macro_block(self, trade_date: str) -> Dict[str, Any]:
        rates = self._simple_return_block(trade_date, ["^IRX", "^TNX", "^TYX"], lookback_days=30)
        ten = rates.get("^TNX", {}).get("level")
        three_m = rates.get("^IRX", {}).get("level")
        slope = None
        if isinstance(ten, (int, float)) and isinstance(three_m, (int, float)):
            slope = float(ten) - float(three_m)
        impulse = None
        ten_1d = ((rates.get("^TNX", {}).get("returns_pct") or {}).get("1d"))
        if isinstance(ten_1d, (int, float)):
            if ten_1d > 0.25:
                impulse = "RATES_UP"
            elif ten_1d < -0.25:
                impulse = "RATES_DOWN"
            else:
                impulse = "RATES_FLAT"
        return {
            "yields": rates,
            "curve_slopes": {
                "10y_minus_3m": round(slope, 4) if slope is not None else None,
            },
            "rate_impulse": impulse,
        }

    def _credit_block(self, trade_date: str) -> Dict[str, Any]:
        credit = self._simple_return_block(trade_date, ["HYG", "LQD", "SPY"], lookback_days=60)
        hyg = credit.get("HYG", {}).get("level")
        spy = credit.get("SPY", {}).get("level")
        ratio = None
        if isinstance(hyg, (int, float)) and isinstance(spy, (int, float)) and spy != 0:
            ratio = float(hyg) / float(spy)
        return {
            "etf_pulse": credit,
            "hyg_spy_ratio": round(ratio, 6) if ratio is not None else None,
        }

    def _cross_asset_block(self, trade_date: str) -> Dict[str, Any]:
        primary = self._simple_return_block(
            trade_date,
            ["DX-Y.NYB", "JPY=X", "CL=F", "BZ=F", "GC=F", "HG=F", "BTC-USD", "ETH-USD"],
            lookback_days=60,
        )
        usd = primary.get("DX-Y.NYB") or primary.get("JPY=X") or {}
        return {
            "usd": usd,
            "oil_wti": primary.get("CL=F", {}),
            "oil_brent": primary.get("BZ=F", {}),
            "gold": primary.get("GC=F", {}),
            "copper": primary.get("HG=F", {}),
            "btc": primary.get("BTC-USD", {}),
            "eth": primary.get("ETH-USD", {}),
        }

    def _sector_factor_block(self, trade_date: str) -> Dict[str, Any]:
        sectors = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SPY"]
        factors = ["IWF", "IWD", "IWM", "SPY", "MTUM", "SPLV"]
        sec = self._simple_return_block(trade_date, sectors, lookback_days=90)
        fac = self._simple_return_block(trade_date, factors, lookback_days=90)
        spy_ret = (sec.get("SPY", {}).get("returns_pct") or {})
        heatmap: Dict[str, Any] = {}
        for etf, row in sec.items():
            if etf == "SPY":
                continue
            r = row.get("returns_pct") or {}
            heatmap[etf] = {
                "returns_pct": r,
                "rs_vs_spy_pct": {
                    "1d": (round(float(r["1d"]) - float(spy_ret["1d"]), 4) if isinstance(r.get("1d"), (int, float)) and isinstance(spy_ret.get("1d"), (int, float)) else None),
                    "5d": (round(float(r["5d"]) - float(spy_ret["5d"]), 4) if isinstance(r.get("5d"), (int, float)) and isinstance(spy_ret.get("5d"), (int, float)) else None),
                    "20d": (round(float(r["20d"]) - float(spy_ret["20d"]), 4) if isinstance(r.get("20d"), (int, float)) and isinstance(spy_ret.get("20d"), (int, float)) else None),
                },
            }

        def _spread(a: str, b: str) -> Optional[float]:
            ar = (((fac.get(a) or {}).get("returns_pct") or {}).get("20d"))
            br = (((fac.get(b) or {}).get("returns_pct") or {}).get("20d"))
            if isinstance(ar, (int, float)) and isinstance(br, (int, float)):
                return round(float(ar) - float(br), 4)
            return None

        return {
            "sector_heatmap": heatmap,
            "factor_spreads_20d_pct": {
                "growth_minus_value": _spread("IWF", "IWD"),
                "small_minus_large": _spread("IWM", "SPY"),
                "momentum_minus_spy": _spread("MTUM", "SPY"),
                "lowvol_minus_spy": _spread("SPLV", "SPY"),
            },
        }

    @staticmethod
    def _is_opex_week(trade_date: str) -> bool:
        dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        # OPEX proxy: week containing third Friday.
        first = date(dt.year, dt.month, 1)
        first_friday_offset = (4 - first.weekday()) % 7
        third_friday = first + timedelta(days=first_friday_offset + 14)
        week_start = dt - timedelta(days=dt.weekday())
        week_end = week_start + timedelta(days=6)
        return week_start <= third_friday <= week_end

    @staticmethod
    def _month_end_flag(trade_date: str) -> bool:
        dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        next_month = (dt.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = next_month - timedelta(days=1)
        return (last_day - dt).days <= 3

    def _earnings_intensity(self, trade_date: str) -> Dict[str, Any]:
        dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        counts: Dict[str, int] = {}
        for d in range(0, 5):
            day = (dt + timedelta(days=d)).strftime("%Y-%m-%d")
            try:
                symbols = _fetch_daily_earnings_symbols_from_yahoo(
                    day_str=day,
                    page_size=100,
                    http_timeout_s=10,
                )
                counts[day] = int(len(symbols))
                self._metrics["vendor_calls_estimate"] = int(self._metrics.get("vendor_calls_estimate", 0)) + 1
            except Exception:
                counts[day] = 0
        avg_count = (sum(counts.values()) / len(counts)) if counts else 0.0
        label = "low"
        if avg_count >= 400:
            label = "high"
        elif avg_count >= 200:
            label = "medium"
        return {
            "daily_counts_next_5d": counts,
            "avg_daily_count": round(avg_count, 2),
            "intensity_label": label,
        }

    def _calendar_block(self, trade_date: str) -> Dict[str, Any]:
        dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        return {
            "earnings_season_proxy": self._earnings_intensity(trade_date),
            "opex_week_flag": self._is_opex_week(trade_date),
            "month_end_flag": self._month_end_flag(trade_date),
            "quarter_end_flag": self._month_end_flag(trade_date) and dt.month in {3, 6, 9, 12},
        }

    def _global_news_block(self) -> Dict[str, Any]:
        route_fn = self.config.get("_route_to_vendor")
        if route_fn is None:
            from tradingagents.dataflows.interface import route_to_vendor
            route_fn = route_to_vendor

        try:
            raw_news = route_fn("get_global_news", limit=15)
            self._metrics["vendor_calls_estimate"] = int(self._metrics.get("vendor_calls_estimate", 0)) + 1
        except Exception:
            raw_news = ""

        return {
            "headlines_markdown": str(raw_news)
        }

    @staticmethod
    def _build_indicator_availability() -> Dict[str, Any]:
        computed = [
            "index_regime_snapshot",
            "vix_level_and_changes",
            "rv20_minus_vix",
            "rates_levels_and_daily_changes",
            "curve_10y_3m_if_available",
            "credit_etf_pulse_hyg_lqd",
            "cross_asset_usd_oil_gold_copper_crypto",
            "sector_heatmap_and_rs_vs_spy",
            "factor_spreads_growth_value_small_large_mtum_splv",
            "calendar_earnings_intensity_opex_month_end_quarter_end",
            "global_macro_news",
        ]
        skipped = [
            "true_premarket_prints",
            "futures_term_structure_front_vs_second",
            "advance_decline_internals",
            "pct_constituents_above_key_mas",
            "new_highs_minus_new_lows",
            "up_volume_down_volume_ratio",
            "top10_index_contribution",
            "fedwatch_probabilities",
            "move_index",
            "hy_oas_and_ccc_oas",
            "etf_creation_redemption_flows",
            "zero_dte_share",
            "macro_release_feed",
        ]
        return {
            "computed": computed,
            "skipped_unavailable": skipped,
            "failed_runtime": [],
        }

    def build(self, trade_date: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        cfg = self._cache_cfg(ttl_hours=24)
        key = stable_key({
            "v": self._VERSION,
            "type": "pre_stage0_snapshot",
            "trade_date": trade_date,
            "data_vendors": (get_config().get("data_vendors") or {}),
            "tool_vendors": (get_config().get("tool_vendors") or {}),
        })
        cached, hit = load_cache_value(
            namespace="pre_stage0_snapshot",
            key=key,
            cache_config=cfg,
            metrics=self._metrics,
        )
        if hit and isinstance(cached, dict):
            snapshot = dict(cached)
            snapshot["cache_metrics"] = dict(self._metrics)
            availability = snapshot.get("indicator_availability") or self._build_indicator_availability()
            return snapshot, availability

        availability = self._build_indicator_availability()
        snapshot: Dict[str, Any] = {
            "trade_date": trade_date,
            "index_regime": {},
            "vol_options": {},
            "rates_macro": {},
            "credit_liquidity": {},
            "cross_asset": {},
            "sector_factor": {},
            "calendar": {},
            "global_news": {},
            "indicator_availability": availability,
            "cache_metrics": {},
        }
        try:
            snapshot["index_regime"] = self._index_regime_block(trade_date)
            spy_rv20 = (
                (((snapshot.get("index_regime") or {}).get("indices") or {}).get("SPY", {}))
                .get("realized_vol_annualized_pct", {})
                .get("20d")
            )
            snapshot["vol_options"] = self._vol_options_block(trade_date, spy_rv20)
            snapshot["rates_macro"] = self._rates_macro_block(trade_date)
            snapshot["credit_liquidity"] = self._credit_block(trade_date)
            snapshot["cross_asset"] = self._cross_asset_block(trade_date)
            snapshot["sector_factor"] = self._sector_factor_block(trade_date)
            snapshot["calendar"] = self._calendar_block(trade_date)
            snapshot["global_news"] = self._global_news_block()
        except Exception as e:
            availability["failed_runtime"].append(f"snapshot_build_failed:{type(e).__name__}")

        # Composite risk-off proxy
        try:
            spy_1d = (
                (((snapshot.get("index_regime") or {}).get("indices") or {}).get("SPY", {}))
                .get("returns_pct", {})
                .get("1d")
            )
            vix_1d = (
                (((snapshot.get("vol_options") or {}).get("vix") or {}).get("returns_pct") or {})
                .get("1d")
            )
            hyg_1d = (
                ((((snapshot.get("credit_liquidity") or {}).get("etf_pulse") or {}).get("HYG", {})).get("returns_pct") or {})
                .get("1d")
            )
            risk_off = bool(
                isinstance(spy_1d, (int, float)) and spy_1d < 0
                and isinstance(vix_1d, (int, float)) and vix_1d > 0
                and isinstance(hyg_1d, (int, float)) and hyg_1d < 0
            )
            snapshot["index_regime"]["risk_off_flag"] = risk_off
        except Exception:
            pass

        snapshot["cache_metrics"] = dict(self._metrics)
        save_cache_value(
            namespace="pre_stage0_snapshot",
            key=key,
            value=snapshot,
            cache_config=cfg,
        )
        return snapshot, availability
