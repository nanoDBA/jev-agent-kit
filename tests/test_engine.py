"""End-to-end engine tests through the mock transport (spec safety invariants).

Because a mock can never produce an effective accept for any question (story 32, matrix),
these cover the failure, ask, no_advice, shadow, egress-block, uncalibrated and validation
paths, plus receipts. The accept path is exercised in test_live_transport.py against a local
fake HTTP server, since accept requires a non-mock transport.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

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
                "kit": {"consequence": "gate", "gate": {"allow_labels": ["no"]}},
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
    q = req["question_set"]["questions"]["destructive"]
    q["kit"] = {"consequence": "advisory"}  # advisory has no gate.allow_labels
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


# --- C21 op dispatch --------------------------------------------------------


def test_run_json_record_outcome_op_needs_no_transport() -> None:
    from jev_kit.engine import run_json

    resp = run_json({"schema_version": 1, "op": "record_outcome",
                     "decision_id": "act_deadbeefdeadbeefdeadbeefdeadbeef", "outcome": "applied"})
    # Unknown (never committed) decision id -> recorded False, but a well-formed response.
    assert resp["status"] == "error" and resp["recorded"] is False


def test_run_json_decide_batch_op(tmp_path: Any) -> None:
    from jev_kit.engine import run_json

    body = json.dumps({"model": "jev-1.13.0", "answers": {"destructive": {"noul": 0.1}}}).encode()
    from jev_kit.transport import TransportResponse
    transport = MockTransport(outcomes=[TransportResponse(200, {}, body)] * 2)
    req = {"schema_version": 1, "op": "decide_batch",
           "requests": [request("shadow"), request("shadow")]}
    resp = run_json(req, transport=transport)
    assert resp["status"] == "ok" and len(resp["results"]) == 2


# --- GATE round: further review findings ------------------------------------


def test_relabeled_mock_still_cannot_accept(tmp_path: Any) -> None:
    # GATE-01: an instance is_mock=False must not reach accept; provenance is read from type.
    from jev_kit.engine import effective_contract
    from jev_kit.fingerprint import question_fingerprint
    from jev_kit.questionset import load_question_set

    qs = load_question_set(question_set())
    fp = question_fingerprint(
        instructions="Is the command destructive?", criteria=None, question_type="noul",
        option_or_level_set=[], model="jev-1.13.0",
        egress_contract=effective_contract(qs, EngineConfig()),
    )
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps({"schema_version": 1, "entries": {fp: {
        "status": "calibrated", "escalation_target": "gpt-6", "evidence_ref": "e",
        "type": "noul", "threshold": {"yes_bound": 0.9, "no_bound": 0.1}, "date": "2026-09-25"}}}),
        encoding="utf-8")
    transport = reply(0.02)
    transport.is_mock = False  # type: ignore[misc]  # runtime attempt to lie about provenance
    cfg = EngineConfig(
        hmac_key=HMAC_KEY, writer=ReceiptWriter(directory=tmp_path), rate_budget=RateBudget(),
        registry_path=str(reg),
        attestation={"inventory": True, "inventory_date": "2026-09-25", "dpa": True},
    )
    rec = only(decide(request("enforce"), transport=transport, config=cfg))
    assert rec["route"] == "ask"  # still treated as a mock; cannot accept
    assert rec["is_mock"] is True


def test_process_wide_cap_across_sequential_calls(tmp_path: Any, monkeypatch: Any) -> None:
    # GATE-05: the default budget is shared across calls, not recreated per call.
    from jev_kit import engine
    from jev_kit.ratebudget import RateBudget as RB

    monkeypatch.setattr(engine, "_DEFAULT_BUDGET", RB(max_calls=1))
    writer = ReceiptWriter(directory=tmp_path)
    # No rate_budget in config -> uses the shared default.
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=writer)
    r1 = only(decide(request("shadow"), transport=reply(0.1), config=cfg))
    r2 = only(decide(request("shadow"), transport=reply(0.1), config=cfg))
    # First call consumes the one permit; the second is refused by the shared cap.
    assert r1["fail_reason"] != "rate_budget"
    assert r2["fail_reason"] == "rate_budget"


def test_sent_digest_absent_when_nothing_sent(tmp_path: Any) -> None:
    # GATE-06: a zero-cap budget means no send, so the receipt has no sent_digest.
    from jev_kit.ratebudget import RateBudget as RB

    writer = ReceiptWriter(directory=tmp_path)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=writer, rate_budget=RB(max_calls=0))
    decide(request("shadow"), transport=reply(0.1), config=cfg)
    line = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
    assert line["sent_digest"] is None


def test_transport_raising_yields_records(tmp_path: Any) -> None:
    # GATE-08: a transport that raises still produces per-question records, not an empty envelope.
    from jev_kit.deadline import Deadline
    from jev_kit.transport import TransportResult

    class _Raising:
        is_mock: ClassVar[bool] = True

        def send(self, body: bytes, deadline: Deadline) -> TransportResult:
            raise RuntimeError("boom")

    resp = decide(request("enforce"), transport=_Raising(), config=config(tmp_path))
    assert resp["status"] == "ok"
    assert resp["records"][0]["route"] == "ask"


def test_decide_batch_shares_process_wide_cap(tmp_path: Any, monkeypatch: Any) -> None:
    # MAJOR-1: a batch must draw from the shared default budget, not a fresh per-batch one.
    from jev_kit import engine
    from jev_kit.ratebudget import RateBudget as RB
    from jev_kit.transport import TransportResponse

    monkeypatch.setattr(engine, "_DEFAULT_BUDGET", RB(max_calls=1))
    body = json.dumps({"model": "jev-1.13.0", "answers": {"destructive": {"noul": 0.1}}}).encode()
    transport = MockTransport(outcomes=[TransportResponse(200, {}, body)] * 3)
    cfg = EngineConfig(hmac_key=HMAC_KEY, writer=ReceiptWriter(directory=tmp_path))
    results = engine.decide_batch(
        [request("shadow"), request("shadow"), request("shadow")], transport=transport, config=cfg
    )
    reasons = [r["records"][0]["fail_reason"] for r in results]
    # With a shared cap of 1, only one request can send; the others hit the rate budget.
    assert reasons.count("rate_budget") == 2


# --- phase-0 hardening batch 2 (H3, H11, H12) -------------------------------


def test_fingerprint_changes_with_profile_version() -> None:
    # H3: bumping a profile's version invalidates the prior calibration fingerprint.
    from jev_kit.engine import EngineConfig, effective_contract
    from jev_kit.fingerprint import question_fingerprint
    from jev_kit.questionset import load_question_set

    qset = load_question_set(question_set())

    def fp(cfg: EngineConfig) -> str:
        return question_fingerprint(
            instructions="x", criteria=None, question_type="noul", option_or_level_set=[],
            model="jev-1.13.0", egress_contract=effective_contract(qset, cfg),
        )

    profiles = {"sql": (lambda s: s)}
    v1 = EngineConfig(language_profiles=profiles, language_profile_versions={"sql": "1"})
    v2 = EngineConfig(language_profiles=profiles, language_profile_versions={"sql": "2"})
    assert fp(v1) != fp(v2)


def test_record_outcome_survives_across_processes(tmp_path: Any, monkeypatch: Any) -> None:
    # H11: an outcome may reference a decision committed by a PRIOR process, via the durable
    # receipt on disk, not only the in-memory set.
    from jev_kit import engine
    from jev_kit.receipts import ReceiptWriter

    writer = ReceiptWriter(directory=tmp_path)
    monkeypatch.setattr(engine, "get_writer", lambda: writer)
    rec = only(decide(request("shadow"), transport=reply(0.5), config=EngineConfig(
        hmac_key=HMAC_KEY, writer=writer, rate_budget=RateBudget())))
    did = rec["decision_id"]
    # Simulate a fresh process: clear the in-memory set, point the disk scan at tmp_path.
    monkeypatch.setattr(engine, "_committed_decisions", set())
    monkeypatch.setattr("jev_kit.receipts.receipts_dir", lambda: tmp_path)
    assert engine.record_outcome(did, "applied") is True
    assert engine.record_outcome("act_" + "0" * 32, "applied") is False  # unknown id


def test_config_from_env_reads_source_allowlist(monkeypatch: Any) -> None:
    # H12: the CLI/hook path picks up the owner's source allowlist from the environment.
    from jev_kit import engine

    monkeypatch.setenv("JEV_KIT_SOURCE_ALLOWLIST", "web, agent_context ,")
    monkeypatch.setenv("JEV_KIT_PUBLIC_NAMES", "sys.tables")
    cfg = engine._config_from_env()
    assert cfg.source_allowlist == frozenset({"web", "agent_context"})
    assert cfg.public_names == frozenset({"sys.tables"})
