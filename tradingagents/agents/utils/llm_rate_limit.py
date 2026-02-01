import logging
import random
import threading
import time
from typing import Any


_log = logging.getLogger(__name__)

# Best-effort global throttling across threads in-process.
# Note: this is *event-triggered*; we only schedule delays after we observe a 429/rate-limit.
_NEXT_ALLOWED_TS: dict[str, float] = {}
_LOCK = threading.Lock()


def _is_rate_limit_error(err: BaseException) -> bool:
    # OpenAI SDK error types (direct or via LangChain) commonly have status_code=429.
    status_code = getattr(err, "status_code", None) or getattr(getattr(err, "response", None), "status_code", None)
    if status_code == 429:
        return True

    name = err.__class__.__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True

    msg = str(err).lower()
    if "http 429" in msg or "429" in msg and "too many requests" in msg:
        return True

    return False


def throttle(key: str, min_interval_s: float) -> None:
    if not min_interval_s or min_interval_s <= 0:
        return
    now = time.monotonic()
    with _LOCK:
        next_allowed = _NEXT_ALLOWED_TS.get(key)
        wait_s = (float(next_allowed) - now) if next_allowed is not None else 0.0

    if wait_s > 0:
        time.sleep(wait_s)


def mark_rate_limited(key: str, cooldown_s: float) -> None:
    """After observing a 429, delay subsequent calls for this key for `cooldown_s` seconds."""
    if not cooldown_s or cooldown_s <= 0:
        return
    with _LOCK:
        _NEXT_ALLOWED_TS[key] = time.monotonic() + float(cooldown_s)


def invoke_with_backoff(
    llm: Any,
    prompt: Any,
    *,
    key: str,
    min_interval_s: float = 0.0,
    max_retries: int = 6,
    base_backoff_s: float = 1.0,
    max_backoff_s: float = 30.0,
) -> Any:
    """
    Invoke an LLM with:
      - event-triggered cooldown: only enforced after we observe a 429 (per `key`)
      - exponential backoff + jitter on HTTP 429 / rate-limit errors
    """
    attempt = 0
    while True:
        try:
            return llm.invoke(prompt)
        except Exception as e:
            if not _is_rate_limit_error(e) or attempt >= int(max_retries):
                raise

            backoff = min(float(max_backoff_s), float(base_backoff_s) * (2 ** attempt))
            jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
            sleep_s = max(float(min_interval_s or 0.0), backoff + jitter)
            attempt += 1

            # Only start throttling after we see a 429.
            mark_rate_limited(key, float(min_interval_s or 0.0))

            _log.warning(
                "Rate limited for key=%s; retrying in %.2fs (attempt %s/%s): %s",
                key,
                sleep_s,
                attempt,
                max_retries,
                e,
            )
            throttle(key, float(min_interval_s or 0.0))
            time.sleep(sleep_s)
