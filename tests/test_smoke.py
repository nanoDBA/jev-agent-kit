"""Tests for the live smoke script (spec stories 89 to 91; story 52 / ADR 0002 Amendment 1;
finding C20).

All offline. A mock run here goes through the real ``jev_kit.engine.decide`` over a scripted
``MockTransport`` that never touches the network; the "live" paths tested here are the refusal
paths, which must make zero calls to ``jev_kit.engine.run_json`` before ever reaching a
transport.

Like ``scripts/smoke.py`` itself, this file reaches ``jev_kit`` only through the dynamically
loaded ``smoke`` module below (it re-exports everything this file needs: ``decide``,
``EngineConfig``, ``MockTransport``, ``TransportFailure``, ``FailReason``, ``Deadline``,
``ReceiptWriter``, ``load_question_set``), rather than importing the package directly, since
``scripts/`` and its tests are not analyzed alongside ``src`` (no ``py.typed`` marker) when
mypy is pointed at these two files on their own.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest


def _load_smoke_module() -> types.ModuleType:
    """Load scripts/smoke.py by path.

    It lives outside the installed package (it is a separate operator tool, never imported
    by ``jev_kit`` itself) and ``scripts/`` is not a package, so it is loaded directly from
    its file rather than via a normal ``import`` statement. The module is registered in
    ``sys.modules`` before execution, as the dataclasses in ``smoke.py`` need to look
    themselves up there while they are being defined.
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke.py"
    spec = importlib.util.spec_from_file_location("smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load smoke script from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def _write_attestation(
    path: Path, *, inventory: bool = True, inventory_date: str = "2026-09-25", dpa: bool = True
) -> None:
    path.write_text(
        json.dumps({"inventory": inventory, "inventory_date": inventory_date, "dpa": dpa}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fixture shape (finding C20): every synthetic question set must load through the real
# parser, in the current shape (kit.consequence, Choice as an option map, Score as an
# ordered level array).
# ---------------------------------------------------------------------------


def test_synthetic_question_sets_load_for_every_type() -> None:
    assert set(smoke.SYNTHETIC_QUESTION_SETS) == {"noul", "choice", "score"}

    noul = smoke.load_question_set(smoke.SYNTHETIC_QUESTION_SETS["noul"])
    (noul_question,) = noul.questions.values()
    assert not hasattr(noul_question, "options")
    assert not hasattr(noul_question, "levels")

    choice = smoke.load_question_set(smoke.SYNTHETIC_QUESTION_SETS["choice"])
    (choice_question,) = choice.questions.values()
    assert choice_question.options == ("yes", "no")

    score = smoke.load_question_set(smoke.SYNTHETIC_QUESTION_SETS["score"])
    (score_question,) = score.questions.values()
    assert score_question.levels == ("low", "medium", "high")

    for qset in smoke.SYNTHETIC_QUESTION_SETS.values():
        assert qset["model"] == smoke.MODEL


def test_validate_synthetic_fixtures_does_not_raise() -> None:
    smoke.validate_synthetic_fixtures()


# ---------------------------------------------------------------------------
# The mock workload actually exercises the real engine (finding C20): a probe is only "ok"
# when the response status is ok and its records carry no unexpected fail reason.
# ---------------------------------------------------------------------------


def test_mock_run_produces_ok_round_trips_for_all_three_types(tmp_path: Path) -> None:
    writer = smoke.ReceiptWriter(directory=tmp_path / "receipts")

    report, transport = smoke.run_smoke(max_calls=50, mode="shadow", writer=writer)

    by_name = {scenario.name: scenario for scenario in report.scenarios}
    for qtype in ("noul", "choice", "score"):
        scenario = by_name[f"type-{qtype}"]
        assert scenario.status == "ok", (qtype, scenario.fail_reasons, scenario.note)
        assert scenario.fail_reasons == []
        assert scenario.routes and all(
            route in ("accept", "ask", "no_advice") for route in scenario.routes
        )

    # A mock gate (Choice, consequence "gate") routes to "ask"; a mock advisory (Noul, Score,
    # consequence "advisory") routes to "no_advice". Both are successful round trips.
    assert by_name["type-choice"].routes == ["ask"]
    assert by_name["type-noul"].routes == ["no_advice"]
    assert by_name["type-score"].routes == ["no_advice"]

    assert transport.sent <= 50
    assert report.cap_reached is False


def test_mock_run_never_uses_a_real_network_transport(tmp_path: Path) -> None:
    writer = smoke.ReceiptWriter(directory=tmp_path / "receipts")
    _report, transport = smoke.run_smoke(max_calls=50, mode="shadow", writer=writer)

    assert transport.is_mock is True
    assert isinstance(transport.inner, smoke.MockTransport)


# ---------------------------------------------------------------------------
# Hard call cap (finding C14): every outbound attempt is counted, including what an engine
# retry loop would add, not only top-level calls.
# ---------------------------------------------------------------------------


def test_cap_counts_every_send_and_is_never_exceeded(tmp_path: Path) -> None:
    writer = smoke.ReceiptWriter(directory=tmp_path / "receipts")

    report, transport = smoke.run_smoke(max_calls=2, mode="shadow", writer=writer)

    assert transport.sent <= 2
    assert len(transport.inner.requests) == transport.sent
    assert report.cap_reached is True
    assert any(scenario.status == "cap_reached" for scenario in report.scenarios)


def test_capped_transport_refuses_once_the_cap_is_reached() -> None:
    inner = smoke.MockTransport.replying(200, b'{"model": "m", "answers": {}}')
    transport = smoke.CappedTransport(inner=inner, max_calls=1)
    deadline = smoke.Deadline(5.0)

    first = transport.send(b"{}", deadline)
    second = transport.send(b"{}", deadline)

    assert transport.sent == 1
    assert first == inner.outcomes[0]
    assert isinstance(second, smoke.TransportFailure)
    assert second.reason is smoke.FailReason.RATE_BUDGET
    # The refused send never reached the wrapped transport at all.
    assert len(inner.requests) == 1


def test_cap_is_respected_across_an_engine_retry_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single ``decide`` call whose retries would need three sends must still stop at the
    cap: the cap counts every attempt, not one per top-level call (finding C14)."""
    monkeypatch.setattr("jev_kit.engine.time.sleep", lambda _seconds: None)

    retryable = smoke.TransportFailure(smoke.FailReason.SERVER, "boom")
    inner = smoke.MockTransport(outcomes=[retryable, retryable])
    transport = smoke.CappedTransport(inner=inner, max_calls=2)
    config = smoke.EngineConfig(writer=smoke.ReceiptWriter(directory=tmp_path))

    request = {
        "schema_version": 1,
        "question_set": smoke.SYNTHETIC_QUESTION_SETS["noul"],
        "state": {},
        "mode": "shadow",
    }

    response = smoke.decide(request, transport=transport, config=config)

    # Two attempts were forwarded (both scripted failures); the third, which the engine's
    # retry loop would otherwise make, was refused by the cap before reaching the mock.
    assert transport.sent == 2
    assert len(inner.requests) == 2
    assert response["status"] == "ok"
    (record,) = response["records"]
    assert record["fail_reason"] == "rate_budget"
    assert record["route"] == "no_advice"


def test_call_budget_never_reserves_past_the_cap() -> None:
    budget = smoke.CallBudget(max_calls=2)

    assert budget.try_reserve() is True
    assert budget.try_reserve() is True
    assert budget.try_reserve() is False
    assert budget.used == 2


# ---------------------------------------------------------------------------
# Live gating (spec story 52, ADR 0002 Amendment 1): refused before a single call is made.
# ---------------------------------------------------------------------------


def test_missing_max_calls_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        smoke.main(["--out", "docs/smoke"])
    assert exc_info.value.code != 0


def test_live_without_attestation_refuses_with_zero_sends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return {"schema_version": 1, "status": "ok", "records": []}

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    exit_code = smoke.main(["--max-calls", "5", "--live", "--out", str(tmp_path)])

    assert exit_code != 0
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_live_with_invalid_attestation_refuses_with_zero_sends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return {"schema_version": 1, "status": "ok", "records": []}

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    bad_attestation = tmp_path / "attestation.json"
    _write_attestation(bad_attestation, inventory=False)

    exit_code = smoke.main(
        [
            "--max-calls",
            "5",
            "--live",
            "--attestation",
            str(bad_attestation),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code != 0
    assert calls == []


def test_live_with_missing_attestation_file_refuses_with_zero_sends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return {"schema_version": 1, "status": "ok", "records": []}

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    missing = tmp_path / "does-not-exist.json"

    exit_code = smoke.main(
        ["--max-calls", "5", "--live", "--attestation", str(missing), "--out", str(tmp_path)]
    )

    assert exit_code != 0
    assert calls == []


def test_live_with_valid_attestation_proceeds_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live path is still exercised only through a monkeypatched ``run_json``: this never
    reaches a real transport, since the real ``run_json`` symbol is replaced entirely."""
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return {
            "schema_version": 1,
            "status": "ok",
            "records": [
                {
                    "decision_id": "dec-smoke",
                    "question_id": "q",
                    "route": "no_advice",
                    "confidence": 0.5,
                    "fail_reason": None,
                }
            ],
        }

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    good_attestation = tmp_path / "attestation.json"
    _write_attestation(good_attestation)

    exit_code = smoke.main(
        [
            "--max-calls",
            "3",
            "--live",
            "--attestation",
            str(good_attestation),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    assert 0 < len(calls) <= 3


# ---------------------------------------------------------------------------
# Attestation validation.
# ---------------------------------------------------------------------------


def test_load_attestation_rejects_missing_dpa_field(tmp_path: Path) -> None:
    path = tmp_path / "attestation.json"
    payload = {"inventory": True, "inventory_date": "2026-09-25"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = smoke.load_attestation(path)

    assert result.valid is False


def test_load_attestation_rejects_bad_date(tmp_path: Path) -> None:
    path = tmp_path / "attestation.json"
    _write_attestation(path, inventory_date="not-a-date")

    result = smoke.load_attestation(path)

    assert result.valid is False


def test_load_attestation_accepts_dpa_false_when_well_formed(tmp_path: Path) -> None:
    path = tmp_path / "attestation.json"
    _write_attestation(path, dpa=False)

    result = smoke.load_attestation(path)

    assert result.valid is True


def test_load_attestation_rejects_non_json(tmp_path: Path) -> None:
    path = tmp_path / "attestation.json"
    path.write_text("not json at all", encoding="utf-8")

    result = smoke.load_attestation(path)

    assert result.valid is False


# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------


def test_dated_markdown_report_is_written_with_per_type_evidence(tmp_path: Path) -> None:
    out_dir = tmp_path / "smoke-out"
    exit_code = smoke.main(["--max-calls", "50", "--out", str(out_dir)])

    assert exit_code == 0
    written = list(out_dir.glob("smoke-*.md"))
    assert len(written) == 1
    assert written[0].name.startswith("smoke-")
    assert written[0].name.endswith(".md")
    text = written[0].read_text(encoding="utf-8")
    assert "Jev kit live smoke report" in text
    assert "Per-type round trip" in text
    assert "MOCK" in text
    assert "type-noul" in text
    assert "type-choice" in text
    assert "type-score" in text


def test_report_marks_capped_probes_as_not_performed(tmp_path: Path) -> None:
    out_dir = tmp_path / "smoke-out"
    exit_code = smoke.main(["--max-calls", "2", "--out", str(out_dir)])

    assert exit_code == 0
    written = next(out_dir.glob("smoke-*.md"))
    text = written.read_text(encoding="utf-8")
    assert "Not performed" in text
    assert "not performed" in text.lower()


def test_report_labels_mock_results_clearly(tmp_path: Path) -> None:
    exit_code = smoke.main(["--max-calls", "50", "--out", str(tmp_path)])

    assert exit_code == 0
    written = next(tmp_path.glob("smoke-*.md"))
    text = written.read_text(encoding="utf-8")
    assert "MOCK" in text
    assert "no network calls" in text
