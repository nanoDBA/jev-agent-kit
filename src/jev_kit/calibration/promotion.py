"""Promotion-criteria evaluator (Phase 4, decision E5).

Given calibrated held-out decisions for one question fingerprint, choose Noul bounds that
maximize coverage subject to accuracy on answered staying within a tolerance of a reference
accuracy, then CONFIRM the same bounds on a second frozen split. Invalid outputs count as
errors. This is the rule from docs/research/09 (arXiv:2609.26550).

This is tooling only. It returns a recommendation; it never writes the threshold registry and
never enables enforce. Promoting a gate to enforce against real traffic is owner-gated
(docs/CHARTER.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jev_kit.calibration.metrics import coverage_at_threshold

# Candidate bound grid; coarse is enough for a recommendation and keeps this deterministic.
_GRID = tuple(round(0.01 * i, 2) for i in range(0, 101))


@dataclass(frozen=True)
class PromotionResult:
    promotable: bool
    reason: str
    no_bound: float | None = None
    yes_bound: float | None = None
    coverage: float | None = None  # answered fraction on the confirm split
    accuracy: float | None = None  # accuracy on answered, confirm split


def _accuracy_and_coverage(
    samples: Sequence[tuple[float, int]], *, low: float, high: float
) -> tuple[float, float | None]:
    cov = coverage_at_threshold(samples, low=low, high=high)
    return cov.answered_fraction, cov.accuracy_on_answered


def select_bounds(
    fit_samples: Sequence[tuple[float, int]],
    confirm_samples: Sequence[tuple[float, int]],
    *,
    reference_accuracy: float,
    tolerance: float,
) -> PromotionResult:
    """Recommend Noul bounds, or decline. Never promotes on its own.

    fit_samples selects the bounds; confirm_samples (a disjoint frozen split) must independently
    meet the accuracy floor for the recommendation to stand.
    """
    if not fit_samples or not confirm_samples:
        return PromotionResult(False, "insufficient_data")
    floor = reference_accuracy - tolerance
    best: tuple[float, float, float] | None = None  # (coverage, no_bound, yes_bound)
    for no_bound in _GRID:
        for yes_bound in _GRID:
            if yes_bound <= no_bound:
                continue
            answered, accuracy = _accuracy_and_coverage(
                fit_samples, low=no_bound, high=yes_bound
            )
            if accuracy is None or accuracy < floor:
                continue
            if best is None or answered > best[0]:
                best = (answered, no_bound, yes_bound)
    if best is None:
        return PromotionResult(False, "no_bounds_meet_accuracy_floor")

    _, no_bound, yes_bound = best
    # Confirm on the frozen disjoint split.
    conf_cov, conf_acc = _accuracy_and_coverage(confirm_samples, low=no_bound, high=yes_bound)
    if conf_acc is None or conf_acc < floor:
        return PromotionResult(
            False, "confirm_split_below_floor", no_bound, yes_bound, conf_cov, conf_acc
        )
    return PromotionResult(True, "meets_criteria", no_bound, yes_bound, conf_cov, conf_acc)
