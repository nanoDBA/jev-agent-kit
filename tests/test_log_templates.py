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
        rec(level="ERROR"),  # level outside the keyword set
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
        rec(host="db01", attempts="a" * 65, reason="timeout"),  # metric token too long
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


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36,
        "4111 1111 1111 1111",
    ],
)
def test_secret_in_metric_param_blocked_by_final_scan(tmp_path: Any, secret: str) -> None:
    blocked(tmp_path, {"events": [rec(host="db01", attempts=secret, reason="timeout")]})


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
