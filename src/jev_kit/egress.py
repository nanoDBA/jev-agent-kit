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
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from jev_kit.errors import FailReason, ValidationError

MAX_DEPTH = 16
MAX_LIST = 200  # keyed collections
MAX_POSITIONAL_LIST = 20  # positional arrays (finding C13; spec story 33)
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

# Tier 3 metric strings are product/version tokens only (spec story 39 to 40; finding C04):
# never an arbitrary-string or container escape hatch.
_METRIC_TOKEN = re.compile(r"^[A-Za-z0-9 ._:+/-]{0,64}$")


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
    # IPv6, full and "::"-compressed forms alike (finding C05, extended by GATE-03 to cover
    # compressed addresses such as "2001:db8::1234" and "::1", which the previous pattern
    # missed because it required every group to be written out).
    re.compile(
        r"(?<![\w:])(?:"
        r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}"
        r"|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}"
        r"|[0-9A-Fa-f]{1,4}:(?:(?::[0-9A-Fa-f]{1,4}){1,6})"
        r"|:(?:(?::[0-9A-Fa-f]{1,4}){1,7}|:)"
        r")(?![\w:])"
    ),
]


def _mask_contacts(text: str) -> str:
    for pattern in _CONTACT_PATTERNS:
        text = pattern.sub("<contact>", text)
    return text


# Unquoted key=value pairs, e.g. "tenant=acme host=db01" (finding C05). Quoted spans are
# already reduced to <v> above, so this only needs to catch what quoting missed. The value
# side is an identifier token, never a literal 'v', so it is always masked (GATE-03).
_KV_PATTERN = re.compile(r"(?<![\w.-])([A-Za-z_][\w.-]*)=(\S+)")

# A bare identifier with no key= prefix and no quoting, e.g. the hostname in "connect to
# internal-db failed" (finding GATE-03). Logs are reduced to codes and templates, so any
# hyphen-joined slug of two or more alphanumeric segments is masked even when nothing marks
# it as a value.
_BARE_IDENTIFIER = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")

# A standalone high-entropy run (16+ chars of a token/base64/hex charset that is not a plain
# word): a reduced log is codes and templates only, so an opaque token that no named detector
# recognizes is still masked rather than shipped verbatim (finding H17). A run that is only
# letters is left alone here; the bare-identifier and word rules already cover ordinary words.
_LOG_ENTROPY_TOKEN = re.compile(r"(?<![\w/+=])[A-Za-z0-9+/_=-]{16,}(?![\w/+=])")


