"""Routing tests: candidate evaluation and the full route-resolution matrix.

Covers spec stories 12 to 21 and Codex findings R07 (uncalibrated and missing thresholds)
and R20 (shadow precedence). The matrix test asserts every row of the table in the spec's
Implementation Decisions.
"""

from __future__ import annotations

import itertools

import pytest

from jev_kit.errors import FailReason, ValidationError
from jev_kit.routing import (
    Candidate,
    ChoiceThreshold,
    NoulThreshold,
    RegistryEntry,
    ScoreInterval,
    ScoreThreshold,
    ThresholdStatus,
    evaluate_candidate,
    resolve_route,
)
from jev_kit.types import (
    ChoiceAnswer,
    ConsequenceClass,
    Mode,
    NoulAnswer,
    Route,
    ScoreAnswer,
)

GATE = ConsequenceClass.GATE
ADVISORY = ConsequenceClass.ADVISORY


# --- Candidate evaluation ---------------------------------------------------


def test_noul_candidate_bands() -> None:
    t = NoulThreshold(yes_bound=0.9, no_bound=0.1)
    assert evaluate_candidate(NoulAnswer("q", 0.95), t) == Candidate(True, "yes")
    assert evaluate_candidate(NoulAnswer("q", 0.05), t) == Candidate(True, "no")
    assert evaluate_candidate(NoulAnswer("q", 0.5), t).clears is False


def test_noul_bounds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        NoulThreshold(yes_bound=0.2, no_bound=0.8)


def test_noul_bound_rejects_bool() -> None:
    with pytest.raises(ValidationError):
        NoulThreshold(yes_bound=True, no_bound=0.1)


def test_choice_candidate_margin_and_confidence() -> None:
    t = ChoiceThreshold(min_confidence=0.7, min_margin=0.2)
    clear = ChoiceAnswer("q", "a", {"a": 0.8, "b": 0.15, "c": 0.05}, 0.8)
    assert evaluate_candidate(clear, t).clears is True
    low_margin = ChoiceAnswer("q", "a", {"a": 0.45, "b": 0.44, "c": 0.11}, 0.9)
    assert evaluate_candidate(low_margin, t).clears is False
    low_conf = ChoiceAnswer("q", "a", {"a": 0.8, "b": 0.15, "c": 0.05}, 0.5)
    assert evaluate_candidate(low_conf, t).clears is False


def test_choice_exact_tie_is_review() -> None:
    t = ChoiceThreshold(min_confidence=0.0, min_margin=0.0)
    tie = ChoiceAnswer("q", "a", {"a": 0.5, "b": 0.5}, 1.0)
    assert evaluate_candidate(tie, t).clears is False


def test_score_intervals() -> None:
    t = ScoreThreshold(
        intervals=(
            ScoreInterval(0.0, 0.5, "safe"),
            ScoreInterval(1.5, 2.0, "unsafe"),
        ),
        min_confidence=0.6,
    )
    safe = ScoreAnswer("q", 0.0, {"0": 1.0, "1": 0.0, "2": 0.0}, {"0": "", "1": "", "2": ""}, 0.9)
    assert evaluate_candidate(safe, t) == Candidate(True, "safe")
    # top interval inclusive at the maximum
    unsafe = ScoreAnswer("q", 2.0, {"0": 0.0, "1": 0.0, "2": 1.0}, {"0": "", "1": "", "2": ""}, 0.9)
    assert evaluate_candidate(unsafe, t) == Candidate(True, "unsafe")
    # a score in the gap between intervals is the review band
    mid = ScoreAnswer("q", 1.0, {"0": 0.0, "1": 1.0, "2": 0.0}, {"0": "", "1": "", "2": ""}, 0.9)
    assert evaluate_candidate(mid, t).clears is False


def test_score_intervals_must_not_overlap() -> None:
    with pytest.raises(ValidationError):
        ScoreThreshold(
            intervals=(ScoreInterval(0.0, 1.2, "a"), ScoreInterval(1.0, 2.0, "b")),
            min_confidence=0.5,
        )


def test_threshold_type_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        evaluate_candidate(NoulAnswer("q", 0.5), ChoiceThreshold(0.5, 0.1))


# --- The route-resolution matrix -------------------------------------------

