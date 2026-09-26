"""Question-set shape tests (finding C12): kit.consequence, criteria per type, legacy reject."""

from __future__ import annotations

from typing import Any

import pytest

from jev_kit.engine import _wire_question
from jev_kit.errors import FailReason, ValidationError
from jev_kit.questionset import load_question_set
from jev_kit.types import ChoiceQuestion, ScoreQuestion


def base(questions: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "t",
        "version": "1",
        "model": "jev-1.13.0",
        "escalation_target": "gpt-6",
        "questions": questions,
    }


def test_choice_from_criteria_map() -> None:
    qs = load_question_set(
        base(
            {
                "route": {
                    "type": "choice",
                    "instructions": "Which team?",
                    "criteria": {"billing": "money", "tech": "bugs"},
                    "kit": {"consequence": "advisory"},
                }
            }
        )
    )
    q = qs.questions["route"]
    assert isinstance(q, ChoiceQuestion)
    assert q.options == ("billing", "tech")
    # Choice criteria is sent as the option map.
    assert _wire_question(q)["criteria"] == {"billing": "money", "tech": "bugs"}


def test_score_criteria_is_ordered_array_on_the_wire() -> None:
    qs = load_question_set(
        base(
            {
                "risk": {
                    "type": "score",
                    "instructions": "How risky?",
                    "criteria": ["safe", "review", "unsafe"],
                    "kit": {"consequence": "gate", "gate": {"allow_labels": ["safe"]}},
                }
            }
        )
    )
    q = qs.questions["risk"]
    assert isinstance(q, ScoreQuestion)
    assert q.levels == ("safe", "review", "unsafe")
    assert _wire_question(q)["criteria"] == ["safe", "review", "unsafe"]


def test_legacy_top_level_consequence_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        load_question_set(
            base(
                {
                    "d": {
                        "type": "noul",
                        "instructions": "Destructive?",
                        "consequence": "gate",  # legacy shortcut, must be rejected
                    }
                }
            )
        )
    assert exc.value.reason is FailReason.CONFIG


def test_legacy_options_field_rejected() -> None:
    with pytest.raises(ValidationError):
        load_question_set(
            base(
                {
                    "r": {
                        "type": "choice",
                        "instructions": "Which?",
                        "options": ["a", "b"],  # legacy, must be rejected
                        "kit": {"consequence": "advisory"},
                    }
                }
            )
        )


def test_missing_kit_rejected() -> None:
    with pytest.raises(ValidationError):
        load_question_set(
            base({"d": {"type": "noul", "instructions": "Destructive?"}})
        )


def test_secret_shaped_question_id_rejected() -> None:
    # H6 MAJOR-1: a charset-safe but credential-shaped question id is rejected at load.
    with pytest.raises(ValidationError) as exc:
        load_question_set(
            base({"AKIAIOSFODNN7EXAMPLE": {"type": "noul", "instructions": "x?",
                                          "kit": {"consequence": "advisory"}}})
        )
    assert exc.value.reason is FailReason.CONFIG
