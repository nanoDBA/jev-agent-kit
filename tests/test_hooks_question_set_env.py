"""Question-set path resolution shared by all three hook shims (jak-vqy).

Canonical name `JEV_KIT_HOOK_QUESTION_SET_PATH`; deprecated alias `JEV_KIT_QUESTION_SET_PATH`;
canonical wins when both are set; with neither, the default is an absolute path to the
repository's tool-call-gate.json, resolved from the module path, not the working directory.
Every case runs through a shim's public entry point with a capturing runner; no engine, no
network.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jev_kit.hooks import claude as claude_shim
from jev_kit.hooks import codex as codex_shim
from jev_kit.hooks import hermes as hermes_shim
from jev_kit.types import Mode

CANONICAL = "JEV_KIT_HOOK_QUESTION_SET_PATH"
ALIAS = "JEV_KIT_QUESTION_SET_PATH"
REPO_GATE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "jev-runtime"
    / "questions"
    / "tool-call-gate.json"
)

_CLAUDE_EVENT: dict[str, Any] = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
}
_CODEX_EVENT: dict[str, Any] = {
    "hook_event_name": "PreToolUse",
    "tool_name": "shell",
    "tool_input": {"command": "ls"},
}
_HERMES_EVENT: dict[str, Any] = {"tool_name": "terminal", "args": {"command": "ls"}}


def _run_claude(runner: Any) -> None:
    claude_shim.handle_claude_event(_CLAUDE_EVENT, mode=Mode.SHADOW, runner=runner)


def _run_codex(runner: Any) -> None:
    codex_shim.handle_codex_event(_CODEX_EVENT, mode=Mode.SHADOW, runner=runner)


def _run_hermes(runner: Any) -> None:
    hermes_shim.pre_tool_call(None, _HERMES_EVENT, mode=Mode.SHADOW, runner=runner)


SHIMS: dict[str, Callable[[Any], None]] = {
    "claude": _run_claude,
    "codex": _run_codex,
    "hermes": _run_hermes,
}


def _captured_path(shim: str) -> str:
    captured: list[dict[str, Any]] = []

    def runner(request: dict[str, Any]) -> dict[str, Any]:
        captured.append(request)
        return {"status": "ok", "records": []}

    SHIMS[shim](runner)
    assert len(captured) == 1
    path = captured[0]["question_set_path"]
    assert isinstance(path, str)
    return path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CANONICAL, raising=False)
    monkeypatch.delenv(ALIAS, raising=False)


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_canonical_name_is_read(shim: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CANONICAL, "/qs/canonical.json")
    assert _captured_path(shim) == "/qs/canonical.json"


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_deprecated_alias_is_read(shim: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALIAS, "/qs/alias.json")
    assert _captured_path(shim) == "/qs/alias.json"


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_canonical_wins_when_both_set(shim: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CANONICAL, "/qs/canonical.json")
    monkeypatch.setenv(ALIAS, "/qs/alias.json")
    assert _captured_path(shim) == "/qs/canonical.json"


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_blank_canonical_falls_through_to_alias(
    shim: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CANONICAL, "  ")
    monkeypatch.setenv(ALIAS, "/qs/alias.json")
    assert _captured_path(shim) == "/qs/alias.json"


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_default_is_absolute_and_independent_of_cwd(
    shim: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path(_captured_path(shim))
    assert path.is_absolute()
    assert path.is_file()
    assert path == REPO_GATE
