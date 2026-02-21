from __future__ import annotations
"""
Market Policy LLM:
Uses an LLM to ingest the market context snapshot and dynamically generate a risk policy adjusting pipeline thresholds and sector weights.
"""

import json
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .pipeline_cache import load_cache_value, save_cache_value, stable_key
from .pipeline_utils import parse_json_dict

_SCHEMA_VERSION = "1.0"
_PROMPT_VERSION = "policy_v1"

_ALLOWED_TRACKS = {"enricher", "anomaly_scan", "dual_track"}
_ALLOWED_REGIMES = {"TRENDING", "MEAN_REVERTING", "RANGE", "NEUTRAL"}
_ALLOWED_RISK = {"RISK_ON", "RISK_OFF", "NEUTRAL"}
_ALLOWED_SCAN_NAMES = {
    "momentum_acceleration",
    "volatility_breakout",
    "rs_divergence",
    "stealth_accumulation",
}
_ALLOWED_SECTOR_ETFS = {
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
}

_SYSTEM_PROMPT = """You are a market-regime policy engine for a stock discovery pipeline.

Task:
- Read the structured pre-stage snapshot.
- Return JSON only.
- Produce a machine-readable policy object, not prose.
- Keep choices bounded and conservative.

Required output contract:
{
  "schema_version": "1.0",
  "regime_label": "TRENDING|MEAN_REVERTING|RANGE|NEUTRAL",
  "risk_posture": "RISK_ON|RISK_OFF|NEUTRAL",
  "preferred_tracks": ["enricher|anomaly_scan|dual_track"],
  "policy": {
    "universe": {
      "stage0_overrides": {
        "min_avg_dollar_volume_20d": 10000000,
        "catalyst_mode": "daily_calendar|per_ticker_calendar",
        "catalyst_window_days": 7
      },
      "sector_weights": {
        "XLK": 1.0,
        "XLF": 1.0,
        "XLE": 1.0,
        "XLV": 1.0,
        "XLI": 1.0,
        "XLY": 1.0,
        "XLP": 1.0,
        "XLU": 1.0,
        "XLB": 1.0,
        "XLRE": 1.0,
        "XLC": 1.0
      },
      "allocation": {
        "max_tickers": {
          "enricher": 250,
          "anomaly_scan": 250,
          "dual_track_total": 300
        },
        "dual_track_split": {
          "enricher": 0.5,
          "anomaly_scan": 0.5
        }
      }
    },
    "scoring": {
      "stage2_weight_tilts": {
        "technical_momentum": 1.0,
        "options_flow": 1.0,
        "sector_momentum": 1.0,
        "short_squeeze": 1.0
      },
      "stage2_hard_filter_overrides": {
        "require_above_sma50": true,
        "min_rs_vs_spy_differential": -5.0,
        "min_avg_dollar_volume_20d": 5000000,
        "max_gap_down_pct": -5.0
      }
    },
    "anomaly_scan": {
      "enabled_scans": ["momentum_acceleration", "volatility_breakout", "rs_divergence", "stealth_accumulation"],
      "thresholds": {
        "momentum_acceleration_min": 1.5,
        "momentum_acceleration_min_vol_ratio": 1.3,
        "breakout_max_bbw_percentile": 20.0,
        "breakout_min_volume_ratio": 1.5,
        "rs_divergence_top_quantile": 0.90,
        "rs_divergence_min_rs_stock_vs_spy": 0.0,
        "stealth_obv_slope_quantile": 0.95,
        "stealth_max_abs_roc_10d_pct": 2.0
      }
    }
  },
  "scan_notes": "short rationale"
}

Rules:
- Sector multipliers and stage2 weight multipliers must be between 0.5 and 1.5.
- preferred_tracks can include 1-3 values but only from allowed set.
- If uncertain, choose neutral values.
"""


def _neutral_policy() -> Dict[str, Any]:
    return {
        "universe": {
            "stage0_overrides": {},
            "sector_weights": {etf: 1.0 for etf in sorted(_ALLOWED_SECTOR_ETFS)},
            "allocation": {
                "max_tickers": {
                    "enricher": 250,
                    "anomaly_scan": 250,
                    "dual_track_total": 300,
                },
                "dual_track_split": {
                    "enricher": 0.5,
                    "anomaly_scan": 0.5,
                },
            },
        },
        "scoring": {
            "stage2_weight_tilts": {
                "technical_momentum": 1.0,
                "options_flow": 1.0,
                "sector_momentum": 1.0,
                "short_squeeze": 1.0,
            },
            "stage2_hard_filter_overrides": {},
        },
        "anomaly_scan": {
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
        },
    }


