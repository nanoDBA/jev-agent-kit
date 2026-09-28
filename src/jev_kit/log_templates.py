"""Declared log templates: structured log records rendered from question-set templates.

ADR 0004 (issue jak-lck). A question set may declare ``log_templates``; a ``log`` field with
``params.format == "template"`` then accepts only structured records
``{"level", "template_id", "params"}``. Template words survive because they are literals
authored in the reviewed question set, never runtime data. Each param is transformed by its
declared slot kind. Anything unexpected blocks the request (fail closed); nothing is passed
through raw. Free-form ``log`` fields keep the reduction in ``egress.py`` unchanged.

This module is deliberately separate from ``egress.py``: editing that module would change
``EGRESS_TRANSFORM_DIGEST`` and so every fingerprint. This module's own source digest is bound
into the egress contract only for question sets that declare templates.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_kit.egress import ContentKind, EgressContext, FieldSpec, scan_text, transform_state
from jev_kit.errors import FailReason, ValidationError

TEMPLATE_FORMAT = "template"

MAX_TEMPLATES = 256
MAX_TEMPLATE_TEXT = 240
MAX_SLOTS = 16
MAX_ENUM_VALUES = 64
MAX_RECORDS = 20
MAX_PARAM_CHARS = 256

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,31})\}")
_SLOT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
# Literal template text between slots. No braces (so no stray or nested slot syntax), no
# newlines or control characters, no quotes other than the apostrophe.
_LITERAL = re.compile(r"^[A-Za-z0-9 .,:;()\[\]/_+#%='-]*$")

# Slot kinds: existing egress kinds reused through transform_state, plus a declared enum.
_REUSED_KINDS = {
    "identifier": ContentKind.IDENTIFIER,
    "contact": ContentKind.CONTACT,
    "path": ContentKind.PATH,
    "command": ContentKind.COMMAND,
    "metric": ContentKind.METRIC,
}
_ENUM = "enum"

# A template metric slot is numbers only (owner decision on PR #16): a free-text metric slot
# would let an author pass unmasked text past kind-based masking. Text belongs in an enum slot.
_NUMERIC = re.compile(r"^[+-]?[0-9]{1,32}(?:\.[0-9]{1,32})?$")

LEVELS = frozenset(
    {"trace", "debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"}
)


@dataclass(frozen=True)
class Slot:
    kind: str
    values: tuple[str, ...] = ()  # enum only


@dataclass(frozen=True)
class LogTemplate:
    template_id: str
    text: str
    pieces: tuple[str | None, ...]  # literal strings; None marks a slot, in order
    slot_order: tuple[str, ...]
    slots: Mapping[str, Slot]

    def contract(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "params": {
                name: {"kind": s.kind, **({"values": list(s.values)} if s.kind == _ENUM else {})}
                for name, s in sorted(self.slots.items())
            },
        }


def _config(check: str) -> ValidationError:
    return ValidationError(FailReason.CONFIG, check)


def _blocked(check: str) -> ValidationError:
    return ValidationError(FailReason.EGRESS_BLOCKED, check)


# --------------------------------------------------------------------------- loading


def _parse_slot(spec: Any) -> Slot:
    if not isinstance(spec, dict):
        raise _config("log_template_slot_not_object")
    kind = spec.get("kind")
    if kind == _ENUM:
        if not set(spec) <= {"kind", "values"}:
            raise _config("log_template_slot_unknown_field")
        values = spec.get("values")
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= MAX_ENUM_VALUES
            or not all(isinstance(v, str) and _SAFE_ID.match(v) for v in values)
            or len(set(values)) != len(values)
        ):
            raise _config("log_template_enum_values")
        if any(scan_text(v) is not None for v in values):
            raise _config("log_template_secret_shaped")
        return Slot(_ENUM, tuple(values))
    if kind in _REUSED_KINDS:
        if set(spec) != {"kind"}:
            raise _config("log_template_slot_unknown_field")
        return Slot(kind)
    raise _config("log_template_slot_kind")


def _parse_template(template_id: Any, spec: Any) -> LogTemplate:
    if not isinstance(template_id, str) or not _SAFE_ID.match(template_id):
        raise _config("log_template_id_unsafe")
    if scan_text(template_id) is not None:
        raise _config("log_template_secret_shaped")
    if not isinstance(spec, dict) or not set(spec) <= {"text", "params"} or "text" not in spec:
        raise _config("log_template_shape")
    text = spec["text"]
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEMPLATE_TEXT:
        raise _config("log_template_text")
    raw_params = spec.get("params", {})
    if not isinstance(raw_params, dict) or len(raw_params) > MAX_SLOTS:
        raise _config("log_template_params")
    for name in raw_params:
        if not isinstance(name, str) or not _SLOT_NAME.match(name):
            raise _config("log_template_slot_name")
    slots = {name: _parse_slot(s) for name, s in raw_params.items()}

    pieces: list[str | None] = []
    order: list[str] = []
    pos = 0
    for match in _SLOT.finditer(text):
        pieces.append(text[pos : match.start()])
        pieces.append(None)
        order.append(match.group(1))
        pos = match.end()
    pieces.append(text[pos:])
    for literal in pieces:
        if literal is not None and not _LITERAL.match(literal):
            raise _config("log_template_text_charset")
    # Each declared slot appears exactly once, and no undeclared slot appears.
    if len(order) != len(set(order)) or set(order) != set(slots):
        raise _config("log_template_slots_mismatch")
    if scan_text(text) is not None:
        raise _config("log_template_secret_shaped")
    return LogTemplate(template_id, text, tuple(pieces), tuple(order), slots)


def parse_log_templates(raw: Any) -> dict[str, LogTemplate]:
    """Parse the question set's ``log_templates`` object. Any defect is a CONFIG failure."""
    if not isinstance(raw, dict) or len(raw) > MAX_TEMPLATES:
        raise _config("log_templates_type")
    return {tid: _parse_template(tid, spec) for tid, spec in raw.items()}