def _mask_entropy(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.isalpha():
        return token
    return "<v>"


def _reduce_log(text: str) -> str:
    text = re.sub(r"'[^']*'", "<v>", text)
    text = re.sub(r'"[^"]*"', "<v>", text)
    text = _KV_PATTERN.sub(r"\1=<v>", text)
    text = _BARE_IDENTIFIER.sub("<v>", text)
    text = _LOG_ENTROPY_TOKEN.sub(_mask_entropy, text)
    return _mask_contacts(text)


# A leading environment assignment before the command word, e.g. "REVIEW_TOKEN=PRIVATE tool"
# (finding GATE-02). One or more of these are dropped in full, name and value alike.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _reduce_command(text: str) -> str:
    # Keep the command NAME and bare flag names only; nothing else survives (finding GATE-02):
    # - leading "NAME=VALUE" environment assignments are dropped entirely, not just their value
    # - the command word itself is reduced to its basename, dropping any directory path
    # - a long flag ("--tenant=PRIVATE") keeps only its name, the '=value' is stripped
    # - a short flag with an attached value ("-pPRIVATE") keeps only the flag letter
    # - every positional argument token is dropped entirely rather than kept as-is
    #
    # The text is tokenized with shell rules so a quoted secret ("--tenant='a b'") is one token
    # and cannot spill an inner word into the kept output. Unbalanced quotes or other syntax
    # shlex cannot parse are a fail-closed block, never a naive whitespace split (finding H16).
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValidationError(FailReason.EGRESS_BLOCKED, "command_unparseable") from exc
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGNMENT.match(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return ""
    name = tokens[idx].replace("\\", "/").rsplit("/", 1)[-1]
    kept = [name]
    for tok in tokens[idx + 1 :]:
        if tok.startswith("--"):
            kept.append(tok.split("=", 1)[0])
        elif tok.startswith("-") and len(tok) > 1:
            kept.append(tok[:2])
    return " ".join(kept)


def _require_named_source(params: Mapping[str, Any], ctx: EgressContext) -> None:
    """Free text and transcripts share one rule: only from a source named in the allowlist.

    ADR 0002 Amendment 1: the allowlist ships empty, so both are blocked until the owner names
    sources (spec stories 46, 47; finding C05: transcripts previously skipped this check).
    """
    source = params.get("source_type")
    if not isinstance(source, str) or source not in ctx.source_allowlist:
        raise ValidationError(FailReason.EGRESS_BLOCKED, "free_text_source_not_allowed")


def _transform_scalar(
    kind: ContentKind, value: str, params: Mapping[str, Any], ctx: EgressContext
) -> str:
    if kind is ContentKind.METRIC:
        # Tier 3 metric strings are bounded safe tokens (product/version), never an arbitrary
        # string escape (finding C04).
        if not _METRIC_TOKEN.match(value):
            raise ValidationError(FailReason.EGRESS_BLOCKED, "metric_value")
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
        _require_named_source(params, ctx)
        return _mask_contacts(value)
    if kind is ContentKind.TRANSCRIPT:
        # Real bool True only: a truthy non-bool (e.g. the string "false") must not enable
        # transcripts (finding C05). Transcripts also need the same named-source check as free
        # text, then the free-text masking and the size cap.
        if ctx.transcript_enabled is not True:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "transcript_disabled")
        _require_named_source(params, ctx)
        return _mask_contacts(value[: ctx.transcript_cap])
    raise ValidationError(FailReason.EGRESS_BLOCKED, "unknown_kind")


def _transform_value(value: Any, spec: FieldSpec, ctx: EgressContext, depth: int) -> Any:
    if depth > MAX_DEPTH:
        raise ValidationError(FailReason.EGRESS_BLOCKED, "max_depth")
    if value is None:
        return None
    if isinstance(value, str):
        return _transform_scalar(spec.kind, value, spec.params, ctx)
    if isinstance(value, bool):
        # Booleans are never a metric value either (finding C04): reject before the int check,
        # since bool is an int subclass.
        reason = "metric_value" if spec.kind is ContentKind.METRIC else "number_wrong_kind"
        raise ValidationError(FailReason.EGRESS_BLOCKED, reason)
    if isinstance(value, (int, float)):
        if spec.kind is ContentKind.METRIC:
            return value
        raise ValidationError(FailReason.EGRESS_BLOCKED, "number_wrong_kind")
    if isinstance(value, list):
        if spec.kind is ContentKind.METRIC:
            # A nested container is not a metric value, whatever its leaves hold (finding C04).
            raise ValidationError(FailReason.EGRESS_BLOCKED, "metric_value")
        if len(value) > MAX_POSITIONAL_LIST:
            # Positional arrays are capped tighter than keyed collections (finding C13).
            raise ValidationError(FailReason.EGRESS_BLOCKED, "positional_list_too_long")
        # Only a flat collection of scalars is allowed; a nested container would carry
        # undeclared descendants (finding H4/C04). The schema is flat, so there is no path to
        # declare them.
        return [_transform_value(_reject_nested(item), spec, ctx, depth + 1) for item in value]
    if isinstance(value, dict):
        if spec.kind is ContentKind.METRIC:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "metric_value")
        if len(value) > MAX_LIST:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "object_too_large")
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not _SAFE_KEY.match(key):
                raise ValidationError(FailReason.EGRESS_BLOCKED, "unsafe_object_key")
            # A collection key under an IDENTIFIER field is itself an identifier and would leak
            # verbatim otherwise, so it is tokenized on the same terms as a value (finding H4).
            # For other kinds the key is a structural label kept as a safe identifier.
            out_key = (
                _transform_scalar(ContentKind.IDENTIFIER, key, spec.params, ctx)
                if spec.kind is ContentKind.IDENTIFIER
                else key
            )
            out[out_key] = _transform_value(_reject_nested(item), spec, ctx, depth + 1)
        return out
    raise ValidationError(FailReason.EGRESS_BLOCKED, "unsupported_value_type")


