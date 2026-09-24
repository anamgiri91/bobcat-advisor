"""
protection.py
=============
Per-client rate limiting and a small answer cache.

Both are in-process on purpose: the deployment is a single free-tier
instance, so Redis would add a service (and a failure mode) for no benefit.
The interfaces are narrow (allow(key), get/put) so moving to Redis when
scaling past one instance touches only this file.

Rate limit: token bucket per client IP. It protects the shared LLM key —
one script hammering /ask would otherwise exhaust the free-tier quota for
every user.

Cache: first-turn questions only (answers to follow-ups depend on the
conversation). Keyed on the normalised question + source filter, with a
TTL so re-indexed data shows up within the hour.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict

from ..config import settings


class RateLimiter:
    def __init__(self, per_minute: int):
        self.capacity = max(1, per_minute)
        self.refill_per_sec = self.capacity / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_seconds)"""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, (1 - tokens) / self.refill_per_sec

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class AnswerCache:
    def __init__(self, size: int, ttl_seconds: int = 3600):
        self.size, self.ttl = size, ttl_seconds
        self._data: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    @staticmethod
    def key(question: str, source_filter: str | None) -> tuple:
        return (" ".join(re.sub(r"[^a-z0-9 ]", "", question.lower()).split()), source_filter)

    def get(self, key: tuple) -> dict | None:
        with self._lock:
            item = self._data.get(key)
            if item and time.monotonic() - item[0] < self.ttl:
                self._data.move_to_end(key)
                self.hits += 1
                return item[1]
            self._data.pop(key, None)
            self.misses += 1
            return None

    def put(self, key: tuple, value: dict) -> None:
        if self.size <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)
answer_cache = AnswerCache(settings.ANSWER_CACHE_SIZE)


def client_key(request) -> str:
    """Client IP, honouring the proxy header Render/nginx set."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
