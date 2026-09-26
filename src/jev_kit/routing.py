"""Threshold shapes, candidate evaluation, and the route-resolution matrix.

This is the safety core. Two separate outputs (spec stories 12 to 21, findings R07, R20):

- ``would_route``: the candidate route a calibrated threshold implies, ignoring mode, mocks
  and failures. ``None`` when no evaluable calibrated threshold exists (never invented).
- the effective ``route``: what the caller acts on, after this precedence:
  failure, then mock gate rule, then shadow, then calibration status and never-auto-accept,
  then the threshold. An effective ``accept`` requires a calibrated, target-matched entry for
  advisory and gate questions alike.

Boolean values are rejected wherever a number is expected, including threshold bounds
(spec story 35a).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from enum import StrEnum

from jev_kit.errors import FailReason, ValidationError
from jev_kit.types import (
    Answer,
    ChoiceAnswer,
    ConsequenceClass,
    Mode,
    NoulAnswer,
    Route,
    ScoreAnswer,
)

TIE_TOLERANCE = 1e-9


class ThresholdStatus(StrEnum):
    CALIBRATED = "calibrated"
    UNCALIBRATED = "uncalibrated"
    NEVER_AUTO_ACCEPT = "never_auto_accept"


def _check_bound(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(FailReason.CONFIG, f"bound_not_number:{name}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValidationError(FailReason.CONFIG, f"bound_range:{name}")
    return number


@dataclass(frozen=True)
class NoulThreshold:
    yes_bound: float
    no_bound: float

    def __post_init__(self) -> None:
        _check_bound(self.yes_bound, "yes_bound")
        _check_bound(self.no_bound, "no_bound")
        if not self.no_bound < self.yes_bound:
            raise ValidationError(FailReason.CONFIG, "noul_bounds_order")


@dataclass(frozen=True)
class ChoiceThreshold:
    min_confidence: float
    min_margin: float  # minimum gap between the top two probabilities

    def __post_init__(self) -> None:
        _check_bound(self.min_confidence, "min_confidence")
        _check_bound(self.min_margin, "min_margin")


@dataclass(frozen=True)
class ScoreInterval:
    lower: float  # inclusive
    upper: float  # exclusive, except the top interval is inclusive at the level maximum
    label: str


@dataclass(frozen=True)
class ScoreThreshold:
    intervals: tuple[ScoreInterval, ...]
    min_confidence: float

    def __post_init__(self) -> None:
        _check_bound(self.min_confidence, "min_confidence")
        if not self.intervals:
            raise ValidationError(FailReason.CONFIG, "score_no_intervals")
        for interval in self.intervals:
            for name, edge in (("lower", interval.lower), ("upper", interval.upper)):
                if isinstance(edge, bool) or not isinstance(edge, (int, float)):
                    raise ValidationError(FailReason.CONFIG, f"score_bound_not_number:{name}")
                if edge < 0.0 or not math.isfinite(edge):
                    raise ValidationError(FailReason.CONFIG, f"score_bound_range:{name}")
            if not interval.lower < interval.upper:
                raise ValidationError(FailReason.CONFIG, "score_interval_order")
        ordered = sorted(self.intervals, key=lambda i: i.lower)
        for a, b in itertools.pairwise(ordered):
            # Reject any real overlap; only exact adjacency (a.upper == b.lower) is allowed.
            # No tolerance slack, which previously permitted a tiny order-dependent overlap
            # (finding H9/C11).
            if a.upper > b.lower:
                raise ValidationError(FailReason.CONFIG, "score_intervals_overlap")


Threshold = NoulThreshold | ChoiceThreshold | ScoreThreshold


@dataclass(frozen=True)
class RegistryEntry:
    status: ThresholdStatus
    escalation_target: str
    evidence_ref: str
    threshold: Threshold | None = None


@dataclass(frozen=True)
class Candidate:
    """What a calibrated threshold implies for one answer."""

    clears: bool  # True: cleared the threshold; False: fell in the review band
    label: str | None  # the accepted label when clears is True
    margin: float | None = None  # Choice top-two margin, for the record


def _noul_candidate(answer: NoulAnswer, t: NoulThreshold) -> Candidate:
    if answer.noul >= t.yes_bound:
        return Candidate(clears=True, label="yes")
    if answer.noul <= t.no_bound:
        return Candidate(clears=True, label="no")
    return Candidate(clears=False, label=None)


def _choice_candidate(answer: ChoiceAnswer, t: ChoiceThreshold) -> Candidate:
    ordered = sorted(answer.probabilities.values(), reverse=True)
    top = ordered[0]
    second = ordered[1] if len(ordered) > 1 else 0.0
    margin = top - second
    if margin <= TIE_TOLERANCE:  # a tie is always the review band
        return Candidate(clears=False, label=None, margin=margin)
    if answer.confidence >= t.min_confidence and margin >= t.min_margin:
        return Candidate(clears=True, label=answer.choice, margin=margin)
    return Candidate(clears=False, label=None, margin=margin)


def _score_candidate(answer: ScoreAnswer, t: ScoreThreshold) -> Candidate:
    # The rubric maximum is the top level index (n-1), from the validated answer, not the
    # largest configured interval bound (finding C11). Intervals outside [0, n-1] are a
    # configuration error, so a broad malformed interval cannot clear a valid score.
    rubric_max = float(len(answer.probabilities) - 1)
    ordered = sorted(t.intervals, key=lambda i: i.lower)
    for interval in ordered:
        if interval.upper > rubric_max + TIE_TOLERANCE or interval.lower < -TIE_TOLERANCE:
            raise ValidationError(FailReason.CONFIG, "score_interval_out_of_domain")
    for interval in ordered:
        at_top = abs(interval.upper - rubric_max) <= TIE_TOLERANCE
        in_range = interval.lower <= answer.score and (
            answer.score < interval.upper or (at_top and answer.score <= interval.upper)
        )
        if in_range:
            if answer.confidence >= t.min_confidence:
                return Candidate(clears=True, label=interval.label)
            return Candidate(clears=False, label=None)
    return Candidate(clears=False, label=None)  # gap between intervals is the review band


def evaluate_candidate(answer: Answer, threshold: Threshold) -> Candidate:
    """Compute the candidate for a validated answer against a matching threshold shape."""
    if isinstance(answer, NoulAnswer) and isinstance(threshold, NoulThreshold):
        return _noul_candidate(answer, threshold)
    if isinstance(answer, ChoiceAnswer) and isinstance(threshold, ChoiceThreshold):
        return _choice_candidate(answer, threshold)
    if isinstance(answer, ScoreAnswer) and isinstance(threshold, ScoreThreshold):
        return _score_candidate(answer, threshold)
    raise ValidationError(FailReason.CONFIG, "threshold_type_mismatch")


def _fail_route(consequence: ConsequenceClass) -> Route:
    return Route.ASK if consequence is ConsequenceClass.GATE else Route.NO_ADVICE


@dataclass(frozen=True)
class RouteResolution:
    route: Route
    would_route: Route | None
    reason: FailReason | None  # set when the route is a failure or config outcome


def resolve_route(
    *,
    consequence: ConsequenceClass,
    mode: Mode,
    entry: RegistryEntry | None,
    candidate: Candidate | None,
    failed: bool,
    fail_reason: FailReason | None,
    is_mock: bool,
) -> RouteResolution:
    """Apply the route-resolution matrix (spec Implementation Decisions).

    ``candidate`` is provided only when a calibrated threshold was evaluated. ``failed``
    covers transport, validation, egress, budget, target mismatch and any caught exception.
    """
    # would_route: only when a calibrated threshold produced a candidate.
    would: Route | None
    if entry is not None and entry.status is ThresholdStatus.CALIBRATED and candidate is not None:
        would = Route.ACCEPT if candidate.clears else _fail_route(consequence)
    else:
        would = None

    # 1. Failure, in every mode.
    if failed:
        return RouteResolution(_fail_route(consequence), would, fail_reason or FailReason.INTERNAL)

    # 2. Mock-derived: gates ask, advisory gives no advice, in every mode.
    if is_mock:
        return RouteResolution(_fail_route(consequence), would, None)

    # 3. Shadow: every successful non-mock evaluation gives no advice.
    if mode is Mode.SHADOW:
        return RouteResolution(Route.NO_ADVICE, would, None)

    # 4. Enforce, real, success: calibration status gates acceptance.
    if entry is None or entry.status is not ThresholdStatus.CALIBRATED or candidate is None:
        reason = FailReason.UNCALIBRATED if consequence is ConsequenceClass.GATE else None
        return RouteResolution(_fail_route(consequence), would, reason)

    # 5. Calibrated: clear -> accept, review band -> ask (gate) or no advice (advisory).
    if candidate.clears:
        return RouteResolution(Route.ACCEPT, would, None)
    return RouteResolution(_fail_route(consequence), would, None)
