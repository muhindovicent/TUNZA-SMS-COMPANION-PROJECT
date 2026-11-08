"""In-process sliding-window throttling for authentication attempts."""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime, timedelta

MAX_TRACKED_KEYS = 10000


class SlidingWindowRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self._lock = threading.Lock()
        self._failures: dict[str, deque[datetime]] = {}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _drop_expired(self, key: str, now: datetime) -> deque[datetime] | None:
        failures = self._failures.get(key)
        if failures is None:
            return None
        cutoff = now - self.window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
            return None
        return failures

    def retry_after(self, key: str) -> int | None:
        now = self._now()
        with self._lock:
            failures = self._drop_expired(key, now)
            if failures is None or len(failures) < self.max_attempts:
                return None
            remaining = (failures[0] + self.window - now).total_seconds()
            return max(1, int(remaining + 0.999))

    def record_failure(self, key: str) -> None:
        now = self._now()
        with self._lock:
            if len(self._failures) >= MAX_TRACKED_KEYS:
                self._failures = {
                    tracked: timestamps
                    for tracked, timestamps in self._failures.items()
                    if timestamps and timestamps[-1] > now - self.window
                }
            failures = self._drop_expired(key, now)
            if failures is None:
                failures = deque()
                self._failures[key] = failures
            failures.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
