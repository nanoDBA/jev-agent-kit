"""Load a reviewed question-set file into questions, a state schema, and egress contracts.

Spec stories 6, 39, 40, 69, 70. The file holds static question text (never per-call data),
the declared state field schema, the pinned model, the escalation target, declared excluded
classes, and transcript settings. Parsing rejects duplicate keys and non-finite numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_kit.egress import EGRESS_TRANSFORM_DIGEST, ContentKind, FieldSpec, scan_text
from jev_kit.errors import FailReason, ValidationError
from jev_kit.fingerprint import parse_canonical
from jev_kit.log_templates import (
    LogTemplate,
    check_field_format,
    parse_log_templates,
    templates_contract,
)
from jev_kit.types import (
    ChoiceQuestion,
    ConsequenceClass,
    NoulQuestion,
    Question,
    ScoreQuestion,
)

EGRESS_POLICY_VERSION = 1
_SAFE_QID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


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
    log_templates: dict[str, LogTemplate] = field(default_factory=dict)  # ADR 0004


def _require(obj: dict[str, Any], key: str, check: str) -> Any:
    if key not in obj:
        raise ValidationError(FailReason.CONFIG, check)
    return obj[key]


def _consequence(value: Any) -> ConsequenceClass:
    if value not in (ConsequenceClass.ADVISORY.value, ConsequenceClass.GATE.value):
        raise ValidationError(FailReason.CONFIG, "bad_consequence")
    return ConsequenceClass(value)


# The reviewed question-set shape (spec, finding C12): each question holds the API fields
# (type, instructions, criteria) plus a separate kit object carrying the consequence class.
# Legacy top-level shortcut fields are rejected so meaning cannot silently change.
_LEGACY_FIELDS = ("consequence", "options", "levels")


def _build_question(qid: str, spec: dict[str, Any]) -> Question:
    if not isinstance(spec, dict):
        raise ValidationError(FailReason.CONFIG, "question_not_object")
    for legacy in _LEGACY_FIELDS:
        if legacy in spec:
            raise ValidationError(FailReason.CONFIG, "question_legacy_field")
    # Reject unknown fields at every nested level so an injected key cannot ride along into a
    # loaded question that then produces evidence (finding H18).
    if not set(spec) <= {"type", "instructions", "criteria", "kit"}:
        raise ValidationError(FailReason.CONFIG, "question_unknown_field")
    qtype = _require(spec, "type", "question_no_type")
    instructions = _require(spec, "instructions", "question_no_instructions")
    if not isinstance(instructions, str) or not instructions:
        raise ValidationError(FailReason.CONFIG, "question_instructions")
    kit = _require(spec, "kit", "question_no_kit")
    if not isinstance(kit, dict):
        raise ValidationError(FailReason.CONFIG, "question_kit_type")
    if not set(kit) <= {"consequence", "gate"}:
        raise ValidationError(FailReason.CONFIG, "kit_unknown_field")
    consequence = _consequence(_require(kit, "consequence", "question_no_consequence"))
    criteria = spec.get("criteria")
    is_gate = consequence is ConsequenceClass.GATE

    if qtype == "noul":
        # Noul criteria, when present, is a {true, false} description object. Its keys are
        # restricted to that pair so an unknown key cannot ride along (finding H18).
        if criteria is not None and not isinstance(criteria, dict):
            raise ValidationError(FailReason.CONFIG, "noul_criteria_type")
        if isinstance(criteria, dict) and not set(criteria) <= {"true", "false"}:
            raise ValidationError(FailReason.CONFIG, "noul_criteria_unknown_key")
        allow = _gate_allow_labels(kit, is_gate, frozenset({"yes", "no"}))
        return NoulQuestion(
            qid, instructions, consequence, criteria=criteria, gate_allow_labels=allow
        )
    if qtype == "choice":
        # Choice criteria is the option map: option key -> description (or null).
        if not isinstance(criteria, dict) or not criteria:
            raise ValidationError(FailReason.CONFIG, "choice_criteria_type")
        options = tuple(criteria.keys())
        if not all(isinstance(o, str) for o in options):
            raise ValidationError(FailReason.CONFIG, "choice_option_key_type")
        if not 2 <= len(options) <= 255:
            raise ValidationError(FailReason.CONFIG, "choice_options_count")
        allow = _gate_allow_labels(kit, is_gate, frozenset(options))
        return ChoiceQuestion(
            qid,
            instructions,
            options,
            consequence,
            criteria=dict(criteria),
            gate_allow_labels=allow,
        )
    if qtype == "score":
        # Score criteria is the ordered array of level descriptions.
        if not isinstance(criteria, list) or not all(isinstance(x, str) for x in criteria):
            raise ValidationError(FailReason.CONFIG, "score_criteria_type")
        if not 2 <= len(criteria) <= 10:
            raise ValidationError(FailReason.CONFIG, "score_levels_count")
        # Score interval labels live in the registry threshold, so the subset is validated
        # there, not here; a gate Score still requires the field to be present.
        allow = _gate_allow_labels(kit, is_gate, None)
        return ScoreQuestion(
            qid, instructions, tuple(criteria), consequence, gate_allow_labels=allow
        )
    raise ValidationError(FailReason.CONFIG, "unknown_question_type")


def _gate_allow_labels(
    kit: dict[str, Any], is_gate: bool, valid: frozenset[str] | None
) -> frozenset[str] | None:
    """Parse kit.gate.allow_labels: the labels a gate answer may map to ALLOW (finding H1).

    Required for a gate question (fail closed: a gate with no allow-list can never ship). An
    advisory question has none. When ``valid`` is given, every label must be one of it.
    """
    gate = kit.get("gate")
    if not is_gate:
        if gate is not None:
            raise ValidationError(FailReason.CONFIG, "gate_block_on_advisory")
        return None
    if not isinstance(gate, dict):
        raise ValidationError(FailReason.CONFIG, "gate_no_allow_labels")
    if not set(gate) <= {"allow_labels"}:
        raise ValidationError(FailReason.CONFIG, "gate_unknown_field")
    labels = gate.get("allow_labels")
    if not isinstance(labels, list) or not labels or not all(isinstance(x, str) for x in labels):
        raise ValidationError(FailReason.CONFIG, "gate_allow_labels_type")
    if valid is not None and not set(labels) <= valid:
        raise ValidationError(FailReason.CONFIG, "gate_allow_labels_unknown")
    for label in labels:
        # allow_labels are copied into records verbatim, so a secret-shaped label is refused at
        # load rather than persisted (finding H6/C08).
        if scan_text(label) is not None:
            raise ValidationError(FailReason.CONFIG, "gate_allow_labels_secret_shaped")
    return frozenset(labels)


# The parameter keys each content kind recognizes; a kind not listed takes no params. An
# undeclared params key is rejected rather than silently ignored (finding H18).
_ALLOWED_FIELD_PARAMS: dict[str, frozenset[str]] = {
    ContentKind.CODE.value: frozenset({"language"}),
    ContentKind.FREE_TEXT.value: frozenset({"source_type"}),
    ContentKind.TRANSCRIPT.value: frozenset({"source_type"}),
    # ADR 0004: {"format": "template"} opts a log field into declared templates.
    ContentKind.LOG.value: frozenset({"format"}),
}


def _build_field(spec: Any) -> FieldSpec:
    if not isinstance(spec, dict):
        raise ValidationError(FailReason.CONFIG, "field_not_object")
    if not set(spec) <= {"kind", "params"}:
        raise ValidationError(FailReason.CONFIG, "field_unknown_field")
    kind = _require(spec, "kind", "field_no_kind")
    if kind not in {k.value for k in ContentKind}:
        raise ValidationError(FailReason.CONFIG, "field_unknown_kind")
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValidationError(FailReason.CONFIG, "field_params_type")
    if not set(params) <= _ALLOWED_FIELD_PARAMS.get(kind, frozenset()):
        raise ValidationError(FailReason.CONFIG, "field_params_unknown")
    return FieldSpec(ContentKind(kind), params)


_QS_ALLOWED_FIELDS = frozenset(
    {
        "schema_version", "id", "version", "model", "escalation_target",
        "questions", "state_schema", "transcripts", "excluded_classes",
        "log_templates",
    }
)


def _parse(obj: Any) -> QuestionSet:
    if not isinstance(obj, dict):
        raise ValidationError(FailReason.CONFIG, "questionset_not_object")
    # schema_version must be exactly the integer 1: True == 1 and 1.0 == 1 in Python, so a bare
    # inequality would accept a boolean or float (finding H18).
    if type(obj.get("schema_version")) is not int or obj.get("schema_version") != 1:
        raise ValidationError(FailReason.CONFIG, "questionset_schema_version")
    # Reject any undeclared top-level field so a typo or injected key cannot ride along into a
    # loaded set that then produces evidence (finding H18).
    if not set(obj.keys()) <= _QS_ALLOWED_FIELDS:
        raise ValidationError(FailReason.CONFIG, "questionset_unknown_field")
    raw_questions = _require(obj, "questions", "no_questions")
    if not isinstance(raw_questions, dict) or not raw_questions:
        raise ValidationError(FailReason.CONFIG, "questions_type")
    for qid in raw_questions:
        # Question ids reach records and receipts verbatim, so they must be safe identifiers,
        # never a channel for secret-shaped strings (finding H6/C08).
        if not isinstance(qid, str) or not _SAFE_QID.match(qid):
            raise ValidationError(FailReason.CONFIG, "question_id_unsafe")
        if scan_text(qid) is not None:
            # A charset-safe id can still be a credential shape (AWS/GitHub token); it would
            # land verbatim in a durable receipt, so reject it at load (finding H6 MAJOR-1).
            raise ValidationError(FailReason.CONFIG, "question_id_secret_shaped")
    questions = {qid: _build_question(qid, spec) for qid, spec in raw_questions.items()}

    raw_schema = obj.get("state_schema", {})
    if not isinstance(raw_schema, dict):
        raise ValidationError(FailReason.CONFIG, "schema_type")
    schema = {name: _build_field(spec) for name, spec in raw_schema.items()}
    log_templates = (
        parse_log_templates(obj["log_templates"]) if "log_templates" in obj else {}
    )
    for spec in schema.values():
        check_field_format(spec, log_templates)

    transcripts = obj.get("transcripts", {})
    if not isinstance(transcripts, dict):
        raise ValidationError(FailReason.CONFIG, "transcripts_type")
    if not set(transcripts) <= {"enabled", "cap"}:
        raise ValidationError(FailReason.CONFIG, "transcripts_unknown_field")
    enabled = transcripts.get("enabled", False)
    # A real boolean only: the string "false" is truthy and would silently enable (finding H5).
    if not isinstance(enabled, bool):
        raise ValidationError(FailReason.CONFIG, "transcripts_enabled_type")
    cap = transcripts.get("cap", 2000)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValidationError(FailReason.CONFIG, "transcripts_cap_type")
    excluded = obj.get("excluded_classes", [])
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise ValidationError(FailReason.CONFIG, "excluded_classes_type")

    model = _require(obj, "model", "no_model")
    escalation = _require(obj, "escalation_target", "no_escalation_target")
    set_id = _require(obj, "id", "no_id")
    # version must be a real string, not coerced: str(True) or str(1.0) would silently accept a
    # boolean or float and change the identity a fingerprint binds (finding H18).
    version = _require(obj, "version", "no_version")
    checks = (
        (model, "model_type"), (escalation, "escalation_type"),
        (set_id, "id_type"), (version, "version_type"),
    )
    for value, check in checks:
        if not isinstance(value, str) or not value:
            raise ValidationError(FailReason.CONFIG, check)
    # The id, version, model and escalation target all land verbatim in durable receipts, so a
    # secret-shaped value in any of them is refused at load rather than persisted (finding H6).
    for value in (set_id, version, model, escalation):
        if scan_text(value) is not None:
            raise ValidationError(FailReason.CONFIG, "questionset_metadata_secret_shaped")

    return QuestionSet(
        set_id=set_id,
        version=version,
        model=model,
        escalation_target=escalation,
        questions=questions,
        schema=schema,
        excluded_classes=frozenset(excluded),
        transcripts_enabled=enabled,
        transcripts_cap=cap,
        raw=obj,
        log_templates=log_templates,
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
    contract: dict[str, Any] = {
        "policy_version": EGRESS_POLICY_VERSION,
        # A digest of the actual transform/detector behavior, so a change to how state is reduced
        # (e.g. command reduction) invalidates prior calibration automatically (finding H3).
        "transform_digest": EGRESS_TRANSFORM_DIGEST,
        "schema": {
            name: {"kind": spec.kind.value, "params": dict(spec.params)}
            for name, spec in sorted(qset.schema.items())
        },
        "transcripts": {"enabled": qset.transcripts_enabled, "cap": qset.transcripts_cap},
        "excluded_classes": sorted(qset.excluded_classes),
    }
    # Declared log templates change what the model sees, so they are bound here (ADR 0004). The
    # key is added only when templates exist, so sets without templates keep their fingerprints.
    if qset.log_templates:
        contract["log_templates"] = templates_contract(qset.log_templates)
    return contract
