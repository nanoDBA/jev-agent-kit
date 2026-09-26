"""Threshold registry loading (spec stories 44 to 46, 71, 72, 79a).

Parses a registry JSON file into a mapping from question fingerprint to
``routing.RegistryEntry``, validating every field before it reaches routing. A missing
registry file is not an error: the caller treats every question as uncalibrated (story
72). A present but unreadable or malformed file is always a configuration failure.

Strict JSON rules applied on read (story 71): duplicate object keys are rejected rather
than silently keeping the last one, and non-finite numbers (``NaN``, ``Infinity``,
``-Infinity``) are rejected. Registry-supplied strings (escalation target, evidence
reference) are checked against a safe pattern at load time (story 79a), and booleans are
rejected wherever a number is expected, matching the rest of the kit (story 35a).

Every failure raises ``jev_kit.errors.ValidationError(FailReason.CONFIG, <check>)`` with
a short static check name; the offending value is never echoed. Threshold-shape checks
(bound ranges, ordering, non-overlapping score intervals) are left to the routing
dataclasses' own ``__post_init__``, which already raises the same error type.

A calibrated entry must also carry a ``date`` field, an ISO calendar date (story 72);
uncalibrated and never-auto-accept entries do not require one. Score interval labels are
checked against a safe pattern at load time, alongside escalation target and evidence
reference (story 79a, finding C08).
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from jev_kit.egress import scan_text
from jev_kit.errors import FailReason, ValidationError
from jev_kit.routing import (
    ChoiceThreshold,
    NoulThreshold,
    RegistryEntry,
    ScoreInterval,
    ScoreThreshold,
    Threshold,
    ThresholdStatus,
)

SCHEMA_VERSION = 1

# Fingerprints are hex SHA-256 digests (spec stories 69 to 71).
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")

# Registry-supplied labels: bounded, plain, no separators that could carry structure
# (spec story 79a).
_SAFE_STRING_RE = re.compile(r"^[A-Za-z0-9 ._:-]{1,200}$")

# Score interval labels: same idea, a bit shorter and allowing "+" and "/" for things like
# "pass/fail" or "A+" (spec story 79a, finding C08). No control characters, no newlines.
_LABEL_RE = re.compile(r"^[A-Za-z0-9 ._:+/-]{1,64}$")

# Calibration date (spec story 72): a plain ISO calendar date, not validated as a real
# calendar day (that is not this loader's job, just shape).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_STATUS_BY_VALUE = {status.value: status for status in ThresholdStatus}

_ENTRY_REQUIRED_KEYS = {"status", "escalation_target", "evidence_ref"}
_ENTRY_OPTIONAL_KEYS = {"type", "threshold", "date"}
_ENTRY_ALLOWED_KEYS = _ENTRY_REQUIRED_KEYS | _ENTRY_OPTIONAL_KEYS


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a dict from key/value pairs, rejecting duplicate keys.

    Python's json module silently keeps the last of a duplicate key by default; this
    hook turns that into a configuration failure instead (spec story 71).
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(FailReason.CONFIG, "registry_duplicate_key")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> float:
    """parse_constant hook: called for the tokens NaN, Infinity and -Infinity."""
    raise ValidationError(FailReason.CONFIG, "registry_non_finite_number")


def _parse_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_pairs_hook,
            parse_constant=_reject_non_finite,
        )
    except json.JSONDecodeError:
        raise ValidationError(FailReason.CONFIG, "registry_not_json") from None


def _require_object(value: Any, check: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(FailReason.CONFIG, check)
    return value


def _require_number(value: Any, check: str) -> float:
    """Return value as a finite float. Rejects bool and any non-numeric value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(FailReason.CONFIG, check)
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(FailReason.CONFIG, check)
    return number


def _reject_secret_shaped(value: str, check: str) -> None:
    # A charset-safe operator-supplied string (a label, escalation target, evidence ref) is
    # still copied verbatim into records and durable receipts, so a secret shape in it (an AWS
    # key, a JWT) is refused at load rather than persisted (finding H6/C08, story 79a).
    if scan_text(value) is not None:
        raise ValidationError(FailReason.CONFIG, check)


