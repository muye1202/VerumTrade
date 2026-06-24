from __future__ import annotations
"""
Track B - Anomaly Scans:
Purely technical scanner to flag specific quantitative phenomena based on calculated momentum metrics.
"""
# verumtrade/agents/discovery/intelligence/track_b_anomaly_scans.py
"""
Track B: Momentum Anomaly Scans.

Four pure-numeric scans that run against the liquid ticker universe daily.
All use daily OHLCV from Alpaca free tier — zero LLM calls.

Scan 1: Momentum Acceleration
Scan 2: Volatility Contraction Breakout
Scan 3: Relative Strength Divergence
Scan 4: Stealth Accumulation (Volume Anomaly Without Price Move)
"""


import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .pipeline_models import MomentumScanHit
from .candidate_scoring import SECTOR_ETF_MAP
from .pipeline_utils import (
    compute_obv_series,
    compute_return_pct,
    extract_indicator_value,
    linear_regression_slope,
    parse_price_volume_csv,
)

logger = logging.getLogger(__name__)
_ALLOWED_SCAN_NAMES = {
    "momentum_acceleration",
    "volatility_breakout",
    "rs_divergence",
    "stealth_accumulation",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roc(prices: List[float], period: int) -> Optional[float]:
    """Rate of change over *period* days (percent)."""
    return compute_return_pct(prices, period)


def _sma(values: List[float], period: int) -> Optional[float]:
    """Simple moving average of the last *period* values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _stddev(values: List[float], period: int) -> Optional[float]:
    """Population standard deviation of the last *period* values."""
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    return math.sqrt(variance)


def _bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0
                     ) -> Optional[Tuple[float, float, float]]:
    """Return (upper, middle, lower) Bollinger Bands."""
    middle = _sma(prices, period)
    sd = _stddev(prices, period)
    if middle is None or sd is None:
        return None
    return (middle + num_std * sd, middle, middle - num_std * sd)


def _bb_width(upper: float, lower: float, middle: float) -> float:
    if middle == 0:
        return 0.0
    return (upper - lower) / middle


def _obv_series(prices: List[float], volumes: List[float]) -> List[float]:
    """Compute OBV series from aligned price and volume lists."""
    return compute_obv_series(prices, volumes)


def _linear_regression_slope(values: List[float]) -> float:
    """Slope of a simple linear regression on *values* (index = x)."""
    return linear_regression_slope(values)


def _percentile_rank(value: float, history: List[float]) -> float:
    """Percentile rank of *value* within *history* (0–100)."""
    if not history:
        return 50.0
    count_below = sum(1 for v in history if v < value)
    return (count_below / len(history)) * 100.0


# ---------------------------------------------------------------------------
# Ticker data bundle — fetched once, reused across all scans
# ---------------------------------------------------------------------------

class _TickerData:
    """OHLCV + derived fields for one ticker."""
    __slots__ = ("ticker", "prices", "volumes", "sma50")

    def __init__(
        self,
        ticker: str,
        prices: List[float],
        volumes: List[float],
        sma50: Optional[float],
    ):
        self.ticker = ticker
        self.prices = prices
        self.volumes = volumes
        self.sma50 = sma50


# ---------------------------------------------------------------------------
# MomentumAnomalyScanner
# ---------------------------------------------------------------------------

class MomentumAnomalyScanner:
    """
    Track B scanner: four momentum anomaly scans (pure numeric, no LLM).

    Each scan receives pre-fetched OHLCV data per ticker and returns a list
    of MomentumScanHit for tickers that pass the trigger conditions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _settings(self) -> Dict[str, Any]:
        return {
            "enabled_scans": [
                "momentum_acceleration",
                "volatility_breakout",
                "rs_divergence",
                "stealth_accumulation",
            ],
            "thresholds": {
                "momentum_acceleration_min": 1.5,
                "momentum_acceleration_min_vol_ratio": 1.3,
                "breakout_max_bbw_percentile": 20.0,
                "breakout_min_volume_ratio": 1.5,
                "rs_divergence_top_quantile": 0.90,
                "rs_divergence_min_rs_stock_vs_spy": 0.0,
                "stealth_obv_slope_quantile": 0.95,
                "stealth_max_abs_roc_10d_pct": 2.0,
            },
        }

    def _effective_settings(self, policy_overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        out = self._settings()
        if not isinstance(policy_overrides, dict):
            return out

        enabled = policy_overrides.get("enabled_scans")
        if isinstance(enabled, list):
            clean = []
            for item in enabled:
                name = str(item).strip().lower()
                if name in _ALLOWED_SCAN_NAMES and name not in clean:
                    clean.append(name)
            if clean:
                out["enabled_scans"] = clean

        thresholds = policy_overrides.get("thresholds")
        if isinstance(thresholds, dict):
            merged = dict(out["thresholds"])
            for key in merged:
                if key not in thresholds:
                    continue
                try:
                    merged[key] = float(thresholds[key])
                except Exception:
                    continue
            out["thresholds"] = merged
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all_scans(
        self,
        universe: List[str],
        trade_date: str,
        max_workers: int = 6,
        ohlcv_cache: Optional[Dict[str, str]] = None,
        policy_overrides: Optional[Dict[str, Any]] = None,
    ) -> List[MomentumScanHit]:
        """
        Run all four momentum anomaly scans against *universe*.

        Args:
            ohlcv_cache: Optional shared cache of raw CSV strings keyed by
                ticker.  Populated by Stage 0 or other pipeline stages to
                avoid duplicate ``route_to_vendor('get_stock_data')`` calls.

        Returns a flat list of MomentumScanHit across all scans.
        """
        if not universe:
            return []

        cache = ohlcv_cache if ohlcv_cache is not None else {}
        cfg = self._effective_settings(policy_overrides)

        # 1. Fetch data for all tickers (+ SPY, sector ETFs) in parallel.
        ticker_data = self._fetch_universe_data(universe, trade_date, max_workers, cache)
        if not ticker_data:
            return []

        # Fetch SPY data for Scan 3.
        spy_data = self._fetch_single_ticker("SPY", trade_date, cache)

        # 2. Run each scan.
        hits: List[MomentumScanHit] = []
        enabled = set(cfg["enabled_scans"])
        if "momentum_acceleration" in enabled:
            hits.extend(self._scan1_momentum_acceleration(ticker_data, cfg["thresholds"]))
        if "volatility_breakout" in enabled:
            hits.extend(self._scan2_volatility_breakout(ticker_data, cfg["thresholds"]))
        if "rs_divergence" in enabled:
            hits.extend(self._scan3_rs_divergence(ticker_data, spy_data, trade_date, cfg["thresholds"], max_workers, cache))
        if "stealth_accumulation" in enabled:
            hits.extend(self._scan4_stealth_accumulation(ticker_data, cfg["thresholds"]))

        self.logger.info(
            f"Track B scans complete: {len(universe)} tickers → {len(hits)} hits "
            f"[accel={sum(1 for h in hits if h.scan_name == 'momentum_acceleration')}, "
            f"breakout={sum(1 for h in hits if h.scan_name == 'volatility_breakout')}, "
            f"rs_div={sum(1 for h in hits if h.scan_name == 'rs_divergence')}, "
            f"stealth={sum(1 for h in hits if h.scan_name == 'stealth_accumulation')}]"
        )
        return hits

    # ------------------------------------------------------------------
    # Scan 1: Momentum Acceleration
    # ------------------------------------------------------------------

    def _scan1_momentum_acceleration(
        self,
        ticker_data: Dict[str, _TickerData],
        thresholds: Dict[str, float],
    ) -> List[MomentumScanHit]:
        """
        Catches the 'quiet grind that suddenly accelerates' pattern.

        Signal: (ROC_5d / 5) / (ROC_20d / 20)
        Triggers when:
          - momentum_acceleration > 1.5
          - price > SMA_50
          - vol_5d / vol_20d > 1.3
        """
        hits: List[MomentumScanHit] = []
        for td in ticker_data.values():
            roc5 = _roc(td.prices, 5)
            roc20 = _roc(td.prices, 20)
            if roc5 is None or roc20 is None:
                continue

            # Avoid division by zero / near-zero denominator.
            daily_roc20 = roc20 / 20.0
            if abs(daily_roc20) < 0.001:
                continue

            daily_roc5 = roc5 / 5.0
            momentum_acc = daily_roc5 / daily_roc20

            # Gate: acceleration threshold.
            if momentum_acc <= float(thresholds["momentum_acceleration_min"]):
                continue

            # Gate: price above SMA 50.
            if td.sma50 is None or td.prices[-1] <= td.sma50:
                continue

            # Gate: volume confirming.
            if len(td.volumes) < 20:
                continue
            avg_vol_5d = sum(td.volumes[-5:]) / 5.0
            avg_vol_20d = sum(td.volumes[-20:]) / 20.0
            if avg_vol_20d == 0:
                continue
            vol_ratio = avg_vol_5d / avg_vol_20d
            if vol_ratio <= float(thresholds["momentum_acceleration_min_vol_ratio"]):
                continue

            min_acc = float(thresholds["momentum_acceleration_min"])
            strength = ((momentum_acc - min_acc) / max(0.1, 4.0 - min_acc)) * 100.0
            strength = max(0.0, min(100.0, strength))
            hits.append(MomentumScanHit(
                ticker=td.ticker,
                scan_name="momentum_acceleration",
                signal_value=round(momentum_acc, 4),
                raw_value=round(momentum_acc, 4),
                normalized_strength=round(strength, 2),
                direction="up",
                trigger_details={
                    "roc_5d": round(roc5, 4),
                    "roc_20d": round(roc20, 4),
                    "momentum_acceleration": round(momentum_acc, 4),
                    "vol_ratio_5d_20d": round(vol_ratio, 4),
                    "price": round(td.prices[-1], 2),
                    "sma50": round(td.sma50, 2),
                },
            ))
        return hits

    # ------------------------------------------------------------------
    # Scan 2: Volatility Contraction Breakout
    # ------------------------------------------------------------------

    def _scan2_volatility_breakout(
        self,
        ticker_data: Dict[str, _TickerData],
        thresholds: Dict[str, float],
    ) -> List[MomentumScanHit]:
        """
        Catches the 'coiled spring' setup — tight consolidation then breakout.

        Signal:
          bb_width at low percentile vs last 60 days,
          close > upper band on volume.
        Trigger:
          bb_width_percentile < 20 AND close > upper_band
          AND volume > 1.5 * avg_volume_20d
        """
        hits: List[MomentumScanHit] = []
        for td in ticker_data.values():
            # Need at least 60 + 20 = 80 data points to compute 60-day
            # BB width history (each needing 20 days of lookback).
            if len(td.prices) < 80 or len(td.volumes) < 20:
                continue

            # Compute current Bollinger Bands.
            bb = _bollinger_bands(td.prices, period=20, num_std=2.0)
            if bb is None:
                continue
            upper, middle, lower = bb
            current_bbw = _bb_width(upper, lower, middle)

            # Compute trailing 60-day BB width history for percentile ranking.
            bbw_history: List[float] = []
            for offset in range(1, 61):
                end_idx = len(td.prices) - offset
                if end_idx < 20:
                    break
                slice_prices = td.prices[:end_idx]
                hist_bb = _bollinger_bands(slice_prices, period=20, num_std=2.0)
                if hist_bb is not None:
                    u, m, l = hist_bb
                    bbw_history.append(_bb_width(u, l, m))

            if not bbw_history:
                continue

            pct_rank = _percentile_rank(current_bbw, bbw_history)

            # Gate: BB width must be in bottom 20th percentile.
            if pct_rank >= float(thresholds["breakout_max_bbw_percentile"]):
                continue

            # Gate: price breaking above upper band.
            close = td.prices[-1]
            if close <= upper:
                continue

            # Gate: volume confirmation.
            avg_vol_20d = sum(td.volumes[-20:]) / 20.0
            if avg_vol_20d == 0:
                continue
            current_vol = td.volumes[-1]
            if current_vol <= float(thresholds["breakout_min_volume_ratio"]) * avg_vol_20d:
                continue

            breakout_strength = max(0.0, min(100.0, 100.0 - float(pct_rank)))
            hits.append(MomentumScanHit(
                ticker=td.ticker,
                scan_name="volatility_breakout",
                signal_value=round(breakout_strength, 2),
                raw_value=round(pct_rank, 2),
                normalized_strength=round(breakout_strength, 2),
                direction="up",
                trigger_details={
                    "bb_width": round(current_bbw, 6),
                    "bb_width_percentile": round(pct_rank, 2),
                    "breakout_strength": round(breakout_strength, 2),
                    "close": round(close, 2),
                    "upper_band": round(upper, 2),
                    "volume": round(current_vol, 0),
                    "avg_volume_20d": round(avg_vol_20d, 0),
                },
            ))
        return hits

    # ------------------------------------------------------------------
    # Scan 3: Relative Strength Divergence
    # ------------------------------------------------------------------

    def _scan3_rs_divergence(
        self,
        ticker_data: Dict[str, _TickerData],
        spy_data: Optional[_TickerData],
        trade_date: str,
        thresholds: Dict[str, float],
        max_workers: int = 4,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> List[MomentumScanHit]:
        """
        Catches early sector rotation — stock RS rising vs SPY while
        its sector ETF RS is flat or falling.

        Signal:
          divergence = rs_stock_vs_spy - rs_sector_vs_spy
        Trigger:
          divergence in top decile of universe AND rs_stock_vs_spy > 0.
        """
        if spy_data is None or len(spy_data.prices) < 11:
            return []

        spy_roc_10d = _roc(spy_data.prices, 10)
        if spy_roc_10d is None:
            return []

        # Map tickers to their sector ETF.
        sector_map = self._resolve_sector_etfs(
            list(ticker_data.keys()), max_workers=max_workers,
        )

        # Pre-fetch unique sector ETF OHLCV data.
        unique_etfs = set(sector_map.values())
        etf_roc_cache: Dict[str, float] = {}
        cache = ohlcv_cache if ohlcv_cache is not None else {}
        for etf in unique_etfs:
            etf_data = self._fetch_single_ticker(etf, trade_date, cache)
            if etf_data and len(etf_data.prices) >= 11:
                r = _roc(etf_data.prices, 10)
                if r is not None:
                    etf_roc_cache[etf] = r

        # Compute divergence per ticker.
        divergences: List[Tuple[str, float, float]] = []  # (ticker, divergence, rs_stock_vs_spy)
        for ticker, td in ticker_data.items():
            if len(td.prices) < 11:
                continue
            stock_roc_10d = _roc(td.prices, 10)
            if stock_roc_10d is None:
                continue

            rs_stock_vs_spy = stock_roc_10d - spy_roc_10d

            etf = sector_map.get(ticker)
            if etf is None or etf not in etf_roc_cache:
                continue
            rs_sector_vs_spy = etf_roc_cache[etf] - spy_roc_10d
            divergence = rs_stock_vs_spy - rs_sector_vs_spy
            divergences.append((ticker, divergence, rs_stock_vs_spy))

        if not divergences:
            return []

        # Top decile threshold.
        sorted_divs = sorted([d for _, d, _ in divergences])
        q = min(0.99, max(0.5, float(thresholds["rs_divergence_top_quantile"])))
        q_idx = int(len(sorted_divs) * q)
        q_idx = max(0, min(q_idx, len(sorted_divs) - 1))
        top_decile_threshold = sorted_divs[q_idx] if sorted_divs else 0.0

        max_divergence = max(d for _, d, _ in divergences) if divergences else top_decile_threshold
        hits: List[MomentumScanHit] = []
        for ticker, divergence, rs_stock_vs_spy in divergences:
            if divergence < top_decile_threshold:
                continue
            if rs_stock_vs_spy <= float(thresholds["rs_divergence_min_rs_stock_vs_spy"]):
                continue
            span = max(1e-6, float(max_divergence - top_decile_threshold))
            strength = ((float(divergence) - float(top_decile_threshold)) / span) * 100.0
            strength = max(0.0, min(100.0, strength))
            hits.append(MomentumScanHit(
                ticker=ticker,
                scan_name="rs_divergence",
                signal_value=round(divergence, 4),
                raw_value=round(divergence, 4),
                normalized_strength=round(strength, 2),
                direction="up",
                trigger_details={
                    "rs_stock_vs_spy": round(rs_stock_vs_spy, 4),
                    "divergence": round(divergence, 4),
                },
            ))
        return hits

    # ------------------------------------------------------------------
    # Scan 4: Stealth Accumulation
    # ------------------------------------------------------------------

    def _scan4_stealth_accumulation(
        self,
        ticker_data: Dict[str, _TickerData],
        thresholds: Dict[str, float],
    ) -> List[MomentumScanHit]:
        """
        Catches institutional accumulation *before* the price moves.

        Signal:
          OBV trending up (positive slope) while price is flat.
        Trigger:
          obv_slope > 95th-percentile of universe AND abs(ROC_10d) < 2%.
        """
        # First pass: compute OBV slopes for all tickers to determine
        # adaptive threshold (95th percentile).
        slopes: Dict[str, float] = {}
        for ticker, td in ticker_data.items():
            if len(td.prices) < 10 or len(td.volumes) < 10:
                continue
            obv = _obv_series(td.prices[-10:], td.volumes[-10:])
            slope = _linear_regression_slope(obv)
            slopes[ticker] = slope

        if not slopes:
            return []

        # 95th percentile of positive slopes as threshold.
        sorted_slopes = sorted(slopes.values())
        q = min(0.99, max(0.5, float(thresholds["stealth_obv_slope_quantile"])))
        idx_95 = int(len(sorted_slopes) * q)
        threshold = sorted_slopes[min(idx_95, len(sorted_slopes) - 1)]

        # Only meaningful if threshold is positive (accumulation).
        if threshold <= 0:
            return []

        hits: List[MomentumScanHit] = []
        for ticker, slope in slopes.items():
            if slope <= threshold:
                continue
            td = ticker_data[ticker]
            roc_10d = _roc(td.prices, 10)
            if roc_10d is None:
                continue
            if abs(roc_10d) >= float(thresholds["stealth_max_abs_roc_10d_pct"]):
                continue

            ratio = float(slope) / float(threshold) if threshold > 0 else 1.0
            strength = max(0.0, min(100.0, (ratio - 1.0) * 100.0))
            hits.append(MomentumScanHit(
                ticker=ticker,
                scan_name="stealth_accumulation",
                signal_value=round(slope, 4),
                raw_value=round(slope, 4),
                normalized_strength=round(strength, 2),
                direction="up",
                trigger_details={
                    "obv_slope_10d": round(slope, 4),
                    "price_change_10d_pct": round(roc_10d, 4),
                    "obv_slope_threshold": round(threshold, 4),
                },
            ))
        return hits

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    def _fetch_universe_data(
        self,
        universe: List[str],
        trade_date: str,
        max_workers: int = 6,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> Dict[str, _TickerData]:
        """Fetch OHLCV + SMA50 for all tickers in parallel."""
        cache = ohlcv_cache if ohlcv_cache is not None else {}
        result: Dict[str, _TickerData] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._fetch_single_ticker, ticker, trade_date, cache): ticker
                for ticker in universe
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    td = future.result()
                    if td is not None:
                        result[ticker] = td
                except Exception as e:
                    self.logger.debug(f"Failed to fetch data for {ticker}: {e}")
        return result

    def _fetch_single_ticker(
        self,
        ticker: str,
        trade_date: str,
        ohlcv_cache: Optional[Dict[str, str]] = None,
    ) -> Optional[_TickerData]:
        """Fetch OHLCV + SMA50 for one ticker.

        Uses *ohlcv_cache* (ticker → raw CSV) when available to avoid
        duplicate ``route_to_vendor('get_stock_data')`` calls.  On a cache
        miss the fetched CSV is stored into *ohlcv_cache* so other
        consumers (e.g. Stage 1) can benefit.
        """
        from verumtrade.dataflows.interface import route_to_vendor

        cache = ohlcv_cache if ohlcv_cache is not None else {}
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=380)).strftime("%Y-%m-%d")
        try:
            raw_csv = cache.get(ticker)
            if raw_csv is None:
                raw_csv = route_to_vendor("get_stock_data", ticker, start_date, trade_date)
                cache[ticker] = raw_csv  # populate for other consumers

            prices, volumes = parse_price_volume_csv(raw_csv)
            if len(prices) < 20 or len(volumes) < 20:
                return None

            # Fetch SMA50 for Scan 1 gate.
            sma50: Optional[float] = None
            try:
                raw_sma = route_to_vendor("get_indicators", ticker, "close_50_sma", trade_date, 3)
                sma50 = extract_indicator_value(raw_sma)
            except Exception:
                pass

            return _TickerData(
                ticker=ticker,
                prices=prices,
                volumes=volumes,
                sma50=sma50,
            )
        except Exception as e:
            self.logger.debug(f"Data fetch failed for {ticker}: {e}")
            return None

    def _resolve_sector_etfs(
        self,
        tickers: List[str],
        max_workers: int = 4,
    ) -> Dict[str, str]:
        """Map tickers to their sector ETF symbol via yfinance."""
        result: Dict[str, str] = {}

        def _lookup(ticker: str) -> Tuple[str, Optional[str]]:
            try:
                import yfinance as yf
                info = yf.Ticker(ticker).info or {}
                sector = info.get("sector", "")
                etf = SECTOR_ETF_MAP.get(sector)
                return ticker, etf
            except Exception:
                return ticker, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_lookup, t) for t in tickers]
            for future in as_completed(futures):
                ticker, etf = future.result()
                if etf:
                    result[ticker] = etf
        return result
