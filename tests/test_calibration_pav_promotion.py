"""Isotonic (PAV) and promotion-evaluator tests (Phase 4)."""

from __future__ import annotations

import pytest

from jev_kit.calibration.pav import pav_fit
from jev_kit.calibration.promotion import select_bounds


def test_pav_is_monotone_nondecreasing() -> None:
    # Deliberately non-monotone raw means should be pooled into a monotone fit.
    samples = [(0.1, 0), (0.2, 1), (0.3, 0), (0.4, 1), (0.5, 1)]
    model = pav_fit(samples)
    assert list(model.ys) == sorted(model.ys)


def test_pav_pools_a_violator() -> None:
    # 0.2 -> 1 then 0.3 -> 0 violates monotonicity; they pool to 0.5.
    model = pav_fit([(0.2, 1), (0.3, 0)])
    assert model.predict(0.2) == pytest.approx(0.5)
    assert model.predict(0.3) == pytest.approx(0.5)


def test_pav_ties_merge() -> None:
    model = pav_fit([(0.5, 0), (0.5, 1), (0.5, 1)])
    assert model.predict(0.5) == pytest.approx(2 / 3)


def test_pav_already_monotone_passthrough() -> None:
    model = pav_fit([(0.1, 0), (0.9, 1)])
    assert model.predict(0.1) == pytest.approx(0.0)
    assert model.predict(0.9) == pytest.approx(1.0)
    assert 0.0 <= model.predict(0.5) <= 1.0  # interpolated, monotone


def test_pav_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        pav_fit([])
    with pytest.raises(ValueError):
        pav_fit([(0.5, True)])  # bool label
    with pytest.raises(ValueError):
        pav_fit([(1.5, 1)])  # out of range


def _clean_split() -> list[tuple[float, int]]:
    # Confident tails are correct: low probs are label 0, high probs label 1.
    return [(0.02, 0)] * 10 + [(0.98, 1)] * 10 + [(0.5, 1), (0.5, 0)]


def test_promotion_recommends_when_criteria_met() -> None:
    fit = _clean_split()
    confirm = _clean_split()
    result = select_bounds(fit, confirm, reference_accuracy=1.0, tolerance=0.1)
    assert result.promotable is True
    assert result.no_bound is not None and result.yes_bound is not None
    assert result.no_bound < result.yes_bound


def test_promotion_declines_when_confirm_split_fails() -> None:
    fit = _clean_split()
    # Confirm split: the confident tails are WRONG, so accuracy on answered collapses.
    confirm = [(0.02, 1)] * 10 + [(0.98, 0)] * 10
    result = select_bounds(fit, confirm, reference_accuracy=1.0, tolerance=0.1)
    assert result.promotable is False


def test_promotion_needs_data() -> None:
    assert select_bounds([], [], reference_accuracy=0.9, tolerance=0.1).promotable is False
