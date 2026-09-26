"""Calibration metrics: Brier score, ECE, reliability curves and coverage.

Operates on plain labeled samples, independent of the engine or its answer types
(docs/specs/phase-3-4-plan.md, E5: tooling and synthetic-fixture tests only; a real
corpus and enforce promotion stay owner-gated). A sample is (prob, label): prob is
the model's predicted probability of the positive class in [0, 1], label is 0 or 1.
All functions here are pure and deterministic. Stdlib only (ADR 0003).

See docs/research/04-calibration.md: raw probabilities rank well but are
miscalibrated with ties everywhere, hence ECE, Brier and coverage-at-threshold as
the measured quantities rather than trusting the raw number.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

Sample = tuple[float, int]


@dataclass(frozen=True)
class Bin:
    """One equal-width bin of a reliability curve over [0, 1].

    mean_prob and accuracy are None when the bin is empty (count == 0); an empty
    bin is still included so callers can see coverage gaps.
    """

    lower: float
    upper: float
    count: int
    mean_prob: float | None
    accuracy: float | None


@dataclass(frozen=True)
class Coverage:
    """Coverage and accuracy of the confident tails at a threshold pair.

    answered_fraction is the share of samples with prob <= low or prob >= high.
    accuracy_on_answered is the accuracy of the derived hard prediction over that
    subset (prob >= high predicts label 1, prob <= low predicts label 0), ignoring
    the unanswered middle band; None when nothing is answered.
    """

    answered_fraction: float
    accuracy_on_answered: float | None


def _check_sample(prob: Any, label: Any) -> None:
    """Raise ValueError unless prob is a finite non-bool number in [0, 1] and
    label is 0 or 1 as a plain int (bool is rejected even though bool is an int
    subclass, since a JSON true/false must not silently pass as 1/0)."""
    if isinstance(label, bool) or not isinstance(label, int):
        raise ValueError(f"label must be an int 0 or 1, got {label!r}")
    if label not in (0, 1):
        raise ValueError(f"label must be 0 or 1, got {label!r}")
    if isinstance(prob, bool) or not isinstance(prob, (int, float)):
        raise ValueError(f"prob must be a finite number in [0, 1], got {prob!r}")
    if not math.isfinite(prob):
        raise ValueError(f"prob must be finite, got {prob!r}")
    if prob < 0.0 or prob > 1.0:
        raise ValueError(f"prob must be in [0, 1], got {prob!r}")


def _validated(samples: Sequence[Sample]) -> list[Sample]:
    """Check every sample and return a plain (float, int) copy."""
    result: list[Sample] = []
    for prob, label in samples:
        _check_sample(prob, label)
        result.append((float(prob), int(label)))
    return result


def brier_score(samples: Sequence[Sample]) -> float:
    """Mean squared error between predicted probability and label.

    Raises ValueError if samples is empty or any sample is invalid.
    """
    validated = _validated(samples)
    if not validated:
        raise ValueError("brier_score requires at least one sample")
    return math.fsum((prob - label) ** 2 for prob, label in validated) / len(validated)


def _bin_index(prob: float, bins: int) -> int:
    """Equal-width bin index over [0, 1]; prob == 1.0 lands in the last bin
    rather than one past the end."""
    index = int(prob * bins)
    return min(max(index, 0), bins - 1)


def _binned(validated: list[Sample], bins: int) -> list[Bin]:
    width = 1.0 / bins
    sums = [0.0] * bins
    counts = [0] * bins
    hits = [0] * bins
    for prob, label in validated:
        index = _bin_index(prob, bins)
        sums[index] += prob
        counts[index] += 1
        hits[index] += label

    curve: list[Bin] = []
    for i in range(bins):
        lower = i * width
        upper = (i + 1) * width
        count = counts[i]
        if count == 0:
            curve.append(Bin(lower=lower, upper=upper, count=0, mean_prob=None, accuracy=None))
        else:
            curve.append(
                Bin(
                    lower=lower,
                    upper=upper,
                    count=count,
                    mean_prob=sums[i] / count,
                    accuracy=hits[i] / count,
                )
            )
    return curve


def reliability_curve(samples: Sequence[Sample], *, bins: int = 10) -> list[Bin]:
    """Equal-width reliability bins over [0, 1], including empty bins.

    Bin counts always sum to len(samples). Raises ValueError if bins < 1 or any
    sample is invalid. Empty input is allowed and returns bins that are all empty
    (a reliability curve is well-defined, if uninformative, with no data).
    """
    if bins < 1:
        raise ValueError(f"bins must be >= 1, got {bins}")
    validated = _validated(samples)
    return _binned(validated, bins)


def expected_calibration_error(samples: Sequence[Sample], *, bins: int = 10) -> float:
    """Standard ECE: sum over nonempty equal-width bins of (bin share) times
    |mean predicted probability - accuracy| in that bin.

    Raises ValueError if samples is empty, bins < 1, or any sample is invalid.
    """
    if bins < 1:
        raise ValueError(f"bins must be >= 1, got {bins}")
    validated = _validated(samples)
    if not validated:
        raise ValueError("expected_calibration_error requires at least one sample")
    total = len(validated)
    terms: list[float] = []
    for b in _binned(validated, bins):
        if b.count == 0 or b.mean_prob is None or b.accuracy is None:
            continue
        weight = b.count / total
        terms.append(weight * abs(b.mean_prob - b.accuracy))
    return math.fsum(terms)


def coverage_at_threshold(samples: Sequence[Sample], *, low: float, high: float) -> Coverage:
    """Coverage and accuracy of the confident tails at (low, high).

    A sample with prob >= high is answered as a predicted positive; one with
    prob <= low is answered as a predicted negative; the open middle band
    (low, high) is left unanswered. Raises ValueError if samples is empty, low
    or high is not finite, or 0 <= low <= high <= 1 does not hold.
    """
    if not math.isfinite(low) or not math.isfinite(high):
        raise ValueError(f"low and high must be finite, got low={low!r} high={high!r}")
    if low < 0.0 or high > 1.0 or low > high:
        raise ValueError(f"require 0 <= low <= high <= 1, got low={low!r} high={high!r}")
    validated = _validated(samples)
    if not validated:
        raise ValueError("coverage_at_threshold requires at least one sample")

    total = len(validated)
    answered = 0
    correct = 0
    for prob, label in validated:
        if prob >= high:
            answered += 1
            correct += 1 if label == 1 else 0
        elif prob <= low:
            answered += 1
            correct += 1 if label == 0 else 0

    accuracy_on_answered = correct / answered if answered > 0 else None
    return Coverage(answered_fraction=answered / total, accuracy_on_answered=accuracy_on_answered)
