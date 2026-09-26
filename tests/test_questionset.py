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


def _noul_q() -> dict[str, Any]:
    return {"d": {"type": "noul", "instructions": "x", "kit": {"consequence": "advisory"}}}


def test_boolean_version_rejected_h18() -> None:
    # H18: version must be a real string, not coerced; str(True) would silently accept a boolean.
    obj = base(_noul_q())
    obj["version"] = True
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG


def test_float_schema_version_rejected_h18() -> None:
    # H18: schema_version must be exactly int 1; 1.0 == 1 in Python but is not the integer type.
    obj = base(_noul_q())
    obj["schema_version"] = 1.0
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG


def test_unknown_top_level_field_rejected_h18() -> None:
    # H18: an undeclared top-level field must not ride along into a loaded set.
    obj = base(_noul_q())
    obj["surprise"] = "ride-along"
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG


def test_noul_criteria_unknown_key_rejected_h18() -> None:
    # Batch-8 H18: Noul criteria is a {true,false} description object; an extra key is rejected.
    obj = base({"d": {
        "type": "noul", "instructions": "x",
        "criteria": {"true": "y", "false": "n", "unknown": "z"},
        "kit": {"consequence": "advisory"},
    }})
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG
    # The valid {true,false} form still loads.
    ok = base({"d": {
        "type": "noul", "instructions": "x", "criteria": {"true": "y", "false": "n"},
        "kit": {"consequence": "advisory"},
    }})
    assert load_question_set(ok) is not None


def test_signed_password_in_question_set_id_rejected_h15() -> None:
    # Batch-10 H15: question-set metadata reaches receipts verbatim, so a signed-number credential
    # shape in the id is refused at load (no receipt is written for it).
    obj = base(_noul_q())
    obj["id"] = '{"password":-123456}'
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG


@pytest.mark.parametrize(
    "bad_id", ['{"password":["x"]}', '{" password ":"x"," password ":"<redacted>"}']
)
def test_container_or_duplicate_credential_in_id_rejected_h15(bad_id: str) -> None:
    # Batch-11 H15: these shapes must not persist as question-set metadata in any receipt.
    obj = base(_noul_q())
    obj["id"] = bad_id
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG


def test_json_encoded_credential_in_id_rejected_h15() -> None:
    # Batch-12 H15: a double-encoded credential must not persist as question-set metadata.
    import json

    obj = base(_noul_q())
    obj["id"] = json.dumps({"payload": json.dumps({" password ": "opensesame"})})
    with pytest.raises(ValidationError) as exc:
        load_question_set(obj)
    assert exc.value.reason is FailReason.CONFIG