def _neutral_bias() -> Dict[str, Any]:
    policy = _neutral_policy()
    out = {
        "schema_version": _SCHEMA_VERSION,
        "regime_label": "NEUTRAL",
        "risk_posture": "NEUTRAL",
        "preferred_tracks": ["enricher", "anomaly_scan", "dual_track"],
        "policy": policy,
        "scan_notes": "Neutral fallback profile.",
    }
    return _apply_legacy_compat(out)


def _as_dict(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except Exception:
        return default
    return max(lo, min(hi, f))


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        i = int(v)
    except Exception:
        return default
    return max(lo, min(hi, i))


def _normalize_catalyst_mode(value: Any, default: str = "daily_calendar") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    # Trim descriptive suffixes like "(recommended default)" and normalize separators.
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


def _legacy_to_policy(data: Dict[str, Any]) -> Dict[str, Any]:
    policy = _neutral_policy()
    s0 = data.get("stage0_overrides")
    if isinstance(s0, dict):
        policy["universe"]["stage0_overrides"] = dict(s0)

    tilts = data.get("stage2_weight_tilts")
    if isinstance(tilts, dict):
        policy["scoring"]["stage2_weight_tilts"] = dict(tilts)
    return policy


def _sanitize_policy(policy_raw: Dict[str, Any]) -> Dict[str, Any]:
    out = _neutral_policy()

    universe = _as_dict(policy_raw.get("universe"), "policy.universe")
    stage0 = _as_dict(universe.get("stage0_overrides", {}), "policy.universe.stage0_overrides")
    clean_stage0: Dict[str, Any] = {}
    if "min_avg_dollar_volume_20d" in stage0:
        clean_stage0["min_avg_dollar_volume_20d"] = _clamp_int(
            stage0.get("min_avg_dollar_volume_20d"), 1_000_000, 30_000_000, 10_000_000,
        )
    if "catalyst_mode" in stage0:
        mode = _normalize_catalyst_mode(stage0.get("catalyst_mode"))
        clean_stage0["catalyst_mode"] = mode
    if "catalyst_window_days" in stage0:
        clean_stage0["catalyst_window_days"] = _clamp_int(
            stage0.get("catalyst_window_days"), 1, 21, 7,
        )
    out["universe"]["stage0_overrides"] = clean_stage0

    sector_weights = _as_dict(universe.get("sector_weights"), "policy.universe.sector_weights")
    clean_sector_weights = {etf: 1.0 for etf in sorted(_ALLOWED_SECTOR_ETFS)}
    for key, value in sector_weights.items():
        etf = str(key).strip().upper()
        if etf not in _ALLOWED_SECTOR_ETFS:
            raise ValueError(f"policy.universe.sector_weights.{etf} unsupported")
        clean_sector_weights[etf] = _clamp_float(value, 0.5, 1.5, 1.0)
    out["universe"]["sector_weights"] = clean_sector_weights

    allocation = _as_dict(universe.get("allocation"), "policy.universe.allocation")
    max_tickers = _as_dict(allocation.get("max_tickers"), "policy.universe.allocation.max_tickers")
    out["universe"]["allocation"]["max_tickers"] = {
        "enricher": _clamp_int(max_tickers.get("enricher"), 20, 1500, 250),
        "anomaly_scan": _clamp_int(max_tickers.get("anomaly_scan"), 20, 1500, 250),
        "dual_track_total": _clamp_int(max_tickers.get("dual_track_total"), 20, 2000, 300),
    }
    split = _as_dict(allocation.get("dual_track_split"), "policy.universe.allocation.dual_track_split")
    split_e = _clamp_float(split.get("enricher"), 0.1, 0.9, 0.5)
    split_a = _clamp_float(split.get("anomaly_scan"), 0.1, 0.9, 0.5)
    total = split_e + split_a
    if total <= 0:
        split_e = 0.5
        split_a = 0.5
        total = 1.0
    out["universe"]["allocation"]["dual_track_split"] = {
        "enricher": round(split_e / total, 4),
        "anomaly_scan": round(split_a / total, 4),
    }

    scoring = _as_dict(policy_raw.get("scoring"), "policy.scoring")
    tilts = _as_dict(scoring.get("stage2_weight_tilts"), "policy.scoring.stage2_weight_tilts")
    out["scoring"]["stage2_weight_tilts"] = {
        "technical_momentum": _clamp_float(tilts.get("technical_momentum"), 0.5, 1.5, 1.0),
        "options_flow": _clamp_float(tilts.get("options_flow"), 0.5, 1.5, 1.0),
        "sector_momentum": _clamp_float(tilts.get("sector_momentum"), 0.5, 1.5, 1.0),
        "short_squeeze": _clamp_float(tilts.get("short_squeeze"), 0.5, 1.5, 1.0),
    }

    hard = _as_dict(scoring.get("stage2_hard_filter_overrides", {}), "policy.scoring.stage2_hard_filter_overrides")
    hard_clean: Dict[str, Any] = {}
    if "require_above_sma50" in hard:
        hard_clean["require_above_sma50"] = bool(hard.get("require_above_sma50"))
    if "min_rs_vs_spy_differential" in hard:
        hard_clean["min_rs_vs_spy_differential"] = _clamp_float(
            hard.get("min_rs_vs_spy_differential"), -30.0, 20.0, -5.0,
        )
    if "min_avg_dollar_volume_20d" in hard:
        hard_clean["min_avg_dollar_volume_20d"] = _clamp_float(
            hard.get("min_avg_dollar_volume_20d"), 1_000_000.0, 30_000_000.0, 5_000_000.0,
        )
    if "max_gap_down_pct" in hard:
        hard_clean["max_gap_down_pct"] = _clamp_float(hard.get("max_gap_down_pct"), -20.0, 0.0, -5.0)
    out["scoring"]["stage2_hard_filter_overrides"] = hard_clean

    anomaly = _as_dict(policy_raw.get("anomaly_scan"), "policy.anomaly_scan")
    enabled_scans = anomaly.get("enabled_scans")
    if not isinstance(enabled_scans, list):
        raise ValueError("policy.anomaly_scan.enabled_scans must be a list")
    clean_scans = []
    for item in enabled_scans:
        name = str(item).strip().lower()
        if name not in _ALLOWED_SCAN_NAMES:
            raise ValueError(f"policy.anomaly_scan.enabled_scans contains invalid value: {name}")
        if name not in clean_scans:
            clean_scans.append(name)
    if not clean_scans:
        raise ValueError("policy.anomaly_scan.enabled_scans must not be empty")
    out["anomaly_scan"]["enabled_scans"] = clean_scans

    thresholds = _as_dict(anomaly.get("thresholds"), "policy.anomaly_scan.thresholds")
    out["anomaly_scan"]["thresholds"] = {
        "momentum_acceleration_min": _clamp_float(thresholds.get("momentum_acceleration_min"), 0.5, 4.0, 1.5),
        "momentum_acceleration_min_vol_ratio": _clamp_float(thresholds.get("momentum_acceleration_min_vol_ratio"), 0.5, 4.0, 1.3),
        "breakout_max_bbw_percentile": _clamp_float(thresholds.get("breakout_max_bbw_percentile"), 1.0, 60.0, 20.0),
        "breakout_min_volume_ratio": _clamp_float(thresholds.get("breakout_min_volume_ratio"), 0.5, 5.0, 1.5),
        "rs_divergence_top_quantile": _clamp_float(thresholds.get("rs_divergence_top_quantile"), 0.5, 0.99, 0.90),
        "rs_divergence_min_rs_stock_vs_spy": _clamp_float(thresholds.get("rs_divergence_min_rs_stock_vs_spy"), -10.0, 10.0, 0.0),
        "stealth_obv_slope_quantile": _clamp_float(thresholds.get("stealth_obv_slope_quantile"), 0.5, 0.99, 0.95),
        "stealth_max_abs_roc_10d_pct": _clamp_float(thresholds.get("stealth_max_abs_roc_10d_pct"), 0.5, 10.0, 2.0),
    }
    return out


def _apply_policy_rules(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    policy = out["policy"]
    stage0 = policy["universe"]["stage0_overrides"]
    tilts = policy["scoring"]["stage2_weight_tilts"]

    if out["regime_label"] == "TRENDING":
        tilts["technical_momentum"] = _clamp_float(tilts.get("technical_momentum"), 1.1, 1.5, 1.2)
        tilts["sector_momentum"] = _clamp_float(tilts.get("sector_momentum"), 1.0, 1.5, 1.1)
        if "anomaly_scan" not in out["preferred_tracks"]:
            out["preferred_tracks"] = ["anomaly_scan", "dual_track", "enricher"]
    elif out["regime_label"] == "MEAN_REVERTING":
        tilts["technical_momentum"] = _clamp_float(tilts.get("technical_momentum"), 0.5, 0.95, 0.85)
        stage0.setdefault("min_avg_dollar_volume_20d", 12_000_000)

    if out["risk_posture"] == "RISK_OFF":
        stage0.setdefault("min_avg_dollar_volume_20d", 15_000_000)
        tilts["short_squeeze"] = _clamp_float(tilts.get("short_squeeze"), 0.5, 0.95, 0.8)
        tilts["sector_momentum"] = _clamp_float(tilts.get("sector_momentum"), 0.8, 1.3, 1.0)

    out["policy"] = policy
    return out


def _apply_legacy_compat(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    policy = out.get("policy") or _neutral_policy()
    stage0 = ((policy.get("universe") or {}).get("stage0_overrides") or {})
    tilts = ((policy.get("scoring") or {}).get("stage2_weight_tilts") or {})
    out["stage0_overrides"] = dict(stage0)
    out["stage2_weight_tilts"] = dict(tilts)
    out["schema_version"] = _SCHEMA_VERSION
    return out


def _sanitize_bias(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Stage-0 LLM output must be a JSON object")

    out = _neutral_bias()

    regime = str(data.get("regime_label", out["regime_label"]))
    regime = regime.strip().upper()
    if regime not in _ALLOWED_REGIMES:
        raise ValueError(f"regime_label must be one of {sorted(_ALLOWED_REGIMES)}")
    out["regime_label"] = regime

    risk = str(data.get("risk_posture", out["risk_posture"]))
    risk = risk.strip().upper()
    if risk not in _ALLOWED_RISK:
        raise ValueError(f"risk_posture must be one of {sorted(_ALLOWED_RISK)}")
    out["risk_posture"] = risk

    tracks = data.get("preferred_tracks", out["preferred_tracks"])
    if not isinstance(tracks, list):
        raise ValueError("preferred_tracks must be a list")
    clean_tracks = []
    for item in tracks:
        track = str(item).strip().lower()
        if track not in _ALLOWED_TRACKS:
            raise ValueError(f"preferred_tracks contains invalid value: {track}")
        if track not in clean_tracks:
            clean_tracks.append(track)
    if not clean_tracks:
        raise ValueError("preferred_tracks must include at least one track")
    out["preferred_tracks"] = clean_tracks[:3]

    policy_raw = data.get("policy")
    if not isinstance(policy_raw, dict):
        policy_raw = _legacy_to_policy(data)
    out["policy"] = _sanitize_policy(policy_raw)

    notes = str(data.get("scan_notes", out["scan_notes"])).strip()
    if notes:
        out["scan_notes"] = notes[:400]

    out = _apply_policy_rules(out)
    return _apply_legacy_compat(out)


def _invoke_policy_llm(llm: Any, prompt_payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(prompt_payload, ensure_ascii=True)),
    ]

    if hasattr(llm, "with_structured_output"):
        try:
            structured_llm = llm.with_structured_output(dict)
            result = structured_llm.invoke(messages)
            if isinstance(result, dict):
                return result
        except Exception:
            pass

    result = llm.invoke(messages)
    raw = result.content if hasattr(result, "content") else str(result)
    parsed = parse_json_dict(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Unable to parse JSON object from Stage-0 LLM output")
    return parsed


def build_llm_bias_profile(
    llm,
    trade_date: str,
    snapshot: Dict[str, Any],
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    allow_llm_call: bool = True,
) -> Dict[str, Any]:
    key = stable_key({
        "type": "pre_stage0_llm_bias",
        "schema_version": _SCHEMA_VERSION,
        "prompt_version": _PROMPT_VERSION,
        "trade_date": trade_date,
        "snapshot_fingerprint": stable_key({"snapshot": snapshot}),
    })
    cached, hit = load_cache_value(
        namespace="pre_stage0_llm_bias",
        key=key,
        cache_config=cache_config,
        metrics=metrics,
    )
    if hit and isinstance(cached, dict):
        return _sanitize_bias(cached)

    if llm is None or not bool(allow_llm_call):
        return _neutral_bias()

    prompt_payload = {
        "schema_version": _SCHEMA_VERSION,
        "trade_date": trade_date,
        "snapshot": snapshot,
    }
    if isinstance(metrics, dict):
        metrics["llm_calls"] = int(metrics.get("llm_calls", 0)) + 1

    try:
        parsed = _invoke_policy_llm(llm, prompt_payload)
        bias = _sanitize_bias(parsed)
    except Exception:
        return _neutral_bias()

    save_cache_value(
        namespace="pre_stage0_llm_bias",
        key=key,
        value=bias,
        cache_config=cache_config,
    )
    return bias
