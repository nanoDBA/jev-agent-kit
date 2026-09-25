"""Deny-by-default egress: transform declared state, then detect Tier 1 classes.

ADR 0002 with Amendment 1, spec stories 39 to 54. Two stages:

1. transform_state: build the outgoing state from the question set's declared field schema
   only. Undeclared fields and undeclared nested descendants are dropped. Each declared field
   is transformed by its content kind. An unknown or unsupported transform, or code in a
   language with no profile, blocks the request (never passthrough).
2. scan_request: run the Tier 1 detectors over the exact serialized bytes and over every
   decoded key and string value. Any hit blocks the whole request. The engine sends the exact
   bytes that passed detection; nothing is reserialized afterwards.

Classes that patterns cannot reliably detect (health, GDPR special categories, raw rows,
postal addresses) are handled by policy elsewhere (named source types, declared exclusions),
not here (ADR 0002 Amendment 1).

Phase 1 scope: language profiles are injected (synthetic fixtures in tests; real parsers are
Phase 2). Object keys must be safe identifiers rather than being tokenized. These limits are
recorded in docs/specs/phase-1-client.md.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from jev_kit.errors import FailReason, ValidationError

MAX_DEPTH = 16
MAX_LIST = 200
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class ContentKind(StrEnum):
    METRIC = "metric"  # Tier 3, non-personal: sent as-is
    IDENTIFIER = "identifier"  # HMAC tokenized unless publicly allowlisted
    CONTACT = "contact"  # email, phone, IP: masked
    LOG = "log"  # reduced to codes and templates
    COMMAND = "command"  # command and flag names only
    PATH = "path"  # tokenized
    CODE = "code"  # normalized by a language profile
    FREE_TEXT = "free_text"  # only from a named source type
    TRANSCRIPT = "transcript"  # off unless enabled


# Kinds that may carry personal data, so a live call needs an affirmative DPA (spec story 52).
PERSONAL_KINDS = frozenset(
    {
        ContentKind.IDENTIFIER,  # HMAC tokens stay personal data
        ContentKind.CONTACT,
        ContentKind.LOG,
        ContentKind.COMMAND,
        ContentKind.PATH,
        ContentKind.CODE,
        ContentKind.FREE_TEXT,
        ContentKind.TRANSCRIPT,
    }
)


@dataclass(frozen=True)
class FieldSpec:
    kind: ContentKind
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EgressContext:
    hmac_key: bytes | None = None
    public_names: frozenset[str] = frozenset()
    source_allowlist: frozenset[str] = frozenset()
    language_profiles: Mapping[str, Callable[[str], str]] = field(default_factory=dict)
    transcript_enabled: bool = False
    transcript_cap: int = 2000


# --------------------------------------------------------------------------- transforms


def _hmac_token(value: str, key: bytes) -> str:
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"id_{digest[:16]}"


_CONTACT_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # IPv4
    re.compile(r"\+?\d[\d ()-]{7,}\d"),  # phone
]


def _mask_contacts(text: str) -> str:
    for pattern in _CONTACT_PATTERNS:
        text = pattern.sub("<contact>", text)
    return text


def _reduce_log(text: str) -> str:
    text = re.sub(r"'[^']*'", "<v>", text)
    text = re.sub(r'"[^"]*"', "<v>", text)
    return _mask_contacts(text)


def _reduce_command(text: str) -> str:
    tokens = text.split()
    kept = [tokens[0]] if tokens else []
    kept += [tok for tok in tokens[1:] if tok.startswith("-")]
    return " ".join(kept)


def _transform_scalar(
    kind: ContentKind, value: str, params: Mapping[str, Any], ctx: EgressContext
) -> str:
    if kind is ContentKind.METRIC:
        return value
    if kind is ContentKind.IDENTIFIER:
        if value in ctx.public_names:
            return value
        if ctx.hmac_key is None:
            raise ValidationError(FailReason.CONFIG, "hmac_key_missing")
        return _hmac_token(value, ctx.hmac_key)
    if kind is ContentKind.CONTACT:
        return "<contact>"
    if kind is ContentKind.PATH:
        return "<path>"
    if kind is ContentKind.LOG:
        return _reduce_log(value)
    if kind is ContentKind.COMMAND:
        return _reduce_command(value)
    if kind is ContentKind.CODE:
        language = params.get("language")
        profile = ctx.language_profiles.get(language) if isinstance(language, str) else None
        if profile is None:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "code_no_profile")
        return profile(value)
    if kind is ContentKind.FREE_TEXT:
        source = params.get("source_type")
        if not isinstance(source, str) or source not in ctx.source_allowlist:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "free_text_source_not_allowed")
        return _mask_contacts(value)
    if kind is ContentKind.TRANSCRIPT:
        if not ctx.transcript_enabled:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "transcript_disabled")
        return _mask_contacts(value[: ctx.transcript_cap])
    raise ValidationError(FailReason.EGRESS_BLOCKED, "unknown_kind")


def _transform_value(value: Any, spec: FieldSpec, ctx: EgressContext, depth: int) -> Any:
    if depth > MAX_DEPTH:
        raise ValidationError(FailReason.EGRESS_BLOCKED, "max_depth")
    if value is None:
        return None
    if isinstance(value, str):
        return _transform_scalar(spec.kind, value, spec.params, ctx)
    if isinstance(value, (bool, int, float)):
        if spec.kind is ContentKind.METRIC:
            return value
        raise ValidationError(FailReason.EGRESS_BLOCKED, "number_wrong_kind")
    if isinstance(value, list):
        if len(value) > MAX_LIST:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "list_too_long")
        return [_transform_value(item, spec, ctx, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_LIST:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "object_too_large")
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not _SAFE_KEY.match(key):
                raise ValidationError(FailReason.EGRESS_BLOCKED, "unsafe_object_key")
            out[key] = _transform_value(item, spec, ctx, depth + 1)
        return out
    raise ValidationError(FailReason.EGRESS_BLOCKED, "unsupported_value_type")


def transform_state(
    state: Mapping[str, Any], schema: Mapping[str, FieldSpec], ctx: EgressContext
) -> dict[str, Any]:
    """Build the outgoing state from declared fields only. Undeclared fields are dropped."""
    if not isinstance(state, Mapping):
        raise ValidationError(FailReason.EGRESS_BLOCKED, "state_not_object")
    return {
        name: _transform_value(state[name], schema[name], ctx, 0)
        for name in schema
        if name in state
    }


def personal_kinds_present(schema: Mapping[str, FieldSpec]) -> bool:
    """True if any declared field is of a kind that may carry personal data (spec story 52)."""
    return any(spec.kind in PERSONAL_KINDS for spec in schema.values())


# --------------------------------------------------------------------------- detectors


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_PLACEHOLDER = re.compile(r"^[\*x.\s]+$", re.IGNORECASE)


def _is_placeholder(value: str) -> bool:
    # Redaction artifacts such as ***, xxxx, or an angle-bracket token like <password>.
    if value.startswith("<") and value.endswith(">"):
        return True
    return bool(_PLACEHOLDER.match(value))

_DETECTORS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("azure_sas", re.compile(r"[?&]sig=[A-Za-z0-9%/+]{20,}")),
    ("authorization_header", re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+\S+")),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

_CONNSTR = re.compile(r"(?i)(?:password|pwd)\s*=\s*([^;\"'\s]+)")
_PAN_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def scan_text(text: str) -> str | None:
    """Return the id of the first Tier 1 rule that matches, or None."""
    for rule_id, pattern in _DETECTORS:
        if pattern.search(text):
            return rule_id
    for match in _CONNSTR.finditer(text):
        value = match.group(1)
        if not _is_placeholder(value):
            return "connection_string_password"
    for match in _PAN_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return "payment_card"
    return None


def _walk_strings(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_walk_strings(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_strings(item))
    return found


def scan_request(serialized: bytes, decoded: Any) -> str | None:
    """Scan the exact serialized bytes and every decoded key and string value.

    Scanning the raw text catches secrets that JSON escaping split across lines; scanning
    decoded values catches secrets that escaping would otherwise obscure.
    """
    text_hit = scan_text(serialized.decode("utf-8", errors="replace"))
    if text_hit is not None:
        return text_hit
    for value in _walk_strings(decoded):
        hit = scan_text(value)
        if hit is not None:
            return hit
    return None
