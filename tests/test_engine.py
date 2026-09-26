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


def test_decide_batch_preserves_order(tmp_path: Any) -> None:
    from jev_kit.engine import decide_batch
    from jev_kit.transport import TransportResponse

    def reply_body(noul: float) -> bytes:
        answers = {"destructive": {"noul": noul}}
        return json.dumps({"model": "jev-1.13.0", "answers": answers}).encode()

    transport = MockTransport(
        outcomes=[
            TransportResponse(200, {}, reply_body(0.1)),
            TransportResponse(200, {}, reply_body(0.9)),
        ]
    )
    reqs = [request("shadow"), request("shadow")]
    results = decide_batch(reqs, transport=transport, config=config(tmp_path), max_workers=2)
    assert len(results) == 2
    assert all(r["status"] == "ok" for r in results)


def test_record_outcome_appends(tmp_path: Any, monkeypatch: Any) -> None:
    from jev_kit import engine
    from jev_kit.receipts import ReceiptWriter

    writer = ReceiptWriter(directory=tmp_path)
    monkeypatch.setattr(engine, "get_writer", lambda: writer)
    # An outcome may only reference a decision committed in this process (finding C16).
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=writer, rate_budget=RateBudget())
    rec = only(decide(request("shadow"), transport=reply(0.5), config=cfg))
    did = rec["decision_id"]
    assert engine.record_outcome(did, "applied") is True
    assert engine.record_outcome(did, "bogus_code") is False
    assert engine.record_outcome("act_deadbeefdeadbeefdeadbeefdeadbeef", "applied") is False
    assert engine.record_outcome("has spaces!", "applied") is False


# --- Regression tests for the code review (C01, C10, C15, C17) --------------


def test_mock_cannot_claim_non_mock_provenance() -> None:
    # is_mock is a class attribute, not a constructor field (finding C01).
    import pytest

    with pytest.raises(TypeError):
        MockTransport(outcomes=[], is_mock=False)  # type: ignore[call-arg]


def test_duplicate_keys_in_response_are_malformed(tmp_path: Any) -> None:
    # Strict response parse rejects duplicate keys rather than taking the last (finding C10).
    body = (
        b'{"model":"jev-1.13.0","answers":'
        b'{"destructive":{"noul":0.5},"destructive":{"noul":0.99}}}'
    )
    transport = MockTransport.replying(200, body)
    rec = only(decide(request("enforce"), transport=transport, config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "response_malformed"


def test_retry_honors_retry_after_and_stops_after_last(tmp_path: Any) -> None:
    from jev_kit.transport import TransportFailure, TransportResponse

    ok_answers = {"destructive": {"noul": 0.1}}
    ok = TransportResponse(
        200, {}, json.dumps({"model": "jev-1.13.0", "answers": ok_answers}).encode()
    )
    transport = MockTransport(
        outcomes=[TransportFailure(FailReason.OVERLOADED, "x", retry_after=0.01), ok]
    )
    only(decide(request("shadow"), transport=transport, config=config(tmp_path)))
    assert len(transport.requests) == 2  # one retry, then success


def test_non_retryable_failure_not_retried(tmp_path: Any) -> None:
    from jev_kit.transport import TransportFailure

    transport = MockTransport(outcomes=[TransportFailure(FailReason.AUTH, "x")])
    only(decide(request("enforce"), transport=transport, config=config(tmp_path)))
    assert len(transport.requests) == 1


def test_receipt_failure_routes_failure_first(tmp_path: Any, monkeypatch: Any) -> None:
    # A successful shadow gate whose receipt fails to write must become ask, not stay
    # no_advice (finding C17).
    from jev_kit.receipts import ReceiptWriter

    writer = ReceiptWriter(directory=tmp_path)
    monkeypatch.setattr(writer, "write_call", lambda lines: False)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=writer, rate_budget=RateBudget())
    rec = only(decide(request("shadow"), transport=reply(0.5), config=cfg))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "receipt"
    assert rec["receipt_written"] is False


# --- C07 fingerprint includes effective egress config -----------------------


def test_fingerprint_changes_with_allowlist() -> None:
    from jev_kit.engine import EngineConfig, effective_contract
    from jev_kit.fingerprint import question_fingerprint
    from jev_kit.questionset import load_question_set

    qset = load_question_set(question_set())

    def fp(cfg: EngineConfig) -> str:
        return question_fingerprint(
            instructions="x", criteria=None, question_type="noul", option_or_level_set=[],
            model="jev-1.13.0", egress_contract=effective_contract(qset, cfg),
        )

    assert fp(EngineConfig()) != fp(EngineConfig(public_names=frozenset({"db01"})))
    assert fp(EngineConfig()) != fp(EngineConfig(source_allowlist=frozenset({"web"})))


# --- C13 budgets ------------------------------------------------------------


def test_request_over_byte_budget_blocked(tmp_path: Any) -> None:
    cfg = EngineConfig(
        hmac_key=HMAC_KEY, writer=ReceiptWriter(directory=tmp_path), rate_budget=RateBudget(),
        max_request_bytes=10,
    )
    rec = only(decide(request("enforce"), transport=reply(0.1), config=cfg))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "budget_exceeded"


def test_positional_array_over_cap_blocked(tmp_path: Any) -> None:
    req = request("enforce")
    req["question_set"]["state_schema"] = {"ids": {"kind": "identifier"}}
    req["state"] = {"ids": [f"h{i}" for i in range(21)]}
    rec = only(decide(req, transport=reply(0.1), config=config(tmp_path)))
    assert rec["route"] == "ask"
    assert rec["fail_reason"] == "egress_blocked"


# --- C08 metadata scanning --------------------------------------------------


def test_secret_action_id_omitted_from_receipt(tmp_path: Any) -> None:
    # An action_id that matches the safe id pattern but is a secret shape must not be recorded.
    from jev_kit.receipts import ReceiptWriter

    writer = ReceiptWriter(directory=tmp_path)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=writer, rate_budget=RateBudget())
    req = request("shadow")
    req["action_id"] = "AKIAIOSFODNN7EXAMPLE"  # passes safe-id regex, is an AWS key shape
    decide(req, transport=reply(0.1), config=cfg)
    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in text


# --- C16 audit fields and mock identity -------------------------------------


def test_record_carries_audit_fields_and_mock_identity(tmp_path: Any) -> None:
    rec = only(decide(request("shadow"), transport=reply(0.5), config=config(tmp_path)))
    assert rec["model"] == "mock"  # mock provenance is explicit
    assert rec["simulated_model"] == "jev-1.13.0"  # what the mock claimed, kept separate
    assert rec["requested_model"] == "jev-1.13.0"
    assert "fingerprint" in rec and len(rec["fingerprint"]) == 64
    files = list(tmp_path.glob("*.jsonl"))
    line = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert line["schema_version"] == 1
    assert line["model"] == "mock"
    assert line["fingerprint"] == rec["fingerprint"]
