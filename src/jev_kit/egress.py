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
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
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


# The only log tokens kept verbatim: recognized severity/level keywords. Everything else in a
# free-form log line is a potential identifier (a hostname like db01, a username like JaneDoe, a
# tenant, a path), and without a declared template we cannot tell a template word from data, so
# it is masked rather than guessed at (finding H17; ADR 0002 Tier 2 / story 44). Richer log
# support (declared safe templates / structured fields) is a separate, later capability.
_LOG_LEVELS = frozenset(
    {
        "error", "err", "warn", "warning", "info", "information", "debug", "trace", "fatal",
        "critical", "crit", "notice", "alert", "emerg", "emergency", "verbose", "log",
    }
)


def _is_log_safe_token(token: str) -> bool:
    # Only already-inserted placeholders and recognized level keywords survive. A bare number is
    # NOT presumed a safe code or count: a free-form log has no declared template telling a
    # status code from a numeric password or account id, so numbers are masked too (finding
    # H17). Numbers survive only through a future explicitly-safe structured/template contract.
    if token in ("<v>", "<contact>"):
        return True
    core = token.strip(":=[](){}.,;\"'")
    return core.lower() in _LOG_LEVELS


def _reduce_log(text: str) -> str:
    # First reduce the structured parts (quoted spans and contacts) to placeholders, then keep
    # only safe tokens and mask every other token; runs of masked tokens collapse to one <v>.
    text = re.sub(r"'[^']*'", "<v>", text)
    text = re.sub(r'"[^"]*"', "<v>", text)
    text = _mask_contacts(text)
    out: list[str] = []
    for token in text.split():
        masked = token if _is_log_safe_token(token) else "<v>"
        if masked == "<v>" and out and out[-1] == "<v>":
            continue  # collapse consecutive masked tokens
        out.append(masked)
    return " ".join(out)


# A leading variable assignment before the command word: a POSIX "REVIEW_TOKEN=PRIVATE tool"
# (finding GATE-02) or a shell variable form ("$env:TOKEN=..." in PowerShell, "$TOKEN=..."). One
# or more of these are dropped in full, name and value alike.
_ENV_ASSIGNMENT = re.compile(r"^(?:\$env:|\$)?[A-Za-z_][A-Za-z0-9_]*=")

# Compound, redirecting or substituting shell syntax: a semicolon, pipe, background/and/or,
# redirect, backtick or "$(" substitution. Such a line names more than one command or a data
# sink, so it cannot be reduced to a single safe command word and is blocked (finding H16). This
# is dialect-neutral: it holds for POSIX shells, cmd.exe and PowerShell alike.
_CMD_CONTROL = re.compile(r"[;&|<>`\n]|\$\(")

# A backslash that escapes whitespace, or a trailing backslash (line continuation): both join
# tokens across a whitespace split and are rejected (finding H16). A backslash followed by a
# non-whitespace path character (a Windows path) does not match.
_CMD_ESCAPE = re.compile(r"\\(\s|$)")

