"""Keep the published examples working (README and docs/guides).

Each test runs an example exactly as a new user would, offline and without an API key, and
checks the output the docs show. If one of these fails, the docs are wrong.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json"
EXAMPLES = REPO / "examples"
README = (REPO / "README.md").read_text(encoding="utf-8")


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TYPESAFE_", "JEV_KIT_"))  # no key: the docs' offline setting
    }
    env["PYTHONPATH"] = str(REPO / "src")
    env.update(extra)
    return env


def _run_example(name: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        env=_env(), capture_output=True, text=True, timeout=60, check=True,
    )
    assert proc.stderr == ""
    return proc.stdout


def test_gate_walkthrough_matches_readme() -> None:
    out = _run_example("gate_walkthrough.py")
    # The reduced request shown in the README is exactly what the example sends.
    start = out.index("What is actually sent to Jev (state only):")
    sent = json.loads(out[out.index("{", start): out.index("}", start) + 1])
    assert sent["command"] == "rm"
    assert sent["target"].startswith("id_")
    assert json.dumps(sent, indent=2) in README
    for qid in ("destructive", "exfiltrates", "widens_permission"):
        row = next(line for line in out.splitlines() if line.strip().startswith(qid))
        assert row.strip().endswith("-> ask")
        assert " ".join(row.split()) in " ".join(README.split())


def test_route_request_matches_readme() -> None:
    out = _run_example("route_request.py")
    assert "route:         no_advice  (mock=True)" in out
    assert "handled by:    specialist_llm" in out
    for line in out.splitlines():
        assert line in README, line


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


def _load_hermes_plugin_as_package() -> Any:
    # Hermes loads a directory plugin as a package from its __init__.py (not plugin.py).
    folder = EXAMPLES / "hosts" / "hermes" / "jev-gate"
    assert (folder / "plugin.yaml").is_file()
    spec = importlib.util.spec_from_file_location(
        "jev_gate_under_test", folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_hermes_example_plugin(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_HOOK_MODE", mode)
    monkeypatch.setenv("JEV_KIT_HOOK_QUESTION_SET_PATH", str(GATE))
    for key in ("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    plugin = _load_hermes_plugin_as_package()
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


@pytest.mark.parametrize(
    ("path", "module"),
    [("hosts/claude-code/settings.json", "claude"), ("hosts/codex/hooks.json", "codex")],
)
def test_example_hook_command_survives_a_path_with_spaces(path: str, module: str) -> None:
    config = json.loads((EXAMPLES / path).read_text(encoding="utf-8"))
    command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    spaced = "/tmp/My Kit/jev_agent_kit"
    argv = shlex.split(command.replace("/ABSOLUTE/PATH/TO/jev_agent_kit", spaced))
    assert argv[:3] == ["python", "-m", f"jev_kit.hooks.{module}"]
    assert argv[argv.index("--question-set-path") + 1] == (
        f"{spaced}/skills/jev-runtime/questions/tool-call-gate.json"
    )


@pytest.mark.parametrize("path", ["events/claude-pretooluse.json", "events/codex-pretooluse.json"])
def test_sample_event_is_valid_json(path: str) -> None:
    assert isinstance(json.loads((EXAMPLES / path).read_text(encoding="utf-8")), dict)


def test_docs_have_no_em_dashes() -> None:
    # Owner style rule for all prose (CLAUDE.md).
    docs = [REPO / name for name in ("README.md", "SECURITY.md", "CONTRIBUTING.md")]
    docs += [REPO / "docs" / "handoffs" / "codex-reviewer.md"]
    for doc in [*docs, *sorted((REPO / "docs" / "guides").glob("*.md"))]:
        assert "—" not in doc.read_text(encoding="utf-8"), doc.name
