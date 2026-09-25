"""Egress tests: transforms (deny by default) and Tier 1 detection.

Covers spec stories 39 to 54 and the Codex egress fixtures: escaped multiline private key,
placeholder connection string passes, code without a profile blocked, free text from an
unnamed source blocked, PAN and SSN blocked, HMAC stable across the same key, undeclared
fields dropped.
"""

from __future__ import annotations

import json

import pytest

from jev_kit.egress import (
    ContentKind,
    EgressContext,
    FieldSpec,
    personal_kinds_present,
    scan_request,
    scan_text,
    transform_state,
)
from jev_kit.errors import FailReason, ValidationError

HMAC_KEY = b"0" * 32


def ctx(**kw: object) -> EgressContext:
    base: dict[str, object] = {"hmac_key": HMAC_KEY}
    base.update(kw)
    return EgressContext(**base)  # type: ignore[arg-type]


# --- transforms -------------------------------------------------------------


def test_undeclared_fields_dropped() -> None:
    schema = {"host": FieldSpec(ContentKind.IDENTIFIER)}
    out = transform_state({"host": "db01", "secret_notes": "leak me"}, schema, ctx())
    assert set(out) == {"host"}


def test_identifier_hmac_stable_and_public_allowlist() -> None:
    schema = {"host": FieldSpec(ContentKind.IDENTIFIER)}
    a = transform_state({"host": "db01"}, schema, ctx())
    b = transform_state({"host": "db01"}, schema, ctx())
    assert a["host"] == b["host"] and a["host"].startswith("id_")
    pub = transform_state(
        {"host": "sys.tables"}, schema, ctx(public_names=frozenset({"sys.tables"}))
    )
    assert pub["host"] == "sys.tables"


def test_identifier_without_key_is_config_failure() -> None:
    schema = {"host": FieldSpec(ContentKind.IDENTIFIER)}
    with pytest.raises(ValidationError) as exc:
        transform_state(
            {"host": "db01"}, schema, EgressContext(hmac_key=None)
        )
    assert exc.value.reason is FailReason.CONFIG


def test_code_without_profile_blocked() -> None:
    schema = {"q": FieldSpec(ContentKind.CODE, {"language": "sql"})}
    with pytest.raises(ValidationError) as exc:
        transform_state({"q": "select * from t where id = 5"}, schema, ctx())
    assert exc.value.reason is FailReason.EGRESS_BLOCKED


def test_code_with_fixture_profile_normalized() -> None:
    def fake_sql(text: str) -> str:
        import re

        return re.sub(r"\b\d+\b", "?", text)

    schema = {"q": FieldSpec(ContentKind.CODE, {"language": "sql"})}
    out = transform_state(
        {"q": "select * from t where id = 5"},
        schema,
        ctx(language_profiles={"sql": fake_sql}),
    )
    assert "5" not in out["q"] and "?" in out["q"]


def test_free_text_blocked_without_named_source() -> None:
    schema = {"body": FieldSpec(ContentKind.FREE_TEXT, {"source_type": "web"})}
    with pytest.raises(ValidationError):
        transform_state({"body": "hello"}, schema, ctx())  # empty source allowlist


def test_free_text_allowed_from_named_source_masks_contact() -> None:
    schema = {"body": FieldSpec(ContentKind.FREE_TEXT, {"source_type": "web"})}
    out = transform_state(
        {"body": "reach me at a@b.com"},
        schema,
        ctx(source_allowlist=frozenset({"web"})),
    )
    assert "a@b.com" not in out["body"] and "<contact>" in out["body"]


def test_number_in_non_metric_field_blocked() -> None:
    schema = {"host": FieldSpec(ContentKind.IDENTIFIER)}
    with pytest.raises(ValidationError):
        transform_state({"host": 5}, schema, ctx())


def test_metric_passes_number() -> None:
    schema = {"rows": FieldSpec(ContentKind.METRIC)}
    assert transform_state({"rows": 42}, schema, ctx()) == {"rows": 42}


def test_unsafe_object_key_blocked() -> None:
    schema = {"ids": FieldSpec(ContentKind.IDENTIFIER)}
    with pytest.raises(ValidationError):
        transform_state({"ids": {"bad key!": "x"}}, schema, ctx())


def test_keyed_list_transformed() -> None:
    schema = {"ids": FieldSpec(ContentKind.IDENTIFIER)}
    out = transform_state({"ids": {"a": "server1", "b": "server2"}}, schema, ctx())
    assert all(v.startswith("id_") for v in out["ids"].values())


def test_personal_kinds_present() -> None:
    assert personal_kinds_present({"h": FieldSpec(ContentKind.IDENTIFIER)}) is True
    assert personal_kinds_present({"n": FieldSpec(ContentKind.METRIC)}) is False


# --- detectors --------------------------------------------------------------


def test_private_key_detected_even_escaped_in_json() -> None:
    body = json.dumps(
        {"text": "-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----"}
    ).encode("utf-8")
    assert scan_request(body, json.loads(body)) == "private_key"


def test_connection_string_password_detected() -> None:
    assert scan_text("Server=x;User Id=sa;Password=hunter2secret;") == "connection_string_password"


def test_connection_string_placeholder_passes() -> None:
    assert scan_text("Server=x;Password=***;") is None
    assert scan_text("Password=<password>") is None


def test_payment_card_luhn_detected() -> None:
    assert scan_text("card 4111 1111 1111 1111 on file") == "payment_card"


def test_non_luhn_digits_not_flagged_as_card() -> None:
    assert scan_text("order 1234 5678 9012 3456 shipped") != "payment_card"


def test_ssn_detected() -> None:
    assert scan_text("ssn 123-45-6789") == "us_ssn"


def test_jwt_and_aws_and_github_detected() -> None:
    jwt = "eyJhbGciOiJIUzI1NiIsInR5.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4"
    assert scan_text(f"token {jwt}") == "jwt"
    assert scan_text("AKIAIOSFODNN7EXAMPLE") == "aws_key"
    assert scan_text("ghp_" + "a" * 36) == "github_pat"


def test_clean_text_passes() -> None:
    assert scan_text("route this ticket to billing") is None
    assert scan_request(b'{"x":"hello"}', {"x": "hello"}) is None
