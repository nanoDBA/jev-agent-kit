"""A per-process call cap and local rate budget (spec stories 67, 68).

Every outbound attempt, including retries, reserves one permit and its estimated tokens.
Exhaustion is a class-appropriate failure, not a wait past the deadline. These are local,
per-process limits set below the account limits; they do not prevent account-wide 429s.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateBudget:
    def __init__(
        self,
        *,
        max_calls: int = 100,
        requests_per_minute: int = 600,
        tokens_per_second: int = 50_000,  # half the documented 100K tokens per second (2026-09-30)
    ) -> None:
        self._lock = threading.Lock()
        self._max_calls = max_calls
        self._rpm = requests_per_minute
        self._tps = tokens_per_second
        self._calls_made = 0
        self._request_times: deque[float] = deque()
        self._token_events: deque[tuple[float, int]] = deque()

    def reserve(self, estimated_tokens: int) -> bool:
        """Reserve one call and its tokens. Return False if a limit would be exceeded."""
        now = time.monotonic()
        with self._lock:
            if self._calls_made >= self._max_calls:
                return False
            self._trim(now)
            if len(self._request_times) >= self._rpm:
                return False
            recent_tokens = sum(t for _, t in self._token_events)
            if estimated_tokens > self._tps:
                return False
            if recent_tokens + estimated_tokens > self._tps:
                return False
            self._calls_made += 1
            self._request_times.append(now)
            self._token_events.append((now, estimated_tokens))
            return True

    def _trim(self, now: float) -> None:
        while self._request_times and now - self._request_times[0] >= 60.0:
            self._request_times.popleft()
        while self._token_events and now - self._token_events[0][0] >= 1.0:
            self._token_events.popleft()

    @property
    def calls_made(self) -> int:
        with self._lock:
            return self._calls_made