def _reduce_command(text: str) -> str:
    # Emit ONLY the command basename; every argument token is dropped (finding H16). Without a
    # per-command option-arity grammar we cannot tell a flag name from an option's argument
    # value: "git -C --PRIVATE_CUSTOMER ..." feeds --PRIVATE_CUSTOMER to -C as a directory, so a
    # token starting with a dash is not proof of a flag. Rather than guess, we keep nothing after
    # the command word. A declared safe option grammar that could retain known flags is deferred
    # (tracked as a follow-up). Compound/redirecting, quoted and escaped inputs are rejected
    # first, since even the command word cannot be located unambiguously in them.
    if _CMD_CONTROL.search(text):
        raise ValidationError(FailReason.EGRESS_BLOCKED, "command_compound_unsupported")
    if '"' in text or "'" in text:
        raise ValidationError(FailReason.EGRESS_BLOCKED, "command_quoting_unsupported")
    # A backslash before whitespace (escaped space) or at end of a token (line continuation)
    # joins tokens across the whitespace split, so it is rejected; a Windows path backslash
    # (followed by a path character, not whitespace) is unaffected.
    if _CMD_ESCAPE.search(text):
        raise ValidationError(FailReason.EGRESS_BLOCKED, "command_escape_unsupported")
    tokens = text.split()
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGNMENT.match(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return ""
    # Command word: basename only, dropping any POSIX or Windows directory path. If anything odd
    # survives (a quote or '='), emit a placeholder rather than a guess.
    name = tokens[idx].replace("\\", "/").rsplit("/", 1)[-1]
    if not name or any(ch in name for ch in "'\"="):
        name = "<command>"
    return name


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
        # Only an IDENTIFIER field authorizes a keyed collection, because only there are the keys
        # themselves transformed (tokenized). For any other kind the keys are undeclared
        # descendants that no schema covers and would ship verbatim, so a keyed object is refused
        # rather than best-effort masked (finding H4, story 40).
        if spec.kind is not ContentKind.IDENTIFIER:
            raise ValidationError(FailReason.EGRESS_BLOCKED, "undeclared_child_object")
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
    # The separator class tolerates the quotes/colon and any run of whitespace of JSON-embedded
    # auth, e.g. {"Authorization":        "Basic dTpw"} (finding H15), as well as a plain header.
    # It is a single character class with an unbounded but non-overlapping `+`, so there is no
    # length cutoff to bypass and no backtracking ambiguity (linear scan).
    ("authorization_header", re.compile(r"(?i)authorization[\"'\s:=]+(?:bearer|basic)\s+\S+")),
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

# The single authoritative set of STRONG-secret key names: a value under one of these is a leak
# whatever its shape (finding H15). Both the JSON-text detector below and the structural pair
# scan (_scan_auth_pairs, on real decoded dicts) derive from this one set so they can never
# drift apart and leave a key covered by one but not the other.
_STRONG_SECRET_NAMES = frozenset(
    {
        "password", "passwd", "pwd", "secret", "client_secret", "secret_key", "private_key",
        "access_token", "id_token", "refresh_token", "api_key", "api-key", "apikey", "x-api-key",
    }
)

# A structured (JSON-style) strong-secret key mapped to a value, e.g. {"password":"x"},
# {"private_key":"..."} or a NUMERIC {"password":123456}, embedded in a free-text string that the
# structural pair scan cannot look inside. Weak indicators (authorization, token) are NOT here:
# they still need a credential shape and are handled by _scan_auth_pairs, so a benign
# {"authorization":"none"} is not flagged. Group 1 (a quoted string value) is checked against the
# placeholder set; group 2 (a numeric or true/false literal) is always a leak.
_JSON_SECRET_KV = re.compile(
    r'(?i)"(?:'
    + "|".join(re.escape(k) for k in sorted(_STRONG_SECRET_NAMES, key=len, reverse=True))
    + r')"\s*:\s*(?:"([^"]*)"|(-?[0-9][0-9.eE+-]*|true|false))'
)

# JSON string escapes that can obfuscate an embedded credential in a free-text value: a
# \uXXXX unicode escape (e.g. "Authorization") or an escaped quote (e.g. \"password\").
# scan_text scans a de-escaped copy so these forms are still caught (finding H15).
_JSON_ESCAPE = re.compile(r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})')

# Every JSON short escape (RFC 8259 section 7), so an escaped tab/newline separator between a
# key and its value cannot hide an embedded credential (finding H15).
_JSON_ESCAPE_SIMPLE = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}


