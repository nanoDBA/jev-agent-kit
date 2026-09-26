"""Strict response validation, per question type (spec stories 33 to 38).

Every check that fails raises ValidationError with a fail reason and a short static
check name, never the offending value. A ValidatedAnswer is produced only when every
check passes, so routing can trust it.

Design notes tied to the Codex review:
- bool is a subclass of int in Python, so a JSON true/false would otherwise pass as
  1/0. Every numeric check rejects bool explicitly (spec stories 21, 35a; finding R06).
- A Score whose reported score contradicts its distribution is rejected: the score must
  equal the probability-weighted mean of level indices within SCORE_MEAN_TOLERANCE
  (finding R06).
- Legend descriptions must match the question's level strings exactly (finding R06).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from jev_kit.errors import FailReason, ValidationError
from jev_kit.types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    QuestionType,
    ScoreAnswer,
    ScoreQuestion,
)

# Named tolerances, documented with the receipt schema (spec Implementation Decisions).
PROB_SUM_TOLERANCE = 1e-6
SCORE_MEAN_TOLERANCE = 1e-6
ARGMAX_TOLERANCE = 1e-9


def _unit_number(value: Any, check: str) -> float:
    """Return value as a float if it is a finite, non-boolean number in [0, 1].

    Raises ValidationError(ANSWER_INVALID, check) otherwise. Rejects bool explicitly.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(FailReason.ANSWER_INVALID, check)
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValidationError(FailReason.ANSWER_INVALID, check)
    return number


def _finite_number(value: Any, check: str) -> float:
    """Return value as a finite, non-boolean float. Raises otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(FailReason.ANSWER_INVALID, check)
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(FailReason.ANSWER_INVALID, check)
    return number


def _distribution(raw: Any, expected_keys: tuple[str, ...], check_prefix: str) -> dict[str, float]:
    """Validate a probability map: keys equal expected_keys exactly, values are unit
    numbers, and they sum to 1 within tolerance."""
    if not isinstance(raw, dict):
        raise ValidationError(FailReason.ANSWER_INVALID, f"{check_prefix}_not_object")
    if set(raw.keys()) != set(expected_keys):
        raise ValidationError(FailReason.ANSWER_INVALID, f"{check_prefix}_keys_mismatch")
    probabilities = {key: _unit_number(raw[key], f"{check_prefix}_value") for key in expected_keys}
    total = math.fsum(probabilities.values())
    if abs(total - 1.0) > PROB_SUM_TOLERANCE:
        raise ValidationError(FailReason.ANSWER_INVALID, f"{check_prefix}_sum")
    return probabilities


def _require_answer_type(raw: Any, expected: QuestionType) -> dict[str, Any]:
    """The answer must be an object whose declared type matches the question (story 33)."""
    if not isinstance(raw, dict):
        raise ValidationError(FailReason.ANSWER_INVALID, "answer_not_object")
    declared = raw.get("type")
    if declared is not None and declared != expected.value:
        # If the answer declares a type it must match; the API may omit it, and we already
        # validate by the question's known type, so absence is allowed (see review C10 note).
        raise ValidationError(FailReason.ANSWER_INVALID, "answer_type_mismatch")
    return raw


def _validate_choice(question: ChoiceQuestion, raw: dict[str, Any]) -> ChoiceAnswer:
    options = question.options
    probabilities = _distribution(raw.get("probabilities"), options, "choice_prob")
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in options:
        raise ValidationError(FailReason.ANSWER_INVALID, "choice_not_offered")
    confidence = _unit_number(raw.get("confidence"), "choice_confidence")
    # The chosen option must be the argmax, within a tiny tolerance (spec story 34).
    top = max(probabilities.values())
    if probabilities[choice] < top - ARGMAX_TOLERANCE:
        raise ValidationError(FailReason.ANSWER_INVALID, "choice_not_argmax")
    return ChoiceAnswer(
        question_id=question.question_id,
        choice=choice,
        probabilities=probabilities,
        confidence=confidence,
    )


def _validate_score(question: ScoreQuestion, raw: dict[str, Any]) -> ScoreAnswer:
    n = len(question.levels)
    keys = tuple(str(i) for i in range(n))
    probabilities = _distribution(raw.get("probabilities"), keys, "score_prob")

    legend = raw.get("legend")
    if not isinstance(legend, dict) or set(legend.keys()) != set(keys):
        raise ValidationError(FailReason.ANSWER_INVALID, "score_legend_keys")
    for i, key in enumerate(keys):
        if legend[key] != question.levels[i]:
            raise ValidationError(FailReason.ANSWER_INVALID, "score_legend_value")

    confidence = _unit_number(raw.get("confidence"), "score_confidence")
    score = _finite_number(raw.get("score"), "score_value")
    if score < 0.0 or score > (n - 1):
        raise ValidationError(FailReason.ANSWER_INVALID, "score_range")
    weighted_mean = math.fsum(i * probabilities[key] for i, key in enumerate(keys))
    if abs(score - weighted_mean) > SCORE_MEAN_TOLERANCE:
        raise ValidationError(FailReason.ANSWER_INVALID, "score_inconsistent")

    return ScoreAnswer(
        question_id=question.question_id,
        score=score,
        probabilities=probabilities,
        legend={key: legend[key] for key in keys},
        confidence=confidence,
    )


def _validate_noul(question: NoulQuestion, raw: dict[str, Any]) -> NoulAnswer:
    noul = _unit_number(raw.get("noul"), "noul_value")
    return NoulAnswer(question_id=question.question_id, noul=noul)


def validate_answer(question: Question, raw: Any) -> Answer:
    """Validate one raw answer against its question. Raises ValidationError on any failure."""
    if isinstance(question, ChoiceQuestion):
        return _validate_choice(question, _require_answer_type(raw, QuestionType.CHOICE))
    if isinstance(question, ScoreQuestion):
        return _validate_score(question, _require_answer_type(raw, QuestionType.SCORE))
    if isinstance(question, NoulQuestion):
        return _validate_noul(question, _require_answer_type(raw, QuestionType.NOUL))
    raise ValidationError(FailReason.ANSWER_INVALID, "unknown_question_type")


def validate_answer_set(
    questions: Mapping[str, Question], raw_answers: Any
) -> dict[str, Answer]:
    """Validate the whole answer set. The set of answer ids must equal the set of
    question ids exactly, or the whole set fails (spec stories 25, 33)."""
    if not isinstance(raw_answers, dict):
        raise ValidationError(FailReason.RESPONSE_MALFORMED, "answers_not_object")
    if set(raw_answers.keys()) != set(questions.keys()):
        raise ValidationError(FailReason.ANSWER_SET_MISMATCH, "answer_ids_mismatch")
    return {qid: validate_answer(questions[qid], raw_answers[qid]) for qid in questions}
