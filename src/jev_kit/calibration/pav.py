"""Isotonic calibration by pool-adjacent-violators (Phase 4, decision E3).

Raw Jev probabilities rank well but are miscalibrated with ties everywhere (docs/research/04),
so a per-fingerprint monotone calibration maps a raw probability to a calibrated one. This is
the standard PAV algorithm: fit a non-decreasing step function to (probability, label) pairs by
weight. Stdlib only. Fitting a calibration is analysis tooling; it never promotes a threshold
or writes the registry (that is owner-gated enforce work).
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass


def _check(samples: Sequence[tuple[float, int]]) -> None:
    if not samples:
        raise ValueError("no samples")
    for prob, label in samples:
        if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob):
            raise ValueError("probability must be a finite number")
        if not 0.0 <= float(prob) <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        if isinstance(label, bool) or label not in (0, 1):
            raise ValueError("label must be 0 or 1")


@dataclass(frozen=True)
class IsotonicModel:
    """A fitted non-decreasing calibration map. ``xs`` are breakpoints (sorted unique raw
    probabilities), ``ys`` the calibrated value at each; ``predict`` interpolates."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]

    def predict(self, prob: float) -> float:
        if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob):
            raise ValueError("probability must be a finite number")
        x = min(1.0, max(0.0, float(prob)))
        if x <= self.xs[0]:
            return self.ys[0]
        if x >= self.xs[-1]:
            return self.ys[-1]
        i = bisect.bisect_right(self.xs, x) - 1
        x0, x1 = self.xs[i], self.xs[i + 1]
        y0, y1 = self.ys[i], self.ys[i + 1]
        if x1 == x0:
            return y0
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def pav_fit(samples: Sequence[tuple[float, int]]) -> IsotonicModel:
    """Fit an isotonic (non-decreasing) calibration by pool-adjacent-violators."""
    _check(samples)
    # Group by raw probability, averaging labels, so tied inputs form one weighted point.
    grouped: dict[float, list[int]] = {}
    for prob, label in samples:
        grouped.setdefault(float(prob), []).append(int(label))
    xs = sorted(grouped)
    # A block on the stack covers a contiguous run of breakpoints: (npoints, weight, mean).
    stack: list[list[float]] = []
    for x in xs:
        weight = float(len(grouped[x]))
        mean = sum(grouped[x]) / len(grouped[x])
        stack.append([1.0, weight, mean])
        while len(stack) >= 2 and stack[-2][2] > stack[-1][2] + 1e-12:
            n2, w2, m2 = stack.pop()
            n1, w1, m1 = stack.pop()
            total = w1 + w2
            stack.append([n1 + n2, total, (m1 * w1 + m2 * w2) / total])
    # Expand each block's pooled mean over the breakpoints it covers, in order.
    ys: list[float] = []
    idx = 0
    for npoints, _weight, mean in stack:
        for _ in range(int(npoints)):
            ys.append(mean)
            idx += 1
    return IsotonicModel(tuple(xs), tuple(ys))
