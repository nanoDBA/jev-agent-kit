"""Command-line entry point tests (spec stories 7 to 10, 26, 82, 87).

Exercises the CLI's own contract in isolation from the engine: a scripted
``jev_kit.engine.run_json`` stands in for "ok", "error" and "raises unexpectedly", and one
subprocess test runs the real module end to end against the stub engine to prove the exit
codes and JSON-on-stdout contract survive a fresh process (spec story 87).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import jev_kit.engine as engine_module
from jev_kit.cli import main

_OK_RESPONSE: dict[str, Any] = {
    "schema_version": 1,
    "status": "ok",
    "records": [
        {
            "decision_id": "dec-1",
            "question_id": "q1",
            "route": "accept",
            "would_route": None,
            "label": "safe",
            "value": None,
            "distribution": None,
            "confidence": 0.9,
            "noul": None,
            "margin": None,
            "threshold_status": "calibrated",
            "mode": "shadow",
            "fail_reason": None,
            "is_mock": True,
            "receipt_written": True,
        }
    ],
}

_ENGINE_ERROR_RESPONSE: dict[str, Any] = {
    "schema_version": 1,
    "status": "error",
    "reason": "uncalibrated",
    "records": [],
}

_INTERNAL_ENVELOPE: dict[str, Any] = {
    "schema_version": 1,
    "status": "error",
    "reason": "internal",
    "records": [],
}

_CONFIG_ENVELOPE: dict[str, Any] = {
    "schema_version": 1,
    "status": "error",
    "reason": "config",
    "records": [],
}

_VALID_REQUEST: dict[str, Any] = {
    "schema_version": 1,
    "question_set_path": "questions.json",
    "state": {},
    "mode": "shadow",
}


def _set_stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_ok_response_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(engine_module, "run_json", lambda request: _OK_RESPONSE)
    _set_stdin(monkeypatch, json.dumps(_VALID_REQUEST))

    exit_code = main([])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _OK_RESPONSE
    assert captured.out.endswith("\n")


def test_error_envelope_from_engine_is_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(engine_module, "run_json", lambda request: _ENGINE_ERROR_RESPONSE)
    _set_stdin(monkeypatch, json.dumps(_VALID_REQUEST))

    exit_code = main([])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _ENGINE_ERROR_RESPONSE


def test_engine_exception_yields_internal_envelope_and_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("unexpected engine failure")

    monkeypatch.setattr(engine_module, "run_json", _boom)
    _set_stdin(monkeypatch, json.dumps(_VALID_REQUEST))

    exit_code = main([])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _INTERNAL_ENVELOPE


def test_input_from_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(engine_module, "run_json", lambda request: _OK_RESPONSE)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_VALID_REQUEST), encoding="utf-8")

    exit_code = main(["--input", str(request_path)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _OK_RESPONSE


def test_missing_input_file_is_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist.json"

    exit_code = main(["--input", str(missing)])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _CONFIG_ENVELOPE
    assert captured.err != ""


def test_invalid_json_on_stdin_is_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_text = "not-json-XYZ123 { totally broken"
    _set_stdin(monkeypatch, bad_text)

    exit_code = main([])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _CONFIG_ENVELOPE
    assert captured.err != ""
    assert "not-json-XYZ123" not in captured.out
    assert "not-json-XYZ123" not in captured.err


def test_json_array_on_stdin_is_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_stdin(monkeypatch, json.dumps([1, 2, 3]))

    exit_code = main([])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _CONFIG_ENVELOPE
    assert captured.err != ""


def test_unknown_argument_is_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--this-flag-does-not-exist"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _CONFIG_ENVELOPE
    assert captured.err != ""


def test_subprocess_stub_engine_yields_internal_envelope_and_exit_zero() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}

    result = subprocess.run(
        [sys.executable, "-m", "jev_kit.cli"],
        input=json.dumps(_VALID_REQUEST),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    assert parsed["records"] == []