def _deescape_json(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if body[0] == "u":
            return chr(int(body[1:], 16))
        return _JSON_ESCAPE_SIMPLE.get(body, body)

    return _JSON_ESCAPE.sub(_replace, text)
_PAN_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")  # PCI DSS v4.0.1 3.3.1: Luhn-valid PANs

# A whole value that is an HTTP auth scheme plus a single credential token, e.g. "Basic dTpw",
# "Bearer x" or "Bearer a~b". Matched as the ENTIRE string (scheme then exactly one non-space
# token) so an ordinary sentence ("Bearer of bad news", several words) does not trip it, but any
# single-token credential does, with no minimum length or charset loophole (finding H15).
# scan_request scans each decoded value on its own, so {"Authorization": "Basic dTpw"} is caught
# even when the key and value are separate JSON strings.
_AUTH_SCHEME_VALUE = re.compile(r"(?i)^(?:basic|bearer)\s+\S+$")


def scan_text(text: str) -> str | None:
    """Return the id of the first Tier 1 rule that matches, or None.

    The text is scanned as given and, when it contains JSON unicode escapes, also in a
    de-escaped form so an obfuscated credential key or scheme cannot slip past (finding H15).
    """
    return _scan_text(text, 0)


def _scan_text(text: str, depth: int) -> str | None:
    hit = _scan_text_once(text)
    if hit is not None:
        return hit
    candidates = [text]
    if "\\" in text:
        deescaped = _deescape_json(text)
        if deescaped != text:
            hit = _scan_text_once(deescaped)
            if hit is not None:
                return hit
            candidates.append(deescaped)
    # Structural pass: a value that is itself a JSON document is parsed (bounded) and checked
    # with the same structural credential rules as real request objects, so any JSON
    # representation (signed/exponent numbers, escapes, arbitrary whitespace) is judged by
    # meaning rather than by one regex spelling (finding H15).
    for candidate in candidates:
        hit = _scan_embedded_json(candidate, depth)
        if hit is not None:
            return hit
    return None


_MAX_EMBEDDED_JSON = 65_536

# How many layers of JSON-inside-a-JSON-string the scanner decodes. Deeper encoding fails
# closed rather than being passed unscanned (finding H15).
_MAX_JSON_LAYERS = 4


def _scan_embedded_json(text: str, depth: int = 0) -> str | None:
    stripped = text.strip()
    # A JSON document can have an object, array OR string root. A string root ("...") is itself
    # an encoding layer (json.dumps of a JSON document), so it is decoded and rescanned under
    # the same layer budget rather than skipped (finding H15).
    if not stripped or stripped[0] not in '{["' or len(stripped) > _MAX_EMBEDDED_JSON:
        return None
    if depth >= _MAX_JSON_LAYERS:
        return "embedded_json_too_deep"
    if stripped[0] == '"':
        try:
            inner = json.loads(stripped)
        except (ValueError, RecursionError):
            return None
        return _scan_text(inner, depth + 1) if isinstance(inner, str) else None
    try:
        parsed = json.loads(stripped, object_pairs_hook=_pairs_rejecting_duplicates)
    except _DuplicateEmbeddedKey:
        # Ordinary json.loads keeps only the LAST duplicate member, so an earlier credential
        # hidden behind a padded duplicate key would be scanned away while the outgoing string
        # still carries it. A duplicate (after trimming/case-folding) fails closed (H15).
        return "embedded_json_duplicate_key"
    except (ValueError, RecursionError):
        return None
    try:
        hit = _scan_auth_pairs(parsed)
        if hit is not None:
            return hit
        # A string leaf may itself be JSON-encoded ({"payload": "{\" password \": \"x\"}"}),
        # which the structural pass above sees only as an opaque string. Every decoded key and
        # string leaf is rescanned one layer deeper, under the shared layer limit (H15).
        for leaf in _walk_strings(parsed):
            hit = _scan_text(leaf, depth + 1)
            if hit is not None:
                return hit
        return None
    except RecursionError:
        return "embedded_json_too_deep"  # fail closed on pathological nesting


class _DuplicateEmbeddedKey(Exception):
    pass


def _pairs_rejecting_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        normalized = key.strip().lower()
        if normalized in seen:
            raise _DuplicateEmbeddedKey
        seen.add(normalized)
    return dict(pairs)


def _scan_text_once(text: str) -> str | None:
    for rule_id, pattern in _DETECTORS:
        if pattern.search(text):
            return rule_id
    if _AUTH_SCHEME_VALUE.match(text.strip()):
        return "authorization_scheme_value"
    for match in _JSON_SECRET_KV.finditer(text):
        string_value, literal_value = match.group(1), match.group(2)
        if literal_value is not None:  # a numeric or true/false value is always a leak
            return "structured_credential"
        if string_value is not None and not _is_placeholder(string_value):
            return "structured_credential"
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
# Weak indicators name a field that often but not always carries a token, so a value there is
# blocked only when it also has a credential shape (finding GATE-04). _STRONG_SECRET_NAMES (the
# authoritative set) is defined once with the JSON-text detector above.
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


def _is_nonempty_secret_scalar(value: Any) -> bool:
    """True for a non-empty, non-placeholder scalar value under a strong-secret key (H15).

    Covers strings and numbers (bool included): a numeric password is still a secret. None,
    empty/whitespace strings and documented redaction placeholders are not treated as leaks.
    """
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return value.strip() != "" and not _is_placeholder(value)
    if isinstance(value, (list, tuple, dict)):
        # A container under a strong-secret key ({"password": ["x"]} or {"password": {"v": "x"}})
        # carries the secret one level down; recursion alone would lose the parent's secret
        # context, so any non-empty container here is a leak (finding H15).
        return len(value) > 0
    return False


def _scan_auth_pairs(obj: Any) -> str | None:
    """Find a secret adjacent to an auth indicator key (findings GATE-04, H15)."""
    if isinstance(obj, dict):
        # A value directly under a strong-secret key is blocked whatever its shape (H15): a
        # short or lowercase password, or a NUMERIC one (e.g. {"password": 123456}), would pass
        # the string-only / credential-shape heuristics otherwise. Any non-empty, non-placeholder
        # scalar value counts.
        for key, value in obj.items():
            if (
                isinstance(key, str)
                and key.strip().lower() in _STRONG_SECRET_NAMES
                and _is_nonempty_secret_scalar(value)
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


# --------------------------------------------------------------------------- policy identity


def _compute_transform_digest() -> str:
    """A deterministic digest of the shipped egress transform/detector implementation (H3).

    The fingerprint that thresholds calibrate against must change whenever transform or
    detection logic changes (story 69), AND must stay identical for unchanged code across
    processes, or a same-revision registry would never match again. We therefore hash the
    shipped source artifact itself, with line endings normalized so CRLF and LF checkouts of the
    same revision agree. It is deliberately conservative: any edit to this module (even a
    comment) invalidates prior calibration, which is safe. If the source is unavailable (a
    bytecode-only install) we fall back to a canonical, address-free walk of the code objects.
    """
    try:
        source = Path(__file__).read_bytes()
    except OSError:
        return _canonical_module_code_digest()
    return _source_digest(source)


def _source_digest(source: bytes) -> str:
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "src:" + hashlib.sha256(normalized).hexdigest()[:28]


def _canonical_code(code: Any, digest: Any) -> None:
    # Bytecode, names and constants, recursing into nested code objects instead of using their
    # repr (which embeds a memory address and the checkout filename and so differs per process).
    digest.update(code.co_code)
    digest.update(repr(code.co_names).encode("utf-8"))
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            _canonical_code(const, digest)
        elif isinstance(const, frozenset):
            digest.update(repr(sorted(const, key=repr)).encode("utf-8"))
        else:
            digest.update(repr(const).encode("utf-8"))


def _canonical_module_code_digest() -> str:
    import types

    digest = hashlib.sha256()
    for name in sorted(globals()):
        obj = globals()[name]
        if isinstance(obj, types.FunctionType) and obj.__module__ == __name__:
            digest.update(name.encode("utf-8"))
            _canonical_code(obj.__code__, digest)
        elif isinstance(obj, re.Pattern):
            digest.update(name.encode("utf-8") + obj.pattern.encode("utf-8"))
    return "code:" + digest.hexdigest()[:27]


EGRESS_TRANSFORM_DIGEST = _compute_transform_digest()