def is_templated(spec: FieldSpec) -> bool:
    return spec.kind is ContentKind.LOG and "format" in spec.params


def check_field_format(spec: FieldSpec, templates: Mapping[str, LogTemplate]) -> None:
    """Validate a log field's ``format`` param at load time."""
    if "format" not in spec.params:
        return
    if spec.params["format"] != TEMPLATE_FORMAT:
        raise _config("log_format_unknown")
    if not templates:
        raise _config("log_format_without_templates")


# --------------------------------------------------------------------------- rendering


def _transform_param(slot: Slot, value: Any, ctx: EgressContext) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list, tuple)):
        raise _blocked("log_param_type")
    if isinstance(value, float) and not math.isfinite(value):
        raise _blocked("log_param_type")
    if isinstance(value, str) and len(value) > MAX_PARAM_CHARS:
        raise _blocked("log_param_too_long")
    if slot.kind == _ENUM:
        if not isinstance(value, str) or value not in slot.values:
            raise _blocked("log_param_enum")
        return value
    if not isinstance(value, (str, int, float)):
        raise _blocked("log_param_type")
    if slot.kind == "metric":
        if isinstance(value, str):
            if not _NUMERIC.match(value):
                raise _blocked("log_param_not_numeric")
            return value
        return str(value)
    kind = _REUSED_KINDS[slot.kind]
    # Reuse the shipped transform for this kind exactly as a state field would get it. It
    # refuses numbers in non-metric kinds and anything the kind cannot reduce.
    out = transform_state({"v": value}, {"v": FieldSpec(kind)}, ctx)["v"]
    if isinstance(out, bool) or not isinstance(out, (str, int, float)):
        raise _blocked("log_param_type")
    rendered = str(out)
    # A rendered value never carries newlines or slot syntax (defense in depth; the metric
    # token charset already excludes them).
    if any(ch in rendered for ch in "{}\r\n") or not rendered.isprintable():
        raise _blocked("log_param_unsafe")
    return rendered


def _render_record(
    record: Any, templates: Mapping[str, LogTemplate], ctx: EgressContext
) -> dict[str, str]:
    if not isinstance(record, Mapping) or set(record) != {"level", "template_id", "params"}:
        raise _blocked("log_record_shape")
    level = record["level"]
    # Levels are matched case-insensitively and always sent lowercased (owner decision, PR #16).
    level = level.lower() if isinstance(level, str) else level
    if not isinstance(level, str) or level not in LEVELS:
        raise _blocked("log_record_level")
    template_id = record["template_id"]
    if not isinstance(template_id, str) or template_id not in templates:
        raise _blocked("log_template_unknown")
    template = templates[template_id]
    params = record["params"]
    if not isinstance(params, Mapping) or set(params) != set(template.slots):
        raise _blocked("log_params_mismatch")
    values = {name: _transform_param(template.slots[name], params[name], ctx) for name in params}
    out: list[str] = []
    slot_iter = iter(template.slot_order)
    for piece in template.pieces:
        out.append(values[next(slot_iter)] if piece is None else piece)
    return {"level": level, "template_id": template_id, "message": "".join(out)}


def render_log_field(
    value: Any, templates: Mapping[str, LogTemplate], ctx: EgressContext
) -> dict[str, str] | list[dict[str, str]]:
    """Render a templated log field: one record, or a list of at most MAX_RECORDS records."""
    if isinstance(value, list):
        if len(value) > MAX_RECORDS:
            raise _blocked("log_records_too_many")
        return [_render_record(item, templates, ctx) for item in value]
    if isinstance(value, Mapping):
        return _render_record(value, templates, ctx)
    # A string here would be free-form text on a field that promised structure: refused.
    raise _blocked("log_record_shape")


def transform_state_with_templates(
    state: Mapping[str, Any],
    schema: Mapping[str, FieldSpec],
    templates: Mapping[str, LogTemplate],
    ctx: EgressContext,
) -> dict[str, Any]:
    """transform_state for the plain fields, plus rendering for templated log fields."""
    plain = {name: spec for name, spec in schema.items() if not is_templated(spec)}
    out = transform_state(state, plain, ctx)
    for name, spec in schema.items():
        if is_templated(spec) and name in state:
            out[name] = render_log_field(state[name], templates, ctx)
    return out


# --------------------------------------------------------------------------- identity


def _module_digest() -> str | None:
    try:
        source = Path(__file__).read_bytes()
    except OSError:
        return None
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "src:" + hashlib.sha256(normalized).hexdigest()[:28]


LOG_TEMPLATE_DIGEST = _module_digest()


def templates_contract(templates: Mapping[str, LogTemplate]) -> dict[str, Any]:
    """The fingerprint contribution of declared templates (ADR 0004)."""
    if LOG_TEMPLATE_DIGEST is None:
        # Without a source digest a behavior change could not invalidate calibration.
        raise _config("log_template_digest_unavailable")
    return {
        "digest": LOG_TEMPLATE_DIGEST,
        "templates": {tid: t.contract() for tid, t in sorted(templates.items())},
    }
