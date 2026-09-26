"""Watchdog tests (Phase 3): a child that hangs is killed and yields a timeout envelope; a
child that answers is parsed. Uses a trivial python child, not the engine or the network."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from jev_kit.hooks import watchdog

_OK_SCRIPT = (
    "import json, sys; "
    'sys.stdout.write(json.dumps({"schema_version": 1, "status": "ok", "records": []}))'
)


def _fake_cli(monkeypatch: Any, script: str) -> None:
    # Point run_child at a throwaway python -c program instead of the real CLI module.
    real_popen = subprocess.Popen

    def popen(argv: list[str], **kw: Any) -> Any:
        return real_popen([sys.executable, "-c", script], **kw)

    monkeypatch.setattr(subprocess, "Popen", popen)


def test_child_timeout_is_killed_and_returns_timeout(monkeypatch: Any) -> None:
    _fake_cli(monkeypatch, "import time; time.sleep(30)")
    resp = watchdog.run_child({"schema_version": 1}, timeout_s=0.5)
    assert resp["status"] == "error" and resp["reason"] == "timeout"


def test_child_response_is_parsed(monkeypatch: Any) -> None:
    _fake_cli(monkeypatch, _OK_SCRIPT)
    resp = watchdog.run_child({"schema_version": 1}, timeout_s=5.0)
    assert resp["status"] == "ok"


def test_child_nonzero_exit_is_internal(monkeypatch: Any) -> None:
    _fake_cli(monkeypatch, "import sys; sys.exit(1)")
    resp = watchdog.run_child({"schema_version": 1}, timeout_s=5.0)
    assert resp["status"] == "error" and resp["reason"] == "internal"