def _require_safe_string(value: Any, check: str) -> str:
    if not isinstance(value, str) or not _SAFE_STRING_RE.fullmatch(value):
        raise ValidationError(FailReason.CONFIG, check)
    _reject_secret_shaped(value, check)
    return value


def _require_fingerprint(key: Any) -> str:
    if not isinstance(key, str) or not _FINGERPRINT_RE.fullmatch(key):
        raise ValidationError(FailReason.CONFIG, "registry_fingerprint_invalid")
    return key


def _require_status(value: Any) -> ThresholdStatus:
    if not isinstance(value, str) or value not in _STATUS_BY_VALUE:
        raise ValidationError(FailReason.CONFIG, "registry_status_invalid")
    return _STATUS_BY_VALUE[value]


def _build_noul_threshold(raw: Any) -> NoulThreshold:
    obj = _require_object(raw, "registry_threshold_not_object")
    if set(obj.keys()) != {"yes_bound", "no_bound"}:
        raise ValidationError(FailReason.CONFIG, "registry_noul_fields")
    return NoulThreshold(
        yes_bound=_require_number(obj["yes_bound"], "registry_noul_yes_bound"),
        no_bound=_require_number(obj["no_bound"], "registry_noul_no_bound"),
    )


def _build_choice_threshold(raw: Any) -> ChoiceThreshold:
    obj = _require_object(raw, "registry_threshold_not_object")
    if set(obj.keys()) != {"min_confidence", "min_margin"}:
        raise ValidationError(FailReason.CONFIG, "registry_choice_fields")
    return ChoiceThreshold(
        min_confidence=_require_number(obj["min_confidence"], "registry_choice_min_confidence"),
        min_margin=_require_number(obj["min_margin"], "registry_choice_min_margin"),
    )


def _build_score_interval(raw: Any) -> ScoreInterval:
    obj = _require_object(raw, "registry_score_interval_not_object")
    if set(obj.keys()) != {"lower", "upper", "label"}:
        raise ValidationError(FailReason.CONFIG, "registry_score_interval_fields")
    lower = _require_number(obj["lower"], "registry_score_interval_lower")
    upper = _require_number(obj["upper"], "registry_score_interval_upper")
    label = obj["label"]
    # Finding C08: a score interval label is free text an operator writes into the
    # registry, but it is copied into records and receipts, so it gets the same safe
    # pattern as escalation_target and evidence_ref (no newlines, no control characters).
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
        raise ValidationError(FailReason.CONFIG, "label_unsafe")
    _reject_secret_shaped(label, "label_secret_shaped")
    return ScoreInterval(lower=lower, upper=upper, label=label)


def _build_score_threshold(raw: Any) -> ScoreThreshold:
    obj = _require_object(raw, "registry_threshold_not_object")
    if set(obj.keys()) != {"min_confidence", "intervals"}:
        raise ValidationError(FailReason.CONFIG, "registry_score_fields")
    intervals_raw = obj["intervals"]
    if not isinstance(intervals_raw, list) or not intervals_raw:
        raise ValidationError(FailReason.CONFIG, "registry_score_intervals_not_list")
    intervals = tuple(_build_score_interval(item) for item in intervals_raw)
    return ScoreThreshold(
        intervals=intervals,
        min_confidence=_require_number(obj["min_confidence"], "registry_score_min_confidence"),
    )


def _build_threshold(question_type: Any, raw: Any) -> Threshold:
    if question_type == "noul":
        return _build_noul_threshold(raw)
    if question_type == "choice":
        return _build_choice_threshold(raw)
    if question_type == "score":
        return _build_score_threshold(raw)
    raise ValidationError(FailReason.CONFIG, "registry_type_invalid")


