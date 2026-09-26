"""Calibration metrics tests (docs/specs/phase-3-4-plan.md E3/E5).

Synthetic, hand-computed fixtures only; no live traffic and no engine dependency,
per the owner-gated boundary (real corpus, real calibration, and enforce promotion
stay out of scope for this loop).
"""

from __future__ import annotations

import math

import pytest

from jev_kit.calibration.metrics import (
    Sample,
    brier_score,
    coverage_at_threshold,
    expected_calibration_error,
    reliability_curve,
)

# --- Fixtures -----------------------------------------------------------------


def _calibrated_samples() -> list[Sample]:
    """Five groups of 10 samples each, one per decile centre. Within each group
    the fraction of positive labels equals the predicted probability exactly, so
    every nonempty reliability bin has mean_prob == accuracy and ECE is exactly 0.
    """
    samples: list[Sample] = []
    for prob, positives in ((0.1, 1), (0.3, 3), (0.5, 5), (0.7, 7), (0.9, 9)):
        for i in range(10):
            label = 1 if i < positives else 0
            samples.append((prob, label))
    return samples


def _miscalibrated_samples() -> list[Sample]:
    """20 samples, all predicted 0.9 confident-positive, all actually negative."""
    return [(0.9, 0) for _ in range(20)]


# --- brier_score ----------------------------------------------------------


def test_brier_score_calibrated_fixture() -> None:
    samples = _calibrated_samples()
    # Each decile group of 10 contributes 10 * [k * (p - 1)**2 + (10 - k) * p**2]
    # where k is the positive count, summed and divided by 50.
    expected = sum(
        k * (p - 1) ** 2 + (10 - k) * p**2
        for p, k in ((0.1, 1), (0.3, 3), (0.5, 5), (0.7, 7), (0.9, 9))
    ) / 50
    assert brier_score(samples) == pytest.approx(expected)
    assert brier_score(samples) == pytest.approx(0.17, abs=1e-9)


def test_brier_score_miscalibrated_fixture_is_high() -> None:
    # prob=0.9, label=0 for every sample: (0.9 - 0)**2 = 0.81 each.
    assert brier_score(_miscalibrated_samples()) == pytest.approx(0.81)


def test_brier_score_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one sample"):
        brier_score([])


def test_brier_score_rejects_bool_label() -> None:
    with pytest.raises(ValueError, match="label"):
        brier_score([(0.5, True)])


def test_brier_score_rejects_nan_prob() -> None:
    with pytest.raises(ValueError, match="finite"):
        brier_score([(math.nan, 0)])


def test_brier_score_rejects_out_of_range_prob() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        brier_score([(1.5, 1)])


def test_brier_score_rejects_bad_label_value() -> None:
    with pytest.raises(ValueError, match="0 or 1"):
        brier_score([(0.5, 2)])


# --- expected_calibration_error --------------------------------------------


def test_ece_calibrated_fixture_is_zero() -> None:
    assert expected_calibration_error(_calibrated_samples()) == pytest.approx(0.0, abs=1e-9)


def test_ece_miscalibrated_fixture_is_high() -> None:
    # All 20 samples land in bin [0.9, 1.0): mean_prob 0.9, accuracy 0 (all label 0).
    assert expected_calibration_error(_miscalibrated_samples()) == pytest.approx(0.9)


def test_ece_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one sample"):
        expected_calibration_error([])


def test_ece_rejects_bins_below_one() -> None:
    with pytest.raises(ValueError, match="bins"):
        expected_calibration_error(_calibrated_samples(), bins=0)


def test_ece_weights_bins_by_share() -> None:
    # One confident-correct sample and one confident-wrong sample in different
    # bins, unequal group sizes: expected = share-weighted mean of |gap| per bin.
    samples: list[Sample] = [(0.95, 1)] * 5 + [(0.05, 1)] * 15
    # bin [0.9, 1.0): mean_prob 0.95, accuracy 1.0 -> gap 0.05, weight 5/20
    # bin [0.0, 0.1): mean_prob 0.05, accuracy 1.0 -> gap 0.95, weight 15/20
    expected = (5 / 20) * 0.05 + (15 / 20) * 0.95
    assert expected_calibration_error(samples) == pytest.approx(expected)


