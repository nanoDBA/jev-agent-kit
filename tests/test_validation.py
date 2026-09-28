"""Response-validation tests (spec stories 33 to 38, 35a; Codex findings R06, R01).

These exercise the public validation surface with scripted raw answers, the way a mock
transport would supply them. No network, no private-function assertions beyond the
validated dataclasses that the public functions return.
"""

from __future__ import annotations

import pytest

from jev_kit.errors import FailReason, ValidationError
from jev_kit.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    ConsequenceClass,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from jev_kit.validation import validate_answer, validate_answer_set

GATE = ConsequenceClass.GATE


def choice_q() -> ChoiceQuestion:
    return ChoiceQuestion(
        question_id="route",
        instructions="Which handler?",
        options=("billing", "technical", "account"),
        consequence=GATE,
    )


def score_q() -> ScoreQuestion:
    # 0 = safe, 1 = review, 2 = unsafe
    return ScoreQuestion(
        question_id="risk",
        instructions="How risky?",
        levels=("safe", "review", "unsafe"),
        consequence=GATE,
    )


def noul_q() -> NoulQuestion:
    return NoulQuestion(question_id="destructive", instructions="Destructive?", consequence=GATE)


# --- Choice -----------------------------------------------------------------


def test_choice_valid() -> None:
    answer = validate_answer(
        choice_q(),
        {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.8, "technical": 0.15, "account": 0.05},
            "confidence": 0.7,
        },
    )
    assert answer.choice == "billing"  # type: ignore[union-attr]


def test_choice_invented_option_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_answer(
            choice_q(),
            {
                "choice": "billing",
                "probabilities": {"billing": 0.5, "technical": 0.3, "made_up": 0.2},
                "confidence": 0.7,
            },
        )
    assert exc.value.reason is FailReason.ANSWER_INVALID


def test_choice_not_argmax_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_answer(
            choice_q(),
            {
                "choice": "account",  # not the highest probability
                "probabilities": {"billing": 0.8, "technical": 0.15, "account": 0.05},
                "confidence": 0.7,
            },
        )


def test_choice_probabilities_do_not_sum_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_answer(
            choice_q(),
            {
                "choice": "billing",
                "probabilities": {"billing": 0.5, "technical": 0.1, "account": 0.1},
                "confidence": 0.7,
            },
        )


def test_choice_boolean_confidence_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_answer(
            choice_q(),
            {
                "choice": "billing",
                "probabilities": {"billing": 0.8, "technical": 0.15, "account": 0.05},
                "confidence": True,
            },
        )


# --- Score ------------------------------------------------------------------


def test_score_valid() -> None:
    answer = validate_answer(
        score_q(),
        {
            "type": "score",
            "score": 1.0,
            "legend": {"0": "safe", "1": "review", "2": "unsafe"},
            "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0},
            "confidence": 0.9,
        },
    )
    assert answer.score == pytest.approx(1.0)  # type: ignore[union-attr]


def test_score_boolean_score_and_confidence_rejected() -> None:
    # Codex R06 counterexample: score=true, confidence=true would pass naive numeric checks.
    with pytest.raises(ValidationError):
        validate_answer(
            score_q(),
            {
                "score": True,
                "legend": {"0": "safe", "1": "review", "2": "unsafe"},
                "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0},
                "confidence": True,
            },
        )


def test_score_inconsistent_with_distribution_rejected() -> None:
    # score says "safe" (0.0) but the distribution puts all mass on "unsafe" (index 2).
    with pytest.raises(ValidationError) as exc:
        validate_answer(
            score_q(),
            {
                "type": "score",
                "score": 0.0,
                "legend": {"0": "safe", "1": "review", "2": "unsafe"},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0},
                "confidence": 1.0,
            },
        )
    assert exc.value.check == "score_inconsistent"


def test_score_swapped_legend_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_answer(
            score_q(),
            {
                "type": "score",
                "score": 1.0,
                "legend": {"0": "unsafe", "1": "review", "2": "safe"},  # swapped 0 and 2
                "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0},
                "confidence": 0.9,
            },
        )
    assert exc.value.check == "score_legend_value"


# --- Noul -------------------------------------------------------------------


def test_noul_valid() -> None:
    answer = validate_answer(noul_q(), {"type": "noul", "noul": 0.93})
    assert answer.noul == pytest.approx(0.93)  # type: ignore[union-attr]


def test_noul_boolean_rejected() -> None:
    # A JSON true must not read as a strong yes.
    with pytest.raises(ValidationError):
        validate_answer(noul_q(), {"noul": True})