def _reject_nested(item: Any) -> Any:
    """A collection element must be a scalar; an undeclared nested container is blocked (H4)."""
    if isinstance(item, (dict, list)):
        raise ValidationError(FailReason.EGRESS_BLOCKED, "undeclared_nested_container")
    return item


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


_PLACEHOLDER = re.compile(r"^[*x.\s]+$", re.IGNORECASE)

# Finding C06: only these documented redaction forms are placeholders. Any other
# angle-bracket token (e.g. a synthetic secret like <synthetic-review-secret>) must still be
# detected, so this set is exact literals, not "any <...>".
_PLACEHOLDER_TOKENS = frozenset({"<redacted>", "<password>", "<secret>", "<pwd>", "<value>"})


def _is_placeholder(value: str) -> bool:
    # Redaction artifacts: all mask characters (***, xxxx, ....), or one of the documented
    # redaction tokens, case-insensitive.
    if _PLACEHOLDER.match(value):
        return True
    return value.lower() in _PLACEHOLDER_TOKENS


_DETECTORS: list[tuple[str, re.Pattern[str]]] = [
    # Source pattern sets: gitleaks (private_key, jwt, aws_key, github_pat), Microsoft Purview
    # (azure_sas, connection strings), PCI DSS v4.0.1 (card patterns). Patterns are
    # reimplemented from documentation, not copied code (ADR 0002).
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),  # gitleaks
    # gitleaks
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),  # gitleaks
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),  # gitleaks / GitHub secret scanning
    ("azure_sas", re.compile(r"[?&]sig=[A-Za-z0-9%/+]{20,}")),  # Purview: Azure SAS/storage key
    # Purview / gitleaks: bearer and basic HTTP Authorization headers (finding C06 extends
    # this from bearer-only to also match Basic).
    ("authorization_header", re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer|basic)\s+\S+")),
    # GATE-04: the same bearer/basic credential shape, but without requiring the literal word
    # "authorization" nearby. JSON serialization puts a quote between the key and its value
    # (e.g. {"authorization": "Basic <b64>"}), which breaks the rule above; scanning each
    # decoded string on its own (scan_request already does this) catches the value directly.
    # The token itself must look substantial (16+ credential-shaped characters) so an ordinary
    # sentence like "Bearer of bad news" or "present your Bearer token" is not flagged.
    (
        "authorization_header",
        re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9\-_.+/]{16,}=*\b"),
    ),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),  # NIST SP 800-122 2.1
    # Credentialed connection URIs, e.g. postgres://user:pass@host (finding C06; Purview
    # "connection string" family extended to URI-style DSNs for postgres/mysql/mongodb).
    (
        "connection_string_uri",
        re.compile(
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://"
            r"[^\s/:@]+:[^\s/:@]+@[^\s/]+"
        ),
    ),
    # PCI DSS v4.0.1 3.3.1: track 1 ("%B<PAN>^<NAME>^<data>?") and track 2
    # (";<PAN>=<data>?") magnetic stripe formats (finding C06).
    (
        "card_track_data",
        re.compile(r"%B\d{12,19}\^[A-Z0-9/. ]{2,26}\^\d{0,30}\??|;\d{12,19}=\d{0,30}\??"),
    ),
]

# Purview "SQL Server connection string": Password=/Pwd= with an unquoted, single- or
# double-quoted value (finding C06 adds quoted-value handling).
_CONNSTR = re.compile(
    r"(?i)(?:password|pwd)\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^;\"'\s]+))"
)
_PAN_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")  # PCI DSS v4.0.1 3.3.1: Luhn-valid PANs


