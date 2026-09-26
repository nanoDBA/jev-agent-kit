"""Keep the published examples working (README and docs/guides).

Each test runs an example exactly as a new user would, offline and without an API key, and
checks the output the docs show. If one of these fails, the docs are wrong.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json"
EXAMPLES = REPO / "examples"


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TYPESAFE_", "JEV_KIT_"))  # no key: the docs' offline setting
    }
    env["PYTHONPATH"] = str(REPO / "src")
    env.update(extra)
    return env


def test_first_decision_example_matches_readme() -> None:
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "first_decision.py")],
        env=_env(), capture_output=True, text=True, timeout=60, check=True,
    )
    lines = proc.stdout.splitlines()
    assert lines[0] == "status: ok"
    for qid in ("destructive", "exfiltrates", "widens_permission"):
        row = next(line for line in lines if line.strip().startswith(qid))
        assert "route=ask" in row and "threshold=uncalibrated" in row and "mock=True" in row
    assert proc.stderr == ""


@pytest.mark.parametrize(
    ("host", "mode", "exit_code", "decision"),
    [
        ("claude", "shadow", 0, None),
        ("claude", "enforce", 0, "ask"),
        ("codex", "shadow", 0, None),
        ("codex", "enforce", 2, "deny"),
    ],
)
def test_hook_shim_on_sample_event(
    host: str, mode: str, exit_code: int, decision: str | None
) -> None:
    event = (EXAMPLES / "events" / f"{host}-pretooluse.json").read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", f"jev_kit.hooks.{host}", "--mode", mode,
         "--question-set-path", str(GATE)],
        input=event, env=_env(), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == exit_code
    out = json.loads(proc.stdout)
    if decision is None:
        assert out == {}
    else:
        assert out["hookSpecificOutput"]["permissionDecision"] == decision


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_hermes_example_plugin(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_HOOK_MODE", mode)
    monkeypatch.setenv("JEV_KIT_HOOK_QUESTION_SET_PATH", str(GATE))
    for key in ("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.syspath_prepend(str(EXAMPLES / "hosts" / "hermes" / "jev-gate"))
    import importlib

    plugin = importlib.import_module("plugin")
    hooks: dict[str, Any] = {}

    class Ctx:
        def register_hook(self, name: str, fn: Any) -> None:
            hooks[name] = fn

    plugin.register(Ctx())
    result = hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "rm -rf ./build"}, task_id="t1"
    )
    if mode == "shadow":
        assert result is None
    else:
        assert result["action"] == "block"
    sys.modules.pop("plugin", None)


@pytest.mark.parametrize(
    "path",
    ["hosts/claude-code/settings.json", "hosts/codex/hooks.json",
     "events/claude-pretooluse.json", "events/codex-pretooluse.json"],
)
def test_example_config_is_valid_json(path: str) -> None:
    obj = json.loads((EXAMPLES / path).read_text(encoding="utf-8"))
    assert isinstance(obj, dict)


def test_docs_have_no_em_dashes() -> None:
    # Owner style rule for all prose (CLAUDE.md).
    for doc in [REPO / "README.md", *sorted((REPO / "docs" / "guides").glob("*.md"))]:
        assert "—" not in doc.read_text(encoding="utf-8"), doc.name