def _require_calibration_date(obj: dict[str, Any]) -> None:
    """Validate the calibration date on a calibrated entry (spec story 72).

    ``routing.RegistryEntry`` has no ``date`` field of its own (that dataclass belongs to
    another part of the kit), so there is nothing to store the parsed value on here. This
    only checks that the date is present and well-formed; a future ``RegistryEntry`` field
    would be populated from ``date`` right where this function is called.
    """
    date = obj.get("date")
    if date is None:
        raise ValidationError(FailReason.CONFIG, "entry_no_date")
    if not isinstance(date, str) or not _DATE_RE.fullmatch(date):
        raise ValidationError(FailReason.CONFIG, "entry_bad_date")


def _build_entry(raw: Any) -> RegistryEntry:
    obj = _require_object(raw, "registry_entry_not_object")
    unknown = set(obj.keys()) - _ENTRY_ALLOWED_KEYS
    if unknown:
        raise ValidationError(FailReason.CONFIG, "registry_entry_unknown_field")
    missing = _ENTRY_REQUIRED_KEYS - set(obj.keys())
    if missing:
        raise ValidationError(FailReason.CONFIG, "registry_entry_missing_field")

    status = _require_status(obj["status"])
    escalation_target = _require_safe_string(
        obj["escalation_target"], "registry_escalation_target"
    )
    evidence_ref = _require_safe_string(obj["evidence_ref"], "registry_evidence_ref")

    threshold: Threshold | None = None
    if status is ThresholdStatus.CALIBRATED:
        if "type" not in obj or "threshold" not in obj:
            raise ValidationError(FailReason.CONFIG, "registry_calibrated_missing_threshold")
        threshold = _build_threshold(obj["type"], obj["threshold"])
        # Uncalibrated and never_auto_accept entries do not require a date (story 72).
        _require_calibration_date(obj)
    elif "date" in obj and (
        not isinstance(obj["date"], str) or not _DATE_RE.fullmatch(obj["date"])
    ):
        # A date is optional here, but if one was written, keep it honest.
        raise ValidationError(FailReason.CONFIG, "entry_bad_date")

    return RegistryEntry(
        status=status,
        escalation_target=escalation_target,
        evidence_ref=evidence_ref,
        threshold=threshold,
    )


def loads_registry(text: str) -> dict[str, RegistryEntry]:
    """Parse registry JSON text into a fingerprint-to-RegistryEntry mapping.

    Raises ``ValidationError(FailReason.CONFIG, ...)`` on any malformed input. Never
    echoes offending values (spec stories 63, 79a).
    """
    data = _parse_json(text)
    root = _require_object(data, "registry_root_not_object")

    unknown_top = set(root.keys()) - {"schema_version", "entries"}
    if unknown_top:
        raise ValidationError(FailReason.CONFIG, "registry_unknown_field")

    schema_version = root.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise ValidationError(FailReason.CONFIG, "registry_schema_version")

    entries_raw = root.get("entries")
    if not isinstance(entries_raw, dict):
        raise ValidationError(FailReason.CONFIG, "registry_entries_not_object")

    entries: dict[str, RegistryEntry] = {}
    for key, value in entries_raw.items():
        fingerprint = _require_fingerprint(key)
        entries[fingerprint] = _build_entry(value)
    return entries


def load_registry(path: str | os.PathLike[str]) -> dict[str, RegistryEntry]:
    """Load and validate a registry file.

    A missing file returns an empty mapping: every question is treated as uncalibrated
    by the caller (spec story 72). A present but unreadable or malformed file is a
    configuration failure.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValidationError(FailReason.CONFIG, "registry_unreadable") from exc
    return loads_registry(text)


def resolve_registry_path(
    explicit: str | os.PathLike[str] | None,
) -> str | os.PathLike[str] | None:
    """Resolve which registry path to load (spec story 72, finding C19).

    An explicit path always wins. Otherwise, ``JEV_KIT_REGISTRY`` is used when it is set to
    a non-empty string. Otherwise ``None``: the caller should treat that as "no registry"
    and get an empty mapping from ``load_registry`` (or skip calling it), so every question
    is uncalibrated. The engine's config building should call this before deciding whether
    to call ``load_registry`` at all.
    """
    if explicit is not None:
        return explicit
    from_env = os.environ.get("JEV_KIT_REGISTRY")
    if isinstance(from_env, str) and from_env:
        return from_env
    return None