# --- reliability_curve -------------------------------------------------------


def test_reliability_curve_bins_sum_to_sample_count() -> None:
    samples = _calibrated_samples()
    curve = reliability_curve(samples, bins=10)
    assert len(curve) == 10
    assert sum(b.count for b in curve) == len(samples)


def test_reliability_curve_empty_bins_present_with_none() -> None:
    samples = _calibrated_samples()  # occupies deciles 1,3,5,7,9 only (0-indexed)
    curve = reliability_curve(samples, bins=10)
    empty_indices = {0, 2, 4, 6, 8}
    for i, b in enumerate(curve):
        if i in empty_indices:
            assert b.count == 0
            assert b.mean_prob is None
            assert b.accuracy is None
        else:
            assert b.count == 10
            assert b.mean_prob is not None
            assert b.accuracy is not None


def test_reliability_curve_bin_bounds_and_values() -> None:
    curve = reliability_curve(_calibrated_samples(), bins=10)
    decile_1 = curve[1]
    assert decile_1.lower == pytest.approx(0.1)
    assert decile_1.upper == pytest.approx(0.2)
    assert decile_1.count == 10
    assert decile_1.mean_prob == pytest.approx(0.1)
    assert decile_1.accuracy == pytest.approx(0.1)  # 1 positive out of 10


def test_reliability_curve_empty_input_is_all_empty_bins() -> None:
    curve = reliability_curve([], bins=4)
    assert len(curve) == 4
    assert all(b.count == 0 and b.mean_prob is None and b.accuracy is None for b in curve)


def test_reliability_curve_rejects_bins_below_one() -> None:
    with pytest.raises(ValueError, match="bins"):
        reliability_curve(_calibrated_samples(), bins=0)


# --- coverage_at_threshold ---------------------------------------------------


def test_coverage_at_threshold_answers_only_confident_tails() -> None:
    # 10 confident-positive (correct), 10 confident-negative (correct),
    # 10 in the unanswered middle band.
    samples: list[Sample] = (
        [(0.95, 1)] * 10 + [(0.05, 0)] * 10 + [(0.5, 1)] * 5 + [(0.5, 0)] * 5
    )
    coverage = coverage_at_threshold(samples, low=0.1, high=0.9)
    assert coverage.answered_fraction == pytest.approx(20 / 30)
    assert coverage.accuracy_on_answered == pytest.approx(1.0)


def test_coverage_at_threshold_mixed_accuracy_on_answered() -> None:
    # Confident tail predictions: 3 correct positives, 1 wrong positive (label 0),
    # 4 correct negatives, 2 wrong negatives (label 1); 5 unanswered in the middle.
    samples: list[Sample] = (
        [(0.9, 1)] * 3
        + [(0.9, 0)] * 1
        + [(0.1, 0)] * 4
        + [(0.1, 1)] * 2
        + [(0.5, 1)] * 5
    )
    coverage = coverage_at_threshold(samples, low=0.2, high=0.8)
    assert coverage.answered_fraction == pytest.approx(10 / 15)
    assert coverage.accuracy_on_answered == pytest.approx(7 / 10)


def test_coverage_at_threshold_nothing_answered_gives_none_accuracy() -> None:
    samples: list[Sample] = [(0.5, 1), (0.4, 0), (0.6, 1)]
    coverage = coverage_at_threshold(samples, low=0.1, high=0.9)
    assert coverage.answered_fraction == pytest.approx(0.0)
    assert coverage.accuracy_on_answered is None


def test_coverage_at_threshold_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one sample"):
        coverage_at_threshold([], low=0.1, high=0.9)


def test_coverage_at_threshold_rejects_low_above_high() -> None:
    with pytest.raises(ValueError, match="low"):
        coverage_at_threshold([(0.5, 1)], low=0.9, high=0.1)


# --- shared guards ------------------------------------------------------------


def test_reliability_curve_rejects_bool_label() -> None:
    with pytest.raises(ValueError, match="label"):
        reliability_curve([(0.5, False)])


def test_coverage_at_threshold_rejects_nan_prob() -> None:
    with pytest.raises(ValueError, match="finite"):
        coverage_at_threshold([(math.nan, 1)], low=0.1, high=0.9)
