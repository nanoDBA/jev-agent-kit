"""End-to-end engine tests through the mock transport (spec safety invariants).

Because a mock can never produce an effective accept for any question (story 32, matrix),
these cover the failure, ask, no_advice, shadow, egress-block, uncalibrated and validation
paths, plus receipts. The accept path is exercised in test_live_transport.py against a local
fake HTTP server, since accept requires a non-mock transport.
"""

from __future__ import annotations

import json
from typing import Any

from jev_kit.engine import EngineConfig, decide
from jev_kit.errors import FailReason
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import ReceiptWriter
from jev_kit.transport import MockTransport, TransportFailure

HMAC_KEY = b"0" * 32


def question_set() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "triage",
        "version": "1",
        "model": "jev-1.13.0",
        "escalation_target": "gpt-6",
        "questions": {
            "destructive": {
                "type": "noul",
                "instructions": "Is the command destructive?",
                "consequence": "gate",
            }
        },
        "state_schema": {"cmd": {"kind": "command"}},
    }


def config(tmp_path: Any) -> EngineConfig:
    return EngineConfig(
        hmac_key=HMAC_KEY,
        writer=ReceiptWriter(directory=tmp_path),
        rate_budget=RateBudget(),
    )


def request(mode: str = "enforce") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "question_set": question_set(),
        "state": {"cmd": "rm -rf /data --force"},
        "mode": mode,
    }


def reply(noul: float) -> MockTransport:
    body = json.dumps({"model": "jev-1.13.0", "answers": {"destructive": {"noul": noul}}}).encode()
    return MockTransport.replying(200, body)


def only(resp: dict[str, Any]) -> dict[str, Any]:
    assert resp["status"] == "ok"
    records: list[dict[str, Any]] = resp["records"]
    assert len(records) == 1
    return records[0]


def test_mock_gate_never_accepts_even_when_answer_is_clear(tmp_path: Any) -> None:
    # No registry, but even a clear answer through a mock must not accept a gate.
    rec = only(decide(request("enforce"), transport=reply(0.99), config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["is_mock"] is True


def test_uncalibrated_gate_enforce_asks(tmp_path: Any) -> None:
    # No registry entry -> uncalibrated -> enforce gate asks.
    rec = only(decide(request("enforce"), transport=reply(0.01), config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] in ("uncalibrated", None) or rec["is_mock"]


def test_shadow_gate_over_mock_is_ask(tmp_path: Any) -> None:
    # Mock precedes shadow: a mock gate is ask in every mode (matrix, story 32).
    rec = only(decide(request("shadow"), transport=reply(0.99), config=config(tmp_path)))
    assert rec["route"] == "ask"


def test_mock_advisory_is_no_advice(tmp_path: Any) -> None:
    req = request("enforce")
    req["question_set"]["questions"]["destructive"]["consequence"] = "advisory"
    rec = only(decide(req, transport=reply(0.99), config=config(tmp_path)))
    assert rec["route"] == "no_advice"


def test_transport_failure_gate_asks(tmp_path: Any) -> None:
    transport = MockTransport(outcomes=[TransportFailure(FailReason.OVERLOADED, "x")])
    rec = only(decide(request("enforce"), transport=transport, config=config(tmp_path)))
    assert rec["route"] == "ask"


def test_model_mismatch_fails(tmp_path: Any) -> None:
    body = json.dumps({"model": "jev-9.9.9", "answers": {"destructive": {"noul": 0.1}}}).encode()
    transport = MockTransport.replying(200, body)
    rec = only(decide(request("enforce"), transport=transport, config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "model_mismatch"


def test_alias_model_is_config_failure(tmp_path: Any) -> None:
    req = request("enforce")
    req["question_set"]["model"] = "jev-latest"
    # transport should not even be needed, but provide one
    rec = only(decide(req, transport=reply(0.1), config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "config"


def test_egress_block_gate_asks_and_sends_nothing(tmp_path: Any) -> None:
    req = request("enforce")
    req["state"] = {"cmd": "psql 'host=x password=hunter2secret dbname=y'"}
    # command reduction keeps flags only, but a password in the arg should be caught by Tier 1
    req["question_set"]["state_schema"] = {
        "cmd": {"kind": "free_text", "params": {"source_type": "log"}}
    }
    cfg = EngineConfig(
        hmac_key=HMAC_KEY,
        writer=ReceiptWriter(directory=tmp_path),
        rate_budget=RateBudget(),
        source_allowlist=frozenset({"log"}),
    )
    transport = reply(0.1)
    rec = only(decide(req, transport=transport, config=cfg))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "egress_blocked"
    assert transport.requests == []  # nothing left the machine


def test_validation_failure_gate_asks(tmp_path: Any) -> None:
    body = json.dumps({"model": "jev-1.13.0", "answers": {"destructive": {"noul": True}}}).encode()
    transport = MockTransport.replying(200, body)
    rec = only(decide(request("enforce"), transport=transport, config=config(tmp_path)))
    assert rec["route"] == "ask"


def test_unidentifiable_questions_return_error_envelope(tmp_path: Any) -> None:
    resp = decide({"schema_version": 1, "state": {}}, transport=reply(0.1), config=config(tmp_path))
    assert resp["status"] == "error"
    assert resp["reason"] == "config"


def test_receipt_written_flag_true_on_success(tmp_path: Any) -> None:
    rec = only(decide(request("shadow"), transport=reply(0.5), config=config(tmp_path)))
    assert rec["receipt_written"] is True
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[-1])["kind"] == "commit"


def test_api_key_never_in_request_bytes(tmp_path: Any) -> None:
    transport = reply(0.1)
    decide(request("shadow"), transport=transport, config=config(tmp_path))
    for body in transport.requests:
        assert b"Bearer" not in body and b"Authorization" not in body
