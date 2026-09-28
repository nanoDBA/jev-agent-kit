"""Declared log templates (ADR 0004, issue jak-lck), exercised through decide() and MockTransport.

Each test inspects the exact bytes the mock transport received, or asserts nothing was sent.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from jev_kit.engine import EngineConfig, decide, effective_contract
from jev_kit.errors import ValidationError
from jev_kit.fingerprint import question_fingerprint
from jev_kit.questionset import egress_contract, load_question_set
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import ReceiptWriter
from jev_kit.transport import MockTransport

HMAC_KEY = b"0" * 32

TEMPLATES: dict[str, Any] = {
    "conn_failed": {
        "text": "connection to {host} failed after {attempts} retries: {reason}",
        "params": {
            "host": {"kind": "identifier"},
            "attempts": {"kind": "metric"},
            "reason": {"kind": "enum", "values": ["timeout", "refused"]},
        },
    },
    "user_login": {
        "text": "login by {email} from {path} via {tool}",
        "params": {
            "email": {"kind": "contact"},
            "path": {"kind": "path"},
            "tool": {"kind": "command"},
        },
    },
    "started": {"text": "service started"},
}


def qset(templates: Any = TEMPLATES, fmt: bool = True) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "schema_version": 1, "id": "logs", "version": "1", "model": "jev-1.13.0",
        "escalation_target": "gpt-6",
        "questions": {"bad": {"type": "noul", "instructions": "Is this an outage?",
                              "kit": {"consequence": "advisory"}}},
        "state_schema": {
            "events": {"kind": "log", "params": {"format": "template"}} if fmt
            else {"kind": "log"},
            "raw": {"kind": "log"},
        },
    }
    if templates is not None:
        obj["log_templates"] = templates
    return obj


def rec(template_id: str = "conn_failed", level: str = "error", **params: Any) -> dict[str, Any]:
    if not params and template_id == "conn_failed":
        params = {"host": "db01", "attempts": 3, "reason": "timeout"}
    return {"level": level, "template_id": template_id, "params": params}


def run(tmp_path: Any, state: dict[str, Any], qs: dict[str, Any] | None = None
        ) -> tuple[dict[str, Any], MockTransport]:
    body = json.dumps({"model": "jev-1.13.0", "answers": {"bad": {"noul": 0.9}}}).encode()
    transport = MockTransport.replying(200, body)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=ReceiptWriter(directory=tmp_path),
                       rate_budget=RateBudget())
    req = {"schema_version": 1, "question_set": qs or qset(), "state": state, "mode": "shadow"}
    return decide(req, transport=transport, config=cfg), transport


def sent(tmp_path: Any, state: dict[str, Any], qs: dict[str, Any] | None = None) -> Any:
    resp, transport = run(tmp_path, state, qs)
    assert resp["status"] == "ok"
    assert resp["records"][0]["fail_reason"] != "egress_blocked", resp
    assert len(transport.requests) == 1
    return json.loads(transport.requests[0])


def blocked(tmp_path: Any, state: dict[str, Any], qs: dict[str, Any] | None = None) -> None:
    resp, transport = run(tmp_path, state, qs)
    assert transport.requests == []
    record = resp["records"][0]
    assert record["fail_reason"] == "egress_blocked"
    assert record["route"] == "no_advice"


# --------------------------------------------------------------------------- happy path


def test_valid_template_renders_with_literals_and_hmac(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"events": [rec()]})
    [event] = wire["state"]["events"]
    assert event["level"] == "error"
    assert event["template_id"] == "conn_failed"
    msg = event["message"]
    assert msg.startswith("connection to id_") and msg.endswith(" failed after 3 retries: timeout")
    assert "db01" not in json.dumps(wire)


def test_single_record_and_zero_slot_template(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"events": rec("started", "info")})
    assert wire["state"]["events"] == {"level": "info", "template_id": "started",
                                       "message": "service started"}


def test_each_kind_masked_per_kind(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"events": [rec(
        "user_login", "notice", email="jane@example.com", path="C:/Users/jane/x.txt",
        tool="/usr/bin/ssh -i key host")]})
    msg = wire["state"]["events"][0]["message"]
    assert msg == "login by <contact> from <path> via ssh"


def test_public_name_identifier_passes(tmp_path: Any) -> None:
    body = json.dumps({"model": "jev-1.13.0", "answers": {"bad": {"noul": 0.9}}}).encode()
    transport = MockTransport.replying(200, body)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=ReceiptWriter(directory=tmp_path),
                       rate_budget=RateBudget(), public_names=frozenset({"localhost"}))
    req = {"schema_version": 1, "question_set": qset(), "mode": "shadow",
           "state": {"events": [rec(host="localhost", attempts=1, reason="refused")]}}
    decide(req, transport=transport, config=cfg)
    msg = json.loads(transport.requests[0])["state"]["events"][0]["message"]
    assert msg == "connection to localhost failed after 1 retries: refused"


def test_free_form_log_unchanged(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"raw": "ERROR connection to db01 failed after 3 retries"})
    assert wire["state"]["raw"] == "ERROR <v>"


def test_free_form_log_unchanged_without_templates(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"raw": "WARN user JaneDoe 42"}, qset(templates=None, fmt=False))
    assert wire["state"]["raw"] == "WARN <v>"


# --------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "value",
    [
        "ERROR connection to db01 failed",  # free-form string on a templated field
        [rec()] * 21,  # too many records
        [[rec()]],  # nested list
        {"level": "error", "template_id": "conn_failed"},  # missing params key
        {**rec(), "extra": 1},  # extra record key
        rec("nope"),  # unknown template
        rec(level="fail"),  # level outside the keyword set
        rec(level="ERR"),
        rec(level="db01"),
        rec(host="db01", attempts=3),  # missing param
        rec(host="db01", attempts=3, reason="timeout", extra="x"),  # extra param
        rec(host="db01", attempts=True, reason="timeout"),  # bool
        rec(host="db01", attempts=None, reason="timeout"),  # None
        rec(host=["db01"], attempts=3, reason="timeout"),  # nested list
        rec(host={"a": "b"}, attempts=3, reason="timeout"),  # nested object
        rec(host=7, attempts=3, reason="timeout"),  # number in a non-metric slot
        rec(host="db01", attempts=float("inf"), reason="timeout"),  # non-finite
        rec(host="x" * 257, attempts=3, reason="timeout"),  # oversized
        rec(host="db01", attempts=3, reason="other"),  # enum value not declared
        rec(host="db01", attempts=3, reason="{host}"),  # enum injection
        rec(host="db01", attempts="3\nERROR admin password", reason="timeout"),  # newline
        rec(host="db01", attempts="{reason}", reason="timeout"),  # slot syntax in a metric
        rec(host="db01", attempts="1" * 33, reason="timeout"),  # numeric string too long
        {"level": "error", "template_id": "conn_failed", "params": "host=db01"},
        rec("user_login", "info", email="a@b.co", path="/x", tool="ls; cat /etc/passwd"),
        42,
    ],
)
def test_malformed_records_fail_closed(tmp_path: Any, value: Any) -> None:
    blocked(tmp_path, {"events": value})


def test_identifier_injection_is_tokenized_not_expanded(tmp_path: Any) -> None:
    # An identifier value that looks like template text or has newlines is only ever an HMAC token.
    wire = sent(tmp_path, {"events": [rec(host="{attempts}\nfailed after", attempts=3,
                                          reason="timeout")]})
    msg = wire["state"]["events"][0]["message"]
    assert "{" not in msg and "\n" not in msg
    assert msg.count("failed after") == 1


@pytest.mark.parametrize("pan", [4111111111111111, "4111111111111111", "5555555555554444"])
def test_numeric_pan_in_metric_blocked_by_per_slot_scan(tmp_path: Any, pan: Any) -> None:
    # Passes numeric validation, so only the Tier 1 scan stops it (Codex L04).
    blocked(tmp_path, {"events": [rec(host="db01", attempts=pan, reason="timeout")]})


@pytest.mark.parametrize("value", [42, "1234567", 1234567890123, "-0.5"])
def test_ordinary_numbers_in_metric_pass(tmp_path: Any, value: Any) -> None:
    wire = sent(tmp_path, {"events": [rec(host="db01", attempts=value, reason="timeout")]})
    assert f"after {value} retries" in wire["state"]["events"][0]["message"]


@pytest.mark.parametrize(
    "text",
    ["elapsed {n}ms", "id{n} seen", "id{n}", "{n}ms", "{n}{m}", "a {n}{m} b", "x9{n}",
     "{n}7 x"],
)
def test_slot_adjacent_to_letter_digit_or_slot_rejected(tmp_path: Any, text: str) -> None:
    params = {"n": {"kind": "metric"}}
    if "{m}" in text:
        params["m"] = {"kind": "metric"}
    bad = qset(templates={"t": {"text": text, "params": params}})
    with pytest.raises(ValidationError):
        load_question_set(bad)
    resp, transport = run(tmp_path, {"events": {"level": "info", "template_id": "t",
                                                "params": {k: 1 for k in params}}}, bad)
    assert resp["status"] == "error" and resp["reason"] == "config"
    assert transport.requests == []


def test_pan_split_across_slots_blocked_by_final_scan(tmp_path: Any) -> None:
    # Each part is clean alone; only the whole-request scan sees the joined card number.
    params = {k: {"kind": "metric"} for k in "abcd"}
    qs = qset(templates={"t": {"text": "{a} {b} {c} {d}", "params": params}})
    parts = {"a": "4111", "b": "1111", "c": "1111", "d": "1111"}
    blocked(tmp_path, {"events": {"level": "info", "template_id": "t", "params": parts}}, qs)
    clean = {"a": "1", "b": "2", "c": "3", "d": "4"}
    wire = sent(tmp_path, {"events": {"level": "info", "template_id": "t", "params": clean}}, qs)
    assert wire["state"]["events"]["message"] == "1 2 3 4"


@pytest.mark.parametrize(
    "value", [rec(host="db01", attempts="3\n", reason="timeout"),
              rec(host="db01", attempts=3, reason="timeout\n"),
              rec("conn_failed\n", host="db01", attempts=3, reason="timeout"),
              rec(level="error\n")],
)
def test_terminal_newline_values_refused(tmp_path: Any, value: Any) -> None:
    blocked(tmp_path, {"events": value})


@pytest.mark.parametrize(
    "templates",
    [{"t\n": {"text": "x"}},
     {"t": {"text": "x\n"}},
     {"t": {"text": "x {a}", "params": {"a\n": {"kind": "metric"}}}},
     {"t": {"text": "x {a}", "params": {"a": {"kind": "enum", "values": ["b\n"]}}}}],
)
def test_terminal_newline_definitions_refused(templates: Any) -> None:
    with pytest.raises(ValidationError):
        load_question_set(qset(templates=templates))


@pytest.mark.parametrize("kind", [[], {}, 1, None, True])
def test_non_string_slot_kind_is_config_failure(tmp_path: Any, kind: Any) -> None:
    bad = qset(templates={"t": {"text": "x {a}", "params": {"a": {"kind": kind}}}})
    with pytest.raises(ValidationError):
        load_question_set(bad)
    resp, transport = run(tmp_path, {"events": rec()}, bad)
    assert resp["status"] == "error" and resp["reason"] == "config"
    assert transport.requests == []


def test_secret_in_identifier_param_never_leaves(tmp_path: Any) -> None:
    wire = sent(tmp_path, {"events": [rec(host="AKIAABCDEFGHIJKLMNOP", attempts=1,
                                          reason="timeout")]})
    assert "AKIA" not in json.dumps(wire)


# --------------------------------------------------------------------------- loading


@pytest.mark.parametrize(
    "templates",
    [
        {"t": {"text": "x {a}"}},  # undeclared slot
        {"t": {"text": "x", "params": {"a": {"kind": "metric"}}}},  # slot not in text
        {"t": {"text": "{a} {a}", "params": {"a": {"kind": "metric"}}}},  # repeated slot
        {"t": {"text": "x {a", "params": {}}},  # stray brace
        {"t": {"text": "line\nbreak"}},
        {"t": {"text": "say \"hi\""}},
        {"t": {"text": "x" * 241}},
        {"t": {"text": ""}},
        {"t": {"text": "key AKIAABCDEFGHIJKLMNOP"}},  # secret-shaped literal
        {"t": {"text": "x", "extra": 1}},
        {"t": {"text": "x {a}", "params": {"a": {"kind": "free_text"}}}},
        {"t": {"text": "x {a}", "params": {"a": {"kind": "metric", "values": ["y"]}}}},
        {"t": {"text": "x {a}", "params": {"a": {"kind": "enum", "values": []}}}},
        {"t": {"text": "x {a}", "params": {"a": {"kind": "enum", "values": ["b c"]}}}},
        {"t": {"text": "x {a}", "params": {"a": {"kind": "enum", "values": ["b", "b"]}}}},
        {"bad id": {"text": "x"}},
        {"AKIAABCDEFGHIJKLMNOP": {"text": "x"}},
        [],
    ],
)
def test_bad_template_definitions_rejected_at_load(templates: Any) -> None:
    with pytest.raises(ValidationError):
        load_question_set(qset(templates=templates))


def test_template_format_requires_templates() -> None:
    with pytest.raises(ValidationError):
        load_question_set(qset(templates=None))
    with pytest.raises(ValidationError):
        load_question_set(qset(templates={}))


def test_unknown_format_rejected() -> None:
    obj = qset()
    obj["state_schema"]["events"]["params"]["format"] = "regex"
    with pytest.raises(ValidationError):
        load_question_set(obj)


def test_bad_template_set_is_config_failure_through_decide(tmp_path: Any) -> None:
    resp, transport = run(tmp_path, {"events": rec()}, qset(templates={"t": {"text": "{x}"}}))
    assert resp["status"] == "error" and resp["reason"] == "config"
    assert transport.requests == []


# --------------------------------------------------------------------------- fingerprints


def _fp(obj: dict[str, Any]) -> str:
    return question_fingerprint(
        instructions="Is this an outage?", criteria=None, question_type="noul",
        option_or_level_set=[], model="jev-1.13.0",
        egress_contract=effective_contract(load_question_set(obj), EngineConfig()),
    )


def test_templates_are_bound_into_the_fingerprint() -> None:
    base = _fp(qset())
    changed = copy.deepcopy(TEMPLATES)
    changed["started"]["text"] = "service stopped"
    assert _fp(qset(templates=changed)) != base
    changed = copy.deepcopy(TEMPLATES)
    changed["conn_failed"]["params"]["reason"]["values"].append("reset")
    assert _fp(qset(templates=changed)) != base
    changed = copy.deepcopy(TEMPLATES)
    changed["conn_failed"]["params"]["host"] = {"kind": "path"}
    assert _fp(qset(templates=changed)) != base


def test_sets_without_templates_have_no_template_contract() -> None:
    contract = egress_contract(load_question_set(qset(templates=None, fmt=False)))
    assert "log_templates" not in contract
    assert "log_templates" in egress_contract(load_question_set(qset()))


@pytest.mark.parametrize("level", ["ERROR", "Error", "eRrOr"])
def test_level_case_insensitive_sent_lowercased(tmp_path: Any, level: str) -> None:
    wire = sent(tmp_path, {"events": [rec(level=level)]})
    assert wire["state"]["events"][0]["level"] == "error"


@pytest.mark.parametrize(
    "value", ["db01", "JaneDoe", "timeout", "abc123", "deadbeef", "0x1f", "1e5", "3ms",
              "1.2.3", "1,000", " 3", "3 ", "-", "", "1.", ".5", "v1.2"],
)
def test_metric_slot_refuses_non_numeric_text(tmp_path: Any, value: str) -> None:
    blocked(tmp_path, {"events": [rec(host="db01", attempts=value, reason="timeout")]})


@pytest.mark.parametrize(
    ("value", "shown"),
    [(3, "3"), (0, "0"), (-2, "-2"), (1.5, "1.5"), ("42", "42"), ("-0.25", "-0.25"),
     ("+7", "+7")],
)
def test_metric_slot_accepts_numbers(tmp_path: Any, value: Any, shown: str) -> None:
    wire = sent(tmp_path, {"events": [rec(host="db01", attempts=value, reason="timeout")]})
    assert f"failed after {shown} retries" in wire["state"]["events"][0]["message"]


def test_plain_metric_field_behavior_unchanged(tmp_path: Any) -> None:
    obj = qset()
    obj["state_schema"]["version"] = {"kind": "metric"}
    wire = sent(tmp_path, {"version": "python 3.11.4"}, obj)
    assert wire["state"]["version"] == "python 3.11.4"
