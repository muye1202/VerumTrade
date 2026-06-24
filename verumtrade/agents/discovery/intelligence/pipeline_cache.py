from __future__ import annotations
"""
Pipeline Cache:
Provides caching utilities to store intermediate pipeline results and minimize repetitive API calls.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from verumtrade.dataflows.config import get_config


_SOURCE_VERSION = "v1"
_MEMO: Dict[Tuple[str, str], Dict[str, Any]] = {}


def stage0_cache_defaults() -> Dict[str, Any]:
    cfg = get_config()
    base_dir = Path(cfg.get("data_cache_dir", "dataflows/data_cache"))
    return {
        "enabled": True,
        "ttl_hours": 24,
        "dir": str(base_dir / "discovery_stage0"),
        "force_refresh": False,
    }


def resolve_stage0_cache_config(cache_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = stage0_cache_defaults()
    override = cache_config or {}
    cache_dir = override.get("dir")
    if not cache_dir:
        cache_dir = defaults["dir"]
    return {
        "enabled": bool(override.get("enabled", defaults["enabled"])),
        "ttl_hours": int(override.get("ttl_hours", defaults["ttl_hours"])),
        "dir": str(cache_dir),
        "force_refresh": bool(override.get("force_refresh", defaults["force_refresh"])),
    }


def stable_key(payload: Dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _cache_file(cache_cfg: Dict[str, Any], namespace: str, key: str) -> Path:
    safe_ns = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in namespace)
    cache_dir = Path(cache_cfg["dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{safe_ns}-{key}.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return True
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except Exception:
        return True
    return dt <= _now_utc()


def _record(metrics: Optional[Dict[str, Any]], key: str, amount: int = 1) -> None:
    if isinstance(metrics, dict):
        metrics[key] = int(metrics.get(key, 0)) + int(amount)


def load_cache_value(
    namespace: str,
    key: str,
    cache_config: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], bool]:
    cfg = resolve_stage0_cache_config(cache_config)
    if not cfg["enabled"] or cfg["force_refresh"]:
        _record(metrics, "cache_misses")
        return None, False

    memo_key = (namespace, key)
    memo = _MEMO.get(memo_key)
    if isinstance(memo, dict) and not _is_expired(memo.get("expires_at")):
        _record(metrics, "cache_hits")
        return memo.get("value"), True

    try:
        path = _cache_file(cfg, namespace, key)
    except Exception:
        _record(metrics, "cache_misses")
        return None, False
    if not path.exists():
        _record(metrics, "cache_misses")
        return None, False

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_version") != _SOURCE_VERSION:
            _record(metrics, "cache_misses")
            return None, False
        if _is_expired(payload.get("expires_at")):
            _record(metrics, "cache_misses")
            return None, False
        value = payload.get("value")
        _MEMO[memo_key] = {
            "value": value,
            "expires_at": payload.get("expires_at"),
        }
        _record(metrics, "cache_hits")
        return value, True
    except Exception:
        _record(metrics, "cache_misses")
        return None, False


def save_cache_value(
    namespace: str,
    key: str,
    value: Any,
    cache_config: Optional[Dict[str, Any]] = None,
) -> None:
    cfg = resolve_stage0_cache_config(cache_config)
    if not cfg["enabled"]:
        return

    created_at = _now_utc()
    expires_at = created_at + timedelta(hours=max(1, int(cfg["ttl_hours"])))
    payload = {
        "source_version": _SOURCE_VERSION,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "value": value,
    }

    try:
        path = _cache_file(cfg, namespace, key)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=str(path.parent))
    except Exception:
        return
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
        try:
            os.replace(tmp_path, path)
        except Exception:
            try:
                path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
            except Exception:
                return
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    _MEMO[(namespace, key)] = {
        "value": value,
        "expires_at": payload["expires_at"],
    }
