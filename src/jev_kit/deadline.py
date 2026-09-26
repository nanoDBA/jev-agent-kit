"""A cooperative, monotonic deadline (spec stories 65, 66).

Phase 1 cannot interrupt a blocked system call; this only lets cooperating code check the
remaining budget and refuse to start work that cannot finish. Enforce mode inside real host
hooks waits for the Phase 3 child-process watchdog.
"""

from __future__ import annotations

import time


class Deadline:
    def __init__(self, total_seconds: float, *, reserve_seconds: float = 0.0) -> None:
        if total_seconds <= 0:
            raise ValueError("total_seconds must be positive")
        self._start = time.monotonic()
        self._end = self._start + total_seconds
        self._reserve = max(0.0, reserve_seconds)

    def remaining(self) -> float:
        """Seconds left before the hard end."""
        return max(0.0, self._end - time.monotonic())

    def remaining_for_work(self) -> float:
        """Seconds left before the reserved tail (for example receipt writing)."""
        return max(0.0, self._end - self._reserve - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def elapsed(self) -> float:
        return time.monotonic() - self._start