CALIBRATED = RegistryEntry(ThresholdStatus.CALIBRATED, "gpt-6", "ev-1")
UNCALIBRATED = RegistryEntry(ThresholdStatus.UNCALIBRATED, "gpt-6", "ev-1")
NEVER = RegistryEntry(ThresholdStatus.NEVER_AUTO_ACCEPT, "gpt-6", "ev-1")

CLEARS = Candidate(clears=True, label="yes")
REVIEW = Candidate(clears=False, label=None)


def route(
    consequence: ConsequenceClass,
    mode: Mode,
    entry: RegistryEntry | None,
    candidate: Candidate | None,
    *,
    failed: bool = False,
    is_mock: bool = False,
) -> Route:
    return resolve_route(
        consequence=consequence,
        mode=mode,
        entry=entry,
        candidate=candidate,
        failed=failed,
        fail_reason=FailReason.TRANSPORT if failed else None,
        is_mock=is_mock,
    ).route


def test_matrix_failure_row() -> None:
    for mode in (Mode.SHADOW, Mode.ENFORCE):
        assert route(GATE, mode, CALIBRATED, CLEARS, failed=True) is Route.ASK
        assert route(ADVISORY, mode, CALIBRATED, CLEARS, failed=True) is Route.NO_ADVICE


def test_matrix_mock_row() -> None:
    for mode in (Mode.SHADOW, Mode.ENFORCE):
        assert route(GATE, mode, CALIBRATED, CLEARS, is_mock=True) is Route.ASK
        assert route(ADVISORY, mode, CALIBRATED, CLEARS, is_mock=True) is Route.NO_ADVICE


def test_matrix_shadow_success_is_no_advice() -> None:
    for consequence in (GATE, ADVISORY):
        for entry, cand in ((CALIBRATED, CLEARS), (CALIBRATED, REVIEW), (UNCALIBRATED, None)):
            assert route(consequence, Mode.SHADOW, entry, cand) is Route.NO_ADVICE


def test_matrix_enforce_uncalibrated_or_missing() -> None:
    for entry, cand in ((None, None), (UNCALIBRATED, None), (NEVER, CLEARS)):
        assert route(GATE, Mode.ENFORCE, entry, cand) is Route.ASK
        assert route(ADVISORY, Mode.ENFORCE, entry, cand) is Route.NO_ADVICE


def test_matrix_enforce_calibrated_clears() -> None:
    assert route(GATE, Mode.ENFORCE, CALIBRATED, CLEARS) is Route.ACCEPT
    assert route(ADVISORY, Mode.ENFORCE, CALIBRATED, CLEARS) is Route.ACCEPT


def test_matrix_enforce_calibrated_review() -> None:
    assert route(GATE, Mode.ENFORCE, CALIBRATED, REVIEW) is Route.ASK
    assert route(ADVISORY, Mode.ENFORCE, CALIBRATED, REVIEW) is Route.NO_ADVICE


def test_never_accept_anywhere_on_failure_or_mock() -> None:
    # Property: no failure or mock path ever yields ACCEPT (policy never absolves).
    for consequence, mode, flag in itertools.product(
        (GATE, ADVISORY), (Mode.SHADOW, Mode.ENFORCE), ("failed", "mock")
    ):
        r = route(
            consequence,
            mode,
            CALIBRATED,
            CLEARS,
            failed=(flag == "failed"),
            is_mock=(flag == "mock"),
        )
        assert r is not Route.ACCEPT


def test_would_route_null_without_calibration() -> None:
    res = resolve_route(
        consequence=GATE,
        mode=Mode.ENFORCE,
        entry=UNCALIBRATED,
        candidate=None,
        failed=False,
        fail_reason=None,
        is_mock=False,
    )
    assert res.would_route is None
    assert res.reason is FailReason.UNCALIBRATED


def test_would_route_shows_candidate_in_shadow() -> None:
    res = resolve_route(
        consequence=GATE,
        mode=Mode.SHADOW,
        entry=CALIBRATED,
        candidate=CLEARS,
        failed=False,
        fail_reason=None,
        is_mock=False,
    )
    assert res.route is Route.NO_ADVICE  # shadow never changes behavior
    assert res.would_route is Route.ACCEPT  # but shows what enforce would do