def test_noul_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_answer(noul_q(), {"noul": 1.5})


# --- Answer set -------------------------------------------------------------


def test_answer_set_matches() -> None:
    questions = {"destructive": noul_q()}
    answers = validate_answer_set(questions, {"destructive": {"type": "noul", "noul": 0.1}})
    assert set(answers) == {"destructive"}


def test_answer_set_extra_answer_fails_whole_set() -> None:
    questions = {"destructive": noul_q()}
    with pytest.raises(ValidationError) as exc:
        validate_answer_set(
            questions, {"destructive": {"noul": 0.1}, "surprise": {"noul": 0.9}}
        )
    assert exc.value.reason is FailReason.ANSWER_SET_MISMATCH


def test_answer_set_missing_answer_fails_whole_set() -> None:
    questions = {"destructive": noul_q(), "risky": noul_q()}
    with pytest.raises(ValidationError) as exc:
        validate_answer_set(questions, {"destructive": {"noul": 0.1}})
    assert exc.value.reason is FailReason.ANSWER_SET_MISMATCH


def test_wrong_answer_type_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_answer(noul_q(), {"type": "choice", "noul": 0.5})


# --- Live answer shape (docs/research/11-live-answer-shape.md) ---------------
# Real answers recorded from jev-1.13.0 on 2026-09-28. Jev rounds to two decimals, so the
# reported score and chosen option can differ from what the rounded probabilities imply by
# up to the rounding bound. These must validate; contradictions beyond it must not.


def score5_q() -> ScoreQuestion:
    return ScoreQuestion(
        question_id="risk5",
        instructions="How risky?",
        levels=("none", "low", "medium", "high", "critical"),
        consequence=GATE,
    )


def _score(probs: dict[str, float], score: float, levels: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "score",
        "score": score,
        "confidence": 0.3,
        "legend": {str(i): name for i, name in enumerate(levels)},
        "probabilities": probs,
    }


@pytest.mark.parametrize(
    ("question", "probs", "score"),
    [
        # Recorded live: score 0.76 against a mean of exactly 0.76.
        (score_q, {"0": 0.43, "1": 0.38, "2": 0.19}, 0.76),
        # Rounding gaps of 0.01 (3 levels) and 0.02 (5 levels), as measured live.
        (score_q, {"0": 0.43, "1": 0.38, "2": 0.19}, 0.77),
        (score_q, {"0": 0.43, "1": 0.38, "2": 0.19}, 0.75),
        (score5_q, {"0": 0.30, "1": 0.30, "2": 0.26, "3": 0.10, "4": 0.04}, 1.30),
    ],
)
def test_score_within_rounding_bound_validates(
    question: object, probs: dict[str, float], score: float
) -> None:
    q = question()  # type: ignore[operator]
    answer = validate_answer(q, _score(probs, score, q.levels))
    assert isinstance(answer, ScoreAnswer)
    assert answer.score == score


@pytest.mark.parametrize(
    ("question", "probs", "score"),
    [
        (score_q, {"0": 0.43, "1": 0.38, "2": 0.19}, 0.80),  # 0.04 off, bound is 0.015
        (score_q, {"0": 0.43, "1": 0.38, "2": 0.19}, 1.50),
        (score5_q, {"0": 0.30, "1": 0.30, "2": 0.26, "3": 0.10, "4": 0.04}, 1.34),  # bound 0.025
    ],
)
def test_score_beyond_rounding_bound_still_rejected(
    question: object, probs: dict[str, float], score: float
) -> None:
    q = question()  # type: ignore[operator]
    with pytest.raises(ValidationError) as exc:
        validate_answer(q, _score(probs, score, q.levels))
    assert exc.value.reason is FailReason.ANSWER_INVALID


def test_choice_trailing_top_by_one_rounding_step_validates() -> None:
    # Two options within half a step before rounding can end up 0.01 apart after it.
    raw = {"type": "choice", "choice": "billing", "confidence": 0.2,
           "probabilities": {"billing": 0.40, "technical": 0.41, "account": 0.19}}
    answer = validate_answer(choice_q(), raw)
    assert isinstance(answer, ChoiceAnswer)
    assert answer.choice == "billing"


def test_choice_trailing_top_by_more_than_one_step_still_rejected() -> None:
    raw = {"type": "choice", "choice": "billing", "confidence": 0.2,
           "probabilities": {"billing": 0.39, "technical": 0.42, "account": 0.19}}
    with pytest.raises(ValidationError) as exc:
        validate_answer(choice_q(), raw)
    assert exc.value.reason is FailReason.ANSWER_INVALID
