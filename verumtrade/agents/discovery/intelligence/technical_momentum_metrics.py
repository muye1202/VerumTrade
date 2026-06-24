from __future__ import annotations
"""
Technical Momentum Metrics:
Calculates technical momentum signals and underlying metrics used by anomaly scans.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from .pipeline_models import TechnicalSignal
from .universe_prefilters import (
    filter_by_avg_daily_dollar_volume,
    filter_by_upcoming_earnings,
    filter_by_recent_8k,
)
from .pipeline_cache import (
    load_cache_value,
    save_cache_value,
    stable_key,
)
from .pipeline_utils import (
    compute_obv_slope,
    compute_return_pct,
    extract_indicator_value,
    fetch_alpaca_tradeable_assets,
    fetch_alpaca_primary_us_equities,
    normalize_linear,
    parse_json_dict,
    parse_price_volume_csv,
    safe_float,
)


TECHNICAL_SCANNER_SYSTEM_PROMPT = """You are a technical analysis screener. Your ONLY job is to evaluate pre-computed technical data and score each stock's setup quality.

You will receive a table of stocks with their technical indicators. For each stock, score the setup quality and identify the volume/trend regime.

Scoring guide:
- composite_score: 0-100 weighted score based on:
  * Price above 50 SMA AND 200 SMA: +25 points
  * ADX > 25 (trending): +20 points
  * ADX > 40 (strong trend): +10 bonus points
  * 20-day momentum > 5%: +15 points
  * 20-day momentum > 10%: +5 bonus points
  * Relative strength vs SPY > 1.0: +15 points
  * Volume ratio > 1.2 (above-average volume): +10 points
  * OBV accumulation pattern: +10 points (detect from data if possible)

- obv_trend: Based on the price/volume relationship:
  * "accumulation" if price up AND volume expanding
  * "distribution" if price up BUT volume declining (bearish divergence)
  * "neutral" if unclear

