import threading
from contextlib import contextmanager
from typing import Dict, Tuple


_LOCK = threading.Lock()
_SEMAPHORES: Dict[str, Tuple[threading.Semaphore, int]] = {}


def get_semaphore(key: str, max_concurrency: int) -> threading.Semaphore:
    """Return a process-global semaphore for a given key."""
    if not key:
        raise ValueError("llm concurrency key must be non-empty")
    if int(max_concurrency) <= 0:
        raise ValueError("max_concurrency must be >= 1")

    with _LOCK:
        existing = _SEMAPHORES.get(key)
        if existing is not None:
            sem, _limit = existing
            return sem

        sem = threading.Semaphore(int(max_concurrency))
        _SEMAPHORES[key] = (sem, int(max_concurrency))
        return sem


@contextmanager
def llm_inflight_slot(key: str, max_concurrency: int = 1):
    """Block until a concurrency slot is available, then release it on exit."""
    sem = get_semaphore(key, int(max_concurrency))
    sem.acquire()
    try:
        yield
    finally:
        sem.release()
