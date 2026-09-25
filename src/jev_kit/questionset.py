"""Load a reviewed question-set file into questions, a state schema, and egress contracts.

Spec stories 6, 39, 40, 69, 70. The file holds static question text (never per-call data),
the declared state field schema, the pinned model, the escalation target, declared excluded
classes, and transcript settings. Parsing rejects duplicate keys and non-finite numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_kit.egress import ContentKind, FieldSpec
from jev_kit.errors import FailReason, ValidationError
from jev_kit.fingerprint import parse_canonical
from jev_kit.types import (
    ChoiceQuestion,
    ConsequenceClass,
    NoulQuestion,
    Question,
    ScoreQuestion,
)

EGRESS_POLICY_VERSION = 1


@dataclass(frozen=True)
class QuestionSet:
    set_id: str
    version: str
    model: str
    escalation_target: str
    questions: dict[str, Question]
    schema: dict[str, FieldSpec]
    excluded_classes: frozenset[str]
    transcripts_enabled: bool
    transcripts_cap: int
    raw: dict[str, Any]  # the whole file, for the set digest


def _require(obj: dict[str, Any], key: str, check: str) -> Any:
    if key not in obj:
        raise ValidationError(FailReason.CONFIG, check)
    return obj[key]


def _consequence(value: Any) -> ConsequenceClass:
    if value not in (ConsequenceClass.ADVISORY.value, ConsequenceClass.GATE.value):
        raise ValidationError(FailReason.CONFIG, "bad_consequence")
    return ConsequenceClass(value)


def _build_question(qid: str, spec: dict[str, Any]) -> Question:
    if not isinstance(spec, dict):
        raise ValidationError(FailReason.CONFIG, "question_not_object")
    qtype = _require(spec, "type", "question_no_type")
    instructions = _require(spec, "instructions", "question_no_instructions")
    if not isinstance(instructions, str) or not instructions:
        raise ValidationError(FailReason.CONFIG, "question_instructions")
    consequence = _consequence(_require(spec, "consequence", "question_no_consequence"))
    if qtype == "noul":
        return NoulQuestion(qid, instructions, consequence)
    if qtype == "choice":
        options = _require(spec, "options", "choice_no_options")
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise ValidationError(FailReason.CONFIG, "choice_options_type")
        if not 2 <= len(options) <= 255 or len(set(options)) != len(options):
            raise ValidationError(FailReason.CONFIG, "choice_options_count")
        return ChoiceQuestion(qid, instructions, tuple(options), consequence)
    if qtype == "score":
        levels = _require(spec, "levels", "score_no_levels")
        if not isinstance(levels, list) or not all(isinstance(x, str) for x in levels):
            raise ValidationError(FailReason.CONFIG, "score_levels_type")
        if not 2 <= len(levels) <= 10:
            raise ValidationError(FailReason.CONFIG, "score_levels_count")
        return ScoreQuestion(qid, instructions, tuple(levels), consequence)
    raise ValidationError(FailReason.CONFIG, "unknown_question_type")


def _build_field(spec: Any) -> FieldSpec:
    if not isinstance(spec, dict):
        raise ValidationError(FailReason.CONFIG, "field_not_object")
    kind = _require(spec, "kind", "field_no_kind")
    if kind not in {k.value for k in ContentKind}:
        raise ValidationError(FailReason.CONFIG, "field_unknown_kind")
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValidationError(FailReason.CONFIG, "field_params_type")
    return FieldSpec(ContentKind(kind), params)


def _parse(obj: Any) -> QuestionSet:
    if not isinstance(obj, dict):
        raise ValidationError(FailReason.CONFIG, "questionset_not_object")
    if obj.get("schema_version") != 1:
        raise ValidationError(FailReason.CONFIG, "questionset_schema_version")
    raw_questions = _require(obj, "questions", "no_questions")
    if not isinstance(raw_questions, dict) or not raw_questions:
        raise ValidationError(FailReason.CONFIG, "questions_type")
    questions = {qid: _build_question(qid, spec) for qid, spec in raw_questions.items()}

    raw_schema = obj.get("state_schema", {})
    if not isinstance(raw_schema, dict):
        raise ValidationError(FailReason.CONFIG, "schema_type")
    schema = {name: _build_field(spec) for name, spec in raw_schema.items()}

    transcripts = obj.get("transcripts", {})
    if not isinstance(transcripts, dict):
        raise ValidationError(FailReason.CONFIG, "transcripts_type")
    excluded = obj.get("excluded_classes", [])
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise ValidationError(FailReason.CONFIG, "excluded_classes_type")

    model = _require(obj, "model", "no_model")
    escalation = _require(obj, "escalation_target", "no_escalation_target")
    set_id = _require(obj, "id", "no_id")
    version = str(_require(obj, "version", "no_version"))
    checks = ((model, "model_type"), (escalation, "escalation_type"), (set_id, "id_type"))
    for value, check in checks:
        if not isinstance(value, str) or not value:
            raise ValidationError(FailReason.CONFIG, check)

    return QuestionSet(
        set_id=set_id,
        version=version,
        model=model,
        escalation_target=escalation,
        questions=questions,
        schema=schema,
        excluded_classes=frozenset(excluded),
        transcripts_enabled=bool(transcripts.get("enabled", False)),
        transcripts_cap=int(transcripts.get("cap", 2000)),
        raw=obj,
    )


def load_question_set(obj: dict[str, Any]) -> QuestionSet:
    """Build a QuestionSet from an already-parsed inline object."""
    return _parse(obj)


def load_question_set_file(path: str | Path) -> QuestionSet:
    """Read and parse a question-set file. A read error or malformed file is a CONFIG failure."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(FailReason.CONFIG, "questionset_unreadable") from exc
    try:
        obj = parse_canonical(text)
    except ValueError as exc:
        raise ValidationError(FailReason.CONFIG, "questionset_bad_json") from exc
    return _parse(obj)


def egress_contract(qset: QuestionSet) -> dict[str, Any]:
    """The effective egress contract hashed into every question fingerprint (story 69)."""
    return {
        "policy_version": EGRESS_POLICY_VERSION,
        "schema": {
            name: {"kind": spec.kind.value, "params": dict(spec.params)}
            for name, spec in sorted(qset.schema.items())
        },
        "transcripts": {"enabled": qset.transcripts_enabled, "cap": qset.transcripts_cap},
        "excluded_classes": sorted(qset.excluded_classes),
    }