Return ONLY this JSON structure:
{
  "signals": [
    {
      "ticker": "NVDA",
      "price": 950.00,
      "vs_sma50_pct": 8.5,
      "vs_sma200_pct": 45.2,
      "momentum_20d": 12.3,
      "adx": 35.0,
      "obv_trend": "accumulation",
      "relative_strength_vs_spy": 1.85,
      "volume_ratio": 1.4,
      "composite_score": 85
    }
  ]
}"""


class TechnicalMomentumScanner:
    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self._stage0_last_metrics: Dict[str, Any] = {}

    def _emit_progress(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        cb = self.config.get("discovery_progress_callback")
        if callable(cb):
            try:
                cb(event, payload or {})
            except Exception:
                pass

    def _numeric_filter_settings(self) -> Dict[str, Any]:
        defaults = {
            "gates": {
                "min_price": 5.0,
                "min_avg_volume_20d": 500_000.0,
                "require_above_sma200": True,
                "min_adx": 18.0,
                "min_volume_ratio": 0.8,
                "min_roc_20d": -5.0,
            },
            "weights": {
                "roc_20d": 0.20,
                "rs_vs_spy_20d": 0.20,
                "adx": 0.15,
                "volume_ratio": 0.10,
                "trend_vs_sma": 0.15,
                "bollinger_pct_b": 0.10,
                "obv_slope_10d": 0.10,
            },
            "universe_prefilter": {
                "min_avg_dollar_volume_20d": 10_000_000.0,
                "dollar_volume_lookback_days": 20,
                "max_workers": 6,
            },
            "catalyst_prefilter": {
                "enabled": True,
                "mode": "daily_calendar",
                "window_days": 7,
                "max_workers": 4,
                "failure_policy": "fail_closed",
                "http_timeout_s": 12,
                "calendar_page_size": 100,
            },
            "stage0_cache": {
                "enabled": True,
                "ttl_hours": 24,
                "dir": None,
                "force_refresh": False,
            },
        }
        override = self.config.get("numeric_filter", {})
        return {
            "gates": {**defaults["gates"], **override.get("gates", {})},
            "weights": {**defaults["weights"], **override.get("weights", {})},
            "universe_prefilter": {
                **defaults["universe_prefilter"],
                **override.get("universe_prefilter", {}),
            },
            "catalyst_prefilter": {
                **defaults["catalyst_prefilter"],
                **override.get("catalyst_prefilter", {}),
            },
            "stage0_cache": {
                **defaults["stage0_cache"],
                **override.get("stage0_cache", {}),
            },
        }

    @staticmethod
    def _normalize_catalyst_mode(value: Any, default: str = "daily_calendar") -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return default
        raw = raw.split("(", 1)[0].strip().replace("-", "_").replace(" ", "_")
        aliases = {
            "daily_calendar": "daily_calendar",
            "daily": "daily_calendar",
            "calendar": "daily_calendar",
            "per_ticker_calendar": "per_ticker_calendar",
            "pertickercalendar": "per_ticker_calendar",
            "per_ticker": "per_ticker_calendar",
            "ticker_calendar": "per_ticker_calendar",
            "per_symbol_calendar": "per_ticker_calendar",
            "per_stock_calendar": "per_ticker_calendar",
        }
        return aliases.get(raw, default)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        return safe_float(value)

    def _fetch_tradeable_primary_us_equities(
        self,
        trade_date: Optional[str] = None,
        stage0_cache_cfg: Optional[Dict[str, Any]] = None,
        stage0_metrics: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        return fetch_alpaca_primary_us_equities(
            trade_date=trade_date,
            cache_config=stage0_cache_cfg,
            metrics=stage0_metrics,
        )

    # Backward-compatible helper retained for tests/legacy call sites.
    def _fetch_alpaca_tradeable_assets(self, trade_date: Optional[str] = None) -> List[str]:
        cfg = self._numeric_filter_settings()["universe_prefilter"]
        return fetch_alpaca_tradeable_assets(
            trade_date=trade_date,
            min_avg_dollar_volume_20d=float(cfg["min_avg_dollar_volume_20d"]),
            dollar_volume_lookback_days=int(cfg["dollar_volume_lookback_days"]),
            max_workers=int(cfg["max_workers"]),
            cache_config=self._numeric_filter_settings()["stage0_cache"],
        )

    def get_stage0_last_metrics(self) -> Dict[str, Any]:
        return dict(self._stage0_last_metrics or {})

    def build_numeric_universe(
        self,
        trade_date: str,
        excluded_tickers: Optional[List[str]] = None,
        stage0_overrides: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        settings = self._numeric_filter_settings()
        universe_cfg = dict(settings["universe_prefilter"])
        self._emit_progress("stage0.start", {"trade_date": trade_date})
        cfg = dict(settings["catalyst_prefilter"])
        stage0_cache_cfg = settings["stage0_cache"]
        self._apply_stage0_overrides(universe_cfg, cfg, stage0_overrides or {})
        excluded = sorted(
            {
                str(t).strip().upper()
                for t in (excluded_tickers or [])
                if str(t).strip()
            }
        )
        stage0_metrics: Dict[str, Any] = {
            "assets_fetch_s": 0.0,
            "earnings_filter_s": 0.0,
            "adv_filter_s": 0.0,
            "cache_hits": 0,
            "cache_misses": 0,
            "vendor_calls_estimate": 0,
        }

        stage0_universe_key = stable_key(
            {
                "trade_date": trade_date,
                "catalyst_mode": str(cfg.get("mode", "daily_calendar")),
                "catalyst_window_days": int(cfg.get("window_days", 7)),
                "failure_policy": str(cfg.get("failure_policy", "fail_closed")),
                "http_timeout_s": int(cfg.get("http_timeout_s", 12)),
                "calendar_page_size": int(cfg.get("calendar_page_size", 100)),
                "adv_min": float(universe_cfg["min_avg_dollar_volume_20d"]),
                "adv_lookback_days": int(universe_cfg["dollar_volume_lookback_days"]),
                "excluded_tickers": excluded,
            }
        )
        cached_universe, cached_hit = load_cache_value(
            namespace="stage0_final_universe",
            key=stage0_universe_key,
            cache_config=stage0_cache_cfg,
            metrics=stage0_metrics,
        )
        if cached_hit and isinstance(cached_universe, (list, dict)):
            if isinstance(cached_universe, dict):
                cached_filtered = cached_universe.get("filtered_universe", []) or []
                base_len = int(cached_universe.get("base_universe", len(cached_filtered)))
            else:
                cached_filtered = cached_universe
                base_len = len(cached_filtered)
            filtered = [str(t).strip().upper() for t in cached_filtered if str(t).strip()]
            self._stage0_last_metrics = stage0_metrics
            self._emit_progress("stage0.complete", {
                "trade_date": trade_date,
                "mode": str(cfg.get("mode", "daily_calendar")),
                "base_universe": base_len,
                "filtered_universe": len(filtered),
            })
            self._emit_progress("stage0.metrics", self.get_stage0_last_metrics())
            return filtered

        t0 = time.time()
        base = self._fetch_tradeable_primary_us_equities(
            trade_date=trade_date,
            stage0_cache_cfg=stage0_cache_cfg,
            stage0_metrics=stage0_metrics,
        )
        stage0_metrics["assets_fetch_s"] = round(time.time() - t0, 2)
        stage0_metrics["base_universe"] = len(base)
        self.logger.info(
            "Stage 0 catalyst prefilter settings: "
            f"mode={cfg.get('mode', 'daily_calendar')} "
            f"window_days={cfg.get('window_days', 7)} "
            f"failure_policy={cfg.get('failure_policy', 'fail_closed')} "
            f"base_universe={len(base)}"
        )
        if not bool(cfg.get("enabled", True)):
            t_adv = time.time()
            filtered = filter_by_avg_daily_dollar_volume(
                symbols=base,
                trade_date=trade_date,
                min_avg_dollar_volume_20d=float(universe_cfg["min_avg_dollar_volume_20d"]),
                lookback_days=int(universe_cfg["dollar_volume_lookback_days"]),
                max_workers=int(universe_cfg["max_workers"]),
                cache_config=stage0_cache_cfg,
                metrics=stage0_metrics,
            )
            stage0_metrics["adv_filter_s"] = round(time.time() - t_adv, 2)
            if excluded:
                excluded_set = set(excluded)
                filtered = [t for t in filtered if t not in excluded_set]
            save_cache_value(
                namespace="stage0_final_universe",
                key=stage0_universe_key,
                value={
                    "base_universe": len(base),
                    "filtered_universe": filtered,
                },
                cache_config=stage0_cache_cfg,
            )
            self._stage0_last_metrics = stage0_metrics
            self.logger.info(
                "Numeric universe built: "
                f"{len(filtered)} tickers (catalyst prefilter disabled, base={len(base)})"
            )
            self._emit_progress(
                "stage0.complete",
                {
                    "trade_date": trade_date,
                    "mode": "disabled",
                    "base_universe": len(base),
                    "filtered_universe": len(filtered),
                },
            )
            self._emit_progress("stage0.metrics", self.get_stage0_last_metrics())
            return filtered

        mode = str(cfg.get("mode", "daily_calendar")).strip().lower()
        enable_8k = bool(cfg.get("enable_8k_filter", True))
        
        if mode == "daily_calendar":
            t_earnings = time.time()
            catalyst_filtered = filter_by_upcoming_earnings(
                symbols=base,
                analysis_date=trade_date,
                mode=mode,
                window_days=int(cfg.get("window_days", 7)),
                max_workers=int(cfg.get("max_workers", 4)),
                failure_policy=str(cfg.get("failure_policy", "fail_closed")),
                http_timeout_s=int(cfg.get("http_timeout_s", 12)),
                calendar_page_size=int(cfg.get("calendar_page_size", 100)),
                cache_config=stage0_cache_cfg,
                metrics=stage0_metrics,
            )
            stage0_metrics["earnings_filter_s"] = round(time.time() - t_earnings, 2)
            
            if enable_8k:
                t_8k = time.time()
                sec_filtered = filter_by_recent_8k(
                    symbols=base,
                    max_workers=int(cfg.get("max_workers", 4)),
                    failure_policy=str(cfg.get("failure_policy", "fail_closed"))
                )
                stage0_metrics["8k_filter_s"] = round(time.time() - t_8k, 2)
                catalyst_filtered = sorted(set(catalyst_filtered + sec_filtered))

            t_adv = time.time()
            filtered = filter_by_avg_daily_dollar_volume(
                symbols=catalyst_filtered,
                trade_date=trade_date,
                min_avg_dollar_volume_20d=float(universe_cfg["min_avg_dollar_volume_20d"]),
                lookback_days=int(universe_cfg["dollar_volume_lookback_days"]),
                max_workers=int(universe_cfg["max_workers"]),
                cache_config=stage0_cache_cfg,
                metrics=stage0_metrics,
            )
            stage0_metrics["adv_filter_s"] = round(time.time() - t_adv, 2)
        else:
            t_adv = time.time()
            liquidity_filtered = filter_by_avg_daily_dollar_volume(
                symbols=base,
                trade_date=trade_date,
                min_avg_dollar_volume_20d=float(universe_cfg["min_avg_dollar_volume_20d"]),
                lookback_days=int(universe_cfg["dollar_volume_lookback_days"]),
                max_workers=int(universe_cfg["max_workers"]),
                cache_config=stage0_cache_cfg,
                metrics=stage0_metrics,
            )
            stage0_metrics["adv_filter_s"] = round(time.time() - t_adv, 2)

            t_earnings = time.time()
            catalyst_filtered = filter_by_upcoming_earnings(
                symbols=liquidity_filtered,
                analysis_date=trade_date,
                mode=mode,
                window_days=int(cfg.get("window_days", 7)),
                max_workers=int(cfg.get("max_workers", 4)),
                failure_policy=str(cfg.get("failure_policy", "fail_closed")),
                http_timeout_s=int(cfg.get("http_timeout_s", 12)),
                calendar_page_size=int(cfg.get("calendar_page_size", 100)),
                cache_config=stage0_cache_cfg,
                metrics=stage0_metrics,
            )
            stage0_metrics["earnings_filter_s"] = round(time.time() - t_earnings, 2)
            
            if enable_8k:
                t_8k = time.time()
                sec_filtered = filter_by_recent_8k(
                    symbols=liquidity_filtered,
                    max_workers=int(cfg.get("max_workers", 4)),
                    failure_policy=str(cfg.get("failure_policy", "fail_closed"))
                )
                stage0_metrics["8k_filter_s"] = round(time.time() - t_8k, 2)
                filtered = sorted(set(catalyst_filtered + sec_filtered))
            else:
                filtered = catalyst_filtered

        if excluded:
            excluded_set = set(excluded)
            filtered = [t for t in filtered if t not in excluded_set]

        save_cache_value(
            namespace="stage0_final_universe",
            key=stage0_universe_key,
            value={
                "base_universe": len(base),
                "filtered_universe": filtered,
            },
            cache_config=stage0_cache_cfg,
        )
        self._stage0_last_metrics = stage0_metrics
        self.logger.info(
            "Numeric universe catalyst prefilter: "
            f"base={len(base)} filtered={len(filtered)} mode={cfg.get('mode')} window_days={cfg.get('window_days')}"
        )
        self.logger.info(
            "Stage 0 metrics: "
            f"assets={stage0_metrics.get('assets_fetch_s', 0)}s "
            f"earnings={stage0_metrics.get('earnings_filter_s', 0)}s "
            f"adv={stage0_metrics.get('adv_filter_s', 0)}s "
            f"cache_hit_rate="
            f"{(100.0 * stage0_metrics.get('cache_hits', 0) / max(1, stage0_metrics.get('cache_hits', 0) + stage0_metrics.get('cache_misses', 0))):.1f}% "
            f"vendor_calls_est={stage0_metrics.get('vendor_calls_estimate', 0)}"
        )
        self._emit_progress(
            "stage0.complete",
            {
                "trade_date": trade_date,
                "mode": str(cfg.get("mode", "daily_calendar")),
                "base_universe": len(base),
                "filtered_universe": len(filtered),
            },
        )
        self._emit_progress("stage0.metrics", self.get_stage0_last_metrics())
        return filtered

    @staticmethod
    def _apply_stage0_overrides(
        universe_cfg: Dict[str, Any],
        catalyst_cfg: Dict[str, Any],
        overrides: Dict[str, Any],
    ) -> None:
        if not isinstance(overrides, dict):
            return
        if "min_avg_dollar_volume_20d" in overrides:
            try:
                universe_cfg["min_avg_dollar_volume_20d"] = float(overrides["min_avg_dollar_volume_20d"])
            except Exception:
                pass
        if "catalyst_mode" in overrides:
            catalyst_cfg["mode"] = TechnicalMomentumScanner._normalize_catalyst_mode(
                overrides.get("catalyst_mode"),
                default=str(catalyst_cfg.get("mode", "daily_calendar")),
            )
        if "catalyst_window_days" in overrides:
            try:
                catalyst_cfg["window_days"] = max(1, int(overrides["catalyst_window_days"]))
            except Exception:
                pass

    @staticmethod
    def _parse_price_volume_csv(raw_csv: str) -> Tuple[List[float], List[float]]:
        return parse_price_volume_csv(raw_csv)

    @staticmethod
    def _compute_obv_slope_10d(prices: List[float], volumes: List[float]) -> float:
        return compute_obv_slope(prices, volumes, window=10)

    @staticmethod
    def _compute_bollinger_pct_b(price: float, upper: float, lower: float) -> float:
        band = upper - lower
        if band == 0:
            return 0.5
        return (price - lower) / band

    @staticmethod
    def _normalize_linear(value: float, low: float, high: float) -> float:
        return normalize_linear(value, low, high)

    def _apply_hard_gates(self, features: Dict[str, Any]) -> Tuple[bool, List[str]]:
        gates = self._numeric_filter_settings()["gates"]
        fail_reasons: List[str] = []
        if features["price"] < gates["min_price"]:
            fail_reasons.append("min_price")
        if features["avg_volume_20d"] < gates["min_avg_volume_20d"]:
            fail_reasons.append("min_avg_volume_20d")
        if gates["require_above_sma200"] and features["vs_sma200_pct"] <= 0:
            fail_reasons.append("require_above_sma200")
        if features["adx"] < gates["min_adx"]:
            fail_reasons.append("min_adx")
        if features["volume_ratio"] < gates["min_volume_ratio"]:
            fail_reasons.append("min_volume_ratio")
        if features["roc_20d"] < gates["min_roc_20d"]:
            fail_reasons.append("min_roc_20d")
        return (len(fail_reasons) == 0), fail_reasons

    def _compute_weighted_score(self, features: Dict[str, Any], cfg: Dict[str, Any]) -> float:
        weights = cfg["weights"]
        trend_pct = (features["vs_sma50_pct"] + features["vs_sma200_pct"]) / 2.0
        obv_scale = features["obv_slope_10d"] / max(features["avg_volume_20d"], 1.0)
        metric_scores = {
            "roc_20d": self._normalize_linear(features["roc_20d"], -20.0, 25.0),
            "rs_vs_spy_20d": self._normalize_linear(features["rs_vs_spy_20d"], -15.0, 20.0),
            "adx": self._normalize_linear(features["adx"], 10.0, 50.0),
            "volume_ratio": self._normalize_linear(features["volume_ratio"], 0.5, 2.0),
            "trend_vs_sma": self._normalize_linear(trend_pct, -10.0, 30.0),
            "bollinger_pct_b": self._normalize_linear(features["bollinger_pct_b"], 0.0, 1.0),
            "obv_slope_10d": self._normalize_linear(obv_scale, -1.0, 1.0),
        }
        score = 0.0
        for k, w in weights.items():
            score += metric_scores.get(k, 0.0) * float(w)
        return round(score, 2)

    def _compute_numeric_features(self, ticker: str, trade_date: str, spy_roc_20d: float) -> Optional[Dict[str, Any]]:
        from verumtrade.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=380)).strftime("%Y-%m-%d")
        try:
            raw_csv = route_to_vendor("get_stock_data", ticker, start_date, trade_date)
            prices, volumes = self._parse_price_volume_csv(raw_csv)
            if len(prices) < 30 or len(volumes) < 20:
                return None

            current_price = prices[-1]
            roc_20d_raw = compute_return_pct(prices, 20)
            if roc_20d_raw is None:
                return None
            roc_20d = float(roc_20d_raw)
            avg_vol_20d = sum(volumes[-20:]) / 20.0
            avg_vol_5d = sum(volumes[-5:]) / 5.0
            volume_ratio = avg_vol_5d / avg_vol_20d if avg_vol_20d > 0 else 0.0

            sma50 = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "close_50_sma", trade_date, 3))
            sma200 = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "close_200_sma", trade_date, 3))
            adx = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "adx", trade_date, 3))
            boll_ub = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "boll_ub", trade_date, 3))
            boll_lb = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "boll_lb", trade_date, 3))
            if sma50 is None or sma200 is None or adx is None or boll_ub is None or boll_lb is None:
                return None

            vs_sma50 = ((current_price - sma50) / sma50) * 100.0 if sma50 > 0 else 0.0
            vs_sma200 = ((current_price - sma200) / sma200) * 100.0 if sma200 > 0 else 0.0
            pct_b = self._compute_bollinger_pct_b(current_price, boll_ub, boll_lb)
            obv_slope_10d = self._compute_obv_slope_10d(prices, volumes)
            rs_vs_spy_20d = roc_20d - spy_roc_20d
            return {
                "ticker": ticker,
                "price": round(current_price, 2),
                "momentum_20d": round(roc_20d, 2),
                "roc_20d": round(roc_20d, 2),
                "rs_vs_spy_20d": round(rs_vs_spy_20d, 2),
                "relative_strength_vs_spy": round(1.0 + (rs_vs_spy_20d / 100.0), 4),
                "vs_sma50_pct": round(vs_sma50, 2),
                "vs_sma200_pct": round(vs_sma200, 2),
                "adx": round(adx, 2),
                "volume_ratio": round(volume_ratio, 4),
                "avg_volume_20d": round(avg_vol_20d, 2),
                "bollinger_pct_b": round(pct_b, 4),
                "obv_slope_10d": round(obv_slope_10d, 4),
            }
        except Exception as e:
            self.logger.debug(f"Numeric feature fetch failed for {ticker}: {e}")
            return None

    def _fetch_spy_roc_20d(self, trade_date: str) -> float:
        from verumtrade.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")
        raw_csv = route_to_vendor("get_stock_data", "SPY", start, trade_date)
        prices, _ = self._parse_price_volume_csv(raw_csv)
        ret = compute_return_pct(prices, 20)
        if ret is None:
            raise RuntimeError("Unable to compute SPY ROC(20d) for numeric filter.")
        return float(ret)

    def scan_numeric_filter(
        self,
        universe: Optional[List[str]],
        trade_date: str,
        top_n: int = 50,
        max_workers: int = 4,
    ) -> List[TechnicalSignal]:
        cfg = self._numeric_filter_settings()
        base_universe = universe or self.build_numeric_universe(trade_date)
        if not base_universe:
            return []

        spy_roc_20d = self._fetch_spy_roc_20d(trade_date)
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._compute_numeric_features, ticker, trade_date, spy_roc_20d): ticker
                for ticker in base_universe
            }
            for future in as_completed(futures):
                row = future.result()
                if row:
                    gate_passed, gate_fail_reasons = self._apply_hard_gates(row)
                    row["gate_passed"] = gate_passed
                    row["gate_fail_reasons"] = gate_fail_reasons
                    row["composite_score"] = self._compute_weighted_score(row, cfg)
                    rows.append(row)

        passed = [r for r in rows if r["gate_passed"]]
        passed.sort(key=lambda x: x["composite_score"], reverse=True)
        return [
            TechnicalSignal(
                ticker=r["ticker"],
                price=r["price"],
                vs_sma50_pct=r["vs_sma50_pct"],
                vs_sma200_pct=r["vs_sma200_pct"],
                momentum_20d=r["momentum_20d"],
                adx=r["adx"],
                obv_trend="neutral",
                relative_strength_vs_spy=r["relative_strength_vs_spy"],
                volume_ratio=r["volume_ratio"],
                composite_score=r["composite_score"],
                roc_20d=r["roc_20d"],
                rs_vs_spy_20d=r["rs_vs_spy_20d"],
                bollinger_pct_b=r["bollinger_pct_b"],
                obv_slope_10d=r["obv_slope_10d"],
                avg_volume_20d=r["avg_volume_20d"],
                gate_passed=r["gate_passed"],
                gate_fail_reasons=r["gate_fail_reasons"],
            )
            for r in passed[:top_n]
        ]

    def technical_signals_from_scorecards(
        self,
        scorecards: List[Any],
        top_n: int = 50,
    ) -> List[TechnicalSignal]:
        """Build technical signals from Stage 1 scorecards to avoid duplicate data pulls."""
        cfg = self._numeric_filter_settings()
        rows: List[Dict[str, Any]] = []
        for sc in scorecards or []:
            try:
                row = {
                    "ticker": str(getattr(sc, "ticker", "")).strip().upper(),
                    "price": float(getattr(sc, "price", 0.0)),
                    "momentum_20d": float(getattr(sc, "roc_20d", 0.0)),
                    "roc_20d": float(getattr(sc, "roc_20d", 0.0)),
                    "rs_vs_spy_20d": float(getattr(sc, "rs_vs_spy_20d", 0.0)),
                    "relative_strength_vs_spy": round(1.0 + (float(getattr(sc, "rs_vs_spy_20d", 0.0)) / 100.0), 4),
                    "vs_sma50_pct": float(getattr(sc, "vs_sma50_pct", 0.0)),
                    "vs_sma200_pct": float(getattr(sc, "vs_sma200_pct", 0.0)),
                    "adx": float(getattr(sc, "adx", 0.0)),
                    "volume_ratio": float(getattr(sc, "volume_ratio", 0.0)),
                    "avg_volume_20d": float(getattr(sc, "avg_dollar_volume_20d", 0.0)),
                    "bollinger_pct_b": float(getattr(sc, "bollinger_pct_b", 0.5)),
                    "obv_slope_10d": float(getattr(sc, "obv_slope_10d", 0.0)),
                }
            except Exception:
                continue
            if not row["ticker"]:
                continue
            gate_passed, gate_fail_reasons = self._apply_hard_gates(row)
            row["gate_passed"] = gate_passed
            row["gate_fail_reasons"] = gate_fail_reasons
            row["composite_score"] = self._compute_weighted_score(row, cfg)
            rows.append(row)

        passed = [r for r in rows if r["gate_passed"]]
        passed.sort(key=lambda x: x["composite_score"], reverse=True)
        return [
            TechnicalSignal(
                ticker=r["ticker"],
                price=r["price"],
                vs_sma50_pct=r["vs_sma50_pct"],
                vs_sma200_pct=r["vs_sma200_pct"],
                momentum_20d=r["momentum_20d"],
                adx=r["adx"],
                obv_trend="neutral",
                relative_strength_vs_spy=r["relative_strength_vs_spy"],
                volume_ratio=r["volume_ratio"],
                composite_score=r["composite_score"],
                roc_20d=r["roc_20d"],
                rs_vs_spy_20d=r["rs_vs_spy_20d"],
                bollinger_pct_b=r["bollinger_pct_b"],
                obv_slope_10d=r["obv_slope_10d"],
                avg_volume_20d=r["avg_volume_20d"],
                gate_passed=r["gate_passed"],
                gate_fail_reasons=r["gate_fail_reasons"],
            )
            for r in passed[:top_n]
        ]

    def _fetch_ticker_technicals(self, ticker: str, trade_date: str) -> Optional[Dict[str, Any]]:
        from verumtrade.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_30d = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")
        try:
            raw_csv = route_to_vendor("get_stock_data", ticker, start_30d, trade_date)
            prices, volumes = parse_price_volume_csv(str(raw_csv or ""))
            if len(prices) < 10:
                return None

            current_price = prices[-1]
            momentum_20d_raw = compute_return_pct(prices, 20)
            if momentum_20d_raw is None:
                return None
            momentum_20d = float(momentum_20d_raw)

            volume_ratio = 0.0
            if volumes and len(volumes) >= 20:
                avg_vol_20d = sum(volumes[-20:]) / 20
                avg_vol_5d = sum(volumes[-5:]) / 5
                if avg_vol_20d > 0:
                    volume_ratio = avg_vol_5d / avg_vol_20d

            sma50_val = None
            try:
                sma50_val = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "close_50_sma", trade_date, 3))
            except Exception:
                pass
            sma200_val = None
            try:
                sma200_val = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "close_200_sma", trade_date, 3))
            except Exception:
                pass
            adx_val = 0.0
            try:
                adx_val = self._extract_indicator_value(route_to_vendor("get_indicators", ticker, "adx", trade_date, 3)) or 0.0
            except Exception:
                pass

            vs_sma50 = ((current_price - sma50_val) / sma50_val) * 100 if sma50_val and sma50_val > 0 else 0.0
            vs_sma200 = ((current_price - sma200_val) / sma200_val) * 100 if sma200_val and sma200_val > 0 else 0.0
            return {
                "ticker": ticker,
                "price": round(current_price, 2),
                "vs_sma50_pct": round(vs_sma50, 2),
                "vs_sma200_pct": round(vs_sma200, 2),
                "momentum_20d": round(momentum_20d, 2),
                "adx": round(adx_val, 1),
                "volume_ratio": round(volume_ratio, 2),
                "prices_20d": prices[-20:],
                "volumes_20d": volumes[-20:] if len(volumes) >= 20 else volumes,
            }
        except Exception as e:
            self.logger.debug(f"Technical data fetch failed for {ticker}: {e}")
            return None

    def _extract_indicator_value(self, raw_text: str) -> Optional[float]:
        return extract_indicator_value(raw_text)

    def _fetch_spy_returns(self, trade_date: str) -> Tuple[float, float]:
        from verumtrade.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=35)).strftime("%Y-%m-%d")
        try:
            raw_csv = route_to_vendor("get_stock_data", "SPY", start, trade_date)
            prices, _ = parse_price_volume_csv(str(raw_csv or ""))
            if len(prices) < 10:
                return 0.0, 0.0
            spy_return_20d = compute_return_pct(prices, 20)
            if spy_return_20d is None:
                return 0.0, 0.0
            return prices[-1], float(spy_return_20d)
        except Exception:
            return 0.0, 0.0

    def scan(self, universe: List[str], trade_date: str, max_workers: int = 4) -> List[TechnicalSignal]:
        if not universe:
            return []

        _, spy_return_20d = self._fetch_spy_returns(trade_date)
        raw_technicals = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._fetch_ticker_technicals, ticker, trade_date): ticker for ticker in universe}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        if spy_return_20d != 0:
                            stock_ret = result["momentum_20d"]
                            if spy_return_20d > 0:
                                result["relative_strength_vs_spy"] = round(stock_ret / spy_return_20d, 2)
                            else:
                                result["relative_strength_vs_spy"] = round(1.0 + (stock_ret - spy_return_20d) / 100, 2)
                        else:
                            result["relative_strength_vs_spy"] = 1.0
                        raw_technicals.append(result)
                except Exception as e:
                    self.logger.debug(f"Data fetch failed for {ticker}: {e}")

        if not raw_technicals:
            self.logger.warning("No technical data fetched for any ticker")
            return []

        table = f"Technical Screening Data ({trade_date}):\n"
        table += f"SPY 20d Return: {spy_return_20d:+.2f}%\n\n"
        table += "| Ticker | Price | vs 50SMA | vs 200SMA | 20d Mom | ADX | Vol Ratio | RS vs SPY |\n"
        table += "|--------|-------|---------|----------|---------|-----|-----------|----------|\n"
        for t in raw_technicals:
            table += (
                f"| {t['ticker']} | ${t['price']:.2f} | {t['vs_sma50_pct']:+.1f}% | "
                f"{t['vs_sma200_pct']:+.1f}% | {t['momentum_20d']:+.1f}% | {t['adx']:.0f} | "
                f"{t['volume_ratio']:.2f} | {t['relative_strength_vs_spy']:.2f} |\n"
            )
        table += "\n\nRecent Price/Volume for OBV analysis:\n"
        for t in raw_technicals[:15]:
            p_str = ",".join(f"{p:.1f}" for p in t.get("prices_20d", [])[-10:])
            v_str = ",".join(f"{int(v)}" for v in t.get("volumes_20d", [])[-10:])
            table += f"{t['ticker']}: prices=[{p_str}] volumes=[{v_str}]\n"

        signals = None
        llm_scoring_enabled = bool(
            ((self.config.get("discovery") or {}).get("enable_legacy_llm_technical_scoring", False))
        )
        if llm_scoring_enabled and self.llm is not None:
            try:
                result = self.llm.invoke([SystemMessage(content=TECHNICAL_SCANNER_SYSTEM_PROMPT), HumanMessage(content=table)])
                content = result.content if hasattr(result, "content") else str(result)
                signals = self._parse_technical_response(content)
            except Exception as e:
                self.logger.warning(f"LLM technical scoring failed, using quant fallback: {e}")

        if not signals:
            signals = self._quant_fallback_scoring(raw_technicals)
        return sorted(signals, key=lambda s: s.composite_score, reverse=True)

    def _parse_technical_response(self, response_text: str) -> Optional[List[TechnicalSignal]]:
        data = parse_json_dict(response_text)
        if not data:
            return None
        signals_data = data.get("signals", [])
        if not signals_data:
            return None

        signals = []
        for s in signals_data:
            signals.append(
                TechnicalSignal(
                    ticker=s.get("ticker", ""),
                    price=float(s.get("price", 0)),
                    vs_sma50_pct=float(s.get("vs_sma50_pct", 0)),
                    vs_sma200_pct=float(s.get("vs_sma200_pct", 0)),
                    momentum_20d=float(s.get("momentum_20d", 0)),
                    adx=float(s.get("adx", 0)),
                    obv_trend=s.get("obv_trend", "neutral"),
                    relative_strength_vs_spy=float(s.get("relative_strength_vs_spy", 0)),
                    volume_ratio=float(s.get("volume_ratio", 0)),
                    composite_score=float(s.get("composite_score", 0)),
                )
            )
        return signals if signals else None

    def _quant_fallback_scoring(self, raw_technicals: List[Dict]) -> List[TechnicalSignal]:
        signals = []
        for t in raw_technicals:
            score = 0.0
            if t["vs_sma50_pct"] > 0 and t["vs_sma200_pct"] > 0:
                score += 25
            if t["adx"] > 40:
                score += 30
            elif t["adx"] > 25:
                score += 20
            if t["momentum_20d"] > 10:
                score += 20
            elif t["momentum_20d"] > 5:
                score += 15
            if t.get("relative_strength_vs_spy", 1.0) > 1.0:
                score += 15
            if t.get("volume_ratio", 0) > 1.2:
                score += 10
            signals.append(
                TechnicalSignal(
                    ticker=t["ticker"],
                    price=t["price"],
                    vs_sma50_pct=t["vs_sma50_pct"],
                    vs_sma200_pct=t["vs_sma200_pct"],
                    momentum_20d=t["momentum_20d"],
                    adx=t["adx"],
                    obv_trend="neutral",
                    relative_strength_vs_spy=t.get("relative_strength_vs_spy", 1.0),
                    volume_ratio=t.get("volume_ratio", 0),
                    composite_score=score,
                )
            )
        return signals
