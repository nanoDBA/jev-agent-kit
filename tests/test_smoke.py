"""Tests for the live smoke script (spec stories 89 to 91; story 52 / ADR 0002 Amendment 1).

All offline. Nothing here ever calls the real network: mock runs are fully local, and the
"live" paths tested here are the refusal paths, which must make zero calls to
``jev_kit.engine.run_json`` before ever reaching a transport.
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


def _ok_response(confidence: float = 0.5) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "ok",
        "records": [
            {
                "decision_id": "dec-smoke",
                "question_id": "q",
                "route": "no_advice",
                "would_route": None,
                "label": None,
                "value": None,
                "distribution": None,
                "confidence": confidence,
                "noul": None,
                "margin": None,
                "threshold_status": None,
                "mode": "shadow",
                "fail_reason": None,
                "is_mock": True,
                "receipt_written": True,
            }
        ],
    }


def test_missing_max_calls_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        smoke.main(["--out", "docs/smoke"])
    assert exc_info.value.code != 0


def test_live_without_attestation_flag_refuses_and_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    exit_code = smoke.main(["--max-calls", "5", "--live", "--out", str(tmp_path)])

    assert exit_code != 0
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_live_with_invalid_attestation_refuses_and_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return _ok_response()

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


def test_live_with_missing_attestation_file_refuses_and_makes_no_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    missing = tmp_path / "does-not-exist.json"

    exit_code = smoke.main(
        ["--max-calls", "5", "--live", "--attestation", str(missing), "--out", str(tmp_path)]
    )

    assert exit_code != 0
    assert calls == []


def test_live_with_valid_attestation_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        calls.append(request)
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    good_attestation = tmp_path / "attestation.json"
    _write_attestation(good_attestation)

    exit_code = smoke.main(
        [
            "--max-calls",
            "8",
            "--live",
            "--attestation",
            str(good_attestation),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    assert len(calls) > 0
    assert len(calls) <= 8


def test_mock_run_respects_the_hard_call_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    call_count = 0

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    exit_code = smoke.main(["--max-calls", "2", "--out", str(tmp_path)])

    assert exit_code == 0
    assert call_count <= 2


def test_mock_run_never_exceeds_cap_even_with_concurrent_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    call_count = 0

    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    # Enough calls to reach past the three latency scenarios into the concurrent scenario,
    # but not enough to finish it, so the cap must bite mid-batch too.
    exit_code = smoke.main(["--max-calls", "5", "--out", str(tmp_path)])

    assert exit_code == 0
    assert call_count <= 5


def test_mock_run_never_calls_real_transport_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The engine stub raises before touching any transport; this pins that expectation."""
    exit_code = smoke.main(["--max-calls", "3", "--out", str(tmp_path)])

    assert exit_code == 0
    written = list(tmp_path.glob("smoke-*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "engine-not-ready" in text
    assert "MOCK" in text


def test_dated_markdown_file_is_written_to_out_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    out_dir = tmp_path / "smoke-out"
    exit_code = smoke.main(["--max-calls", "10", "--out", str(out_dir)])

    assert exit_code == 0
    written = list(out_dir.glob("smoke-*.md"))
    assert len(written) == 1
    assert written[0].name.startswith("smoke-")
    assert written[0].name.endswith(".md")
    text = written[0].read_text(encoding="utf-8")
    assert "Jev kit live smoke report" in text
    assert "Measurements" in text


def test_report_labels_mock_results_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        return _ok_response()

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    exit_code = smoke.main(["--max-calls", "10", "--out", str(tmp_path)])

    assert exit_code == 0
    written = next(tmp_path.glob("smoke-*.md"))
    text = written.read_text(encoding="utf-8")
    assert "MOCK" in text
    assert "no network calls" in text


def test_report_records_confidence_without_asserting_formula(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fake_run_json(request: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
        return _ok_response(confidence=0.75)

    monkeypatch.setattr(smoke, "run_json", _fake_run_json)

    exit_code = smoke.main(["--max-calls", "10", "--out", str(tmp_path)])

    assert exit_code == 0
    written = next(tmp_path.glob("smoke-*.md"))
    text = written.read_text(encoding="utf-8")
    assert "0.750" in text


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


def test_call_budget_never_reserves_past_the_cap() -> None:
    budget = smoke.CallBudget(max_calls=2)

    assert budget.try_reserve() is True
    assert budget.try_reserve() is True
    assert budget.try_reserve() is False
    assert budget.used == 2


def test_mock_transport_is_never_a_real_network_transport(tmp_path: Path) -> None:
    transport = smoke._build_mock_transport(3)
    assert transport.is_mock is True