def scan_text(text: str) -> str | None:
    """Return the id of the first Tier 1 rule that matches, or None."""
    for rule_id, pattern in _DETECTORS:
        if pattern.search(text):
            return rule_id
    for match in _CONNSTR.finditer(text):
        value = next((g for g in match.groups() if g is not None), "")
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


# GATE-04: a credential split across a JSON key and value evades every regex above, because no
# single string contains both the auth indicator and the token. These names mark either side of
# that split: a dict key equal to one of them, or a sibling string value equal to one of them
# (e.g. {"h": "Authorization", "v": "<token>"}, where "h"'s value names the header generically).
# Strong indicators name a field whose value IS the secret: a value paired with one of these is
# blocked whatever its shape, because a password or client secret can be short, lowercase, or an
# ordinary word and would slip past the token-shape heuristic (finding H15). Only a documented
# redaction placeholder is exempt.
_STRONG_SECRET_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "secret_key",
        "private_key",
        "access_token",
        "id_token",
        "refresh_token",
        "api_key",
        "apikey",
        "x-api-key",
    }
)

# Weak indicators name a field that often but not always carries a token, so a value there is
# blocked only when it also has a credential shape (finding GATE-04).
_AUTH_INDICATOR_NAMES = _STRONG_SECRET_NAMES | frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "basic",
        "token",
        "credential",
        "credentials",
    }
)

# A plausible opaque credential: long enough, base64/JWT/URL-safe charset, and not all-lowercase
# alphabetic (so an ordinary word or slug used as a field value does not, by shape alone, read
# as a token) unless it carries a separator character typical of encoded tokens.
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9._+/-]{16,}=*$")


def _looks_like_credential(value: str) -> bool:
    if value.strip().lower() in _AUTH_INDICATOR_NAMES or _is_placeholder(value):
        return False
    if not _TOKEN_SHAPE.match(value):
        return False
    has_digit = any(ch.isdigit() for ch in value)
    has_mixed_case = any(ch.isupper() for ch in value) and any(ch.islower() for ch in value)
    has_symbol = any(ch in "._+/-" for ch in value)
    return has_digit or has_mixed_case or has_symbol


def _scan_auth_pairs(obj: Any) -> str | None:
    """Find a secret adjacent to an auth indicator key (findings GATE-04, H15)."""
    if isinstance(obj, dict):
        # A value directly under a strong-secret key is blocked whatever its shape (H15): a
        # short or lowercase password would pass the credential-shape heuristic otherwise.
        for key, value in obj.items():
            if (
                isinstance(key, str)
                and key.strip().lower() in _STRONG_SECRET_NAMES
                and isinstance(value, str)
                and value.strip() != ""
                and not _is_placeholder(value)
            ):
                return "auth_credential"
        has_indicator_key = any(
            isinstance(key, str) and key.strip().lower() in _AUTH_INDICATOR_NAMES
            for key in obj
        )
        has_indicator_value = any(
            isinstance(v, str) and v.strip().lower() in _AUTH_INDICATOR_NAMES
            for v in obj.values()
        )
        if has_indicator_key or has_indicator_value:
            for value in obj.values():
                if isinstance(value, str) and _looks_like_credential(value):
                    return "auth_credential"
        for value in obj.values():
            hit = _scan_auth_pairs(value)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _scan_auth_pairs(item)
            if hit is not None:
                return hit
    return None


def scan_request(serialized: bytes, decoded: Any) -> str | None:
    """Scan the exact serialized bytes and every decoded key and string value.

    Scanning the raw text catches secrets that JSON escaping split across lines; scanning
    decoded values catches secrets that escaping would otherwise obscure. A final structural
    pass catches a credential split across a JSON key and its value, which no string-only scan
    can see (finding GATE-04).
    """
    text_hit = scan_text(serialized.decode("utf-8", errors="replace"))
    if text_hit is not None:
        return text_hit
    for value in _walk_strings(decoded):
        hit = scan_text(value)
        if hit is not None:
            return hit
    return _scan_auth_pairs(decoded)
