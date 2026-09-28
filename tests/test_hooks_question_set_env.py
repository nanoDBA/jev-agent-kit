"""Question-set path resolution shared by all three hook shims (jak-vqy, review Q01).

Canonical name `JEV_KIT_HOOK_QUESTION_SET_PATH`; deprecated alias `JEV_KIT_QUESTION_SET_PATH`;
canonical wins when both are set; with neither, the default is an absolute path to the
repository's tool-call-gate.json, resolved from the module path, not the working directory.
A relative path from any source (canonical env, alias env, explicit argument) is rejected
through the config-failure path: shadow gives no decision, enforce fails closed, and the
engine is never called. Every case runs through a shim's public entry point with a capturing
runner; no engine, no network.
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
RELATIVE = "skills/jev-runtime/questions/tool-call-gate.json"
SOURCES = ("canonical", "alias", "argument")

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

Shim = Callable[[Mode, str | None, Any], Any]


def _run_claude(mode: Mode, path: str | None, runner: Any) -> Any:
    return claude_shim.handle_claude_event(
        _CLAUDE_EVENT, mode=mode, question_set_path=path, runner=runner
    )


def _run_codex(mode: Mode, path: str | None, runner: Any) -> Any:
    return codex_shim.handle_codex_event(
        _CODEX_EVENT, mode=mode, question_set_path=path, runner=runner
    )


def _run_hermes(mode: Mode, path: str | None, runner: Any) -> Any:
    return hermes_shim.pre_tool_call(
        None, _HERMES_EVENT, mode=mode, question_set_path=path, runner=runner
    )


SHIMS: dict[str, Shim] = {"claude": _run_claude, "codex": _run_codex, "hermes": _run_hermes}


def _accept(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "records": [{"route": "accept", "label": "no", "allow_labels": ["no"], "is_mock": True}],
    }


def _call(
    shim: str, mode: Mode = Mode.SHADOW, path: str | None = None
) -> tuple[list[dict[str, Any]], Any]:
    captured: list[dict[str, Any]] = []

    def runner(request: dict[str, Any]) -> dict[str, Any]:
        captured.append(request)
        return _accept(request)

    return captured, SHIMS[shim](mode, path, runner)


def _captured_path(shim: str) -> str:
    captured, _ = _call(shim)
    assert len(captured) == 1
    path = captured[0]["question_set_path"]
    assert isinstance(path, str)
    return path


def _select(source: str, value: str, monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Supply `value` through one source; return the explicit argument, if any."""
    if source == "canonical":
        monkeypatch.setenv(CANONICAL, value)
        return None
    if source == "alias":
        monkeypatch.setenv(ALIAS, value)
        return None
    return value


def _is_no_decision(shim: str, result: Any) -> bool:
    if shim == "hermes":
        return result is None
    return isinstance(result, dict) and "hookSpecificOutput" not in result


def _is_fail_closed(shim: str, result: Any) -> bool:
    if shim == "hermes":
        return isinstance(result, dict) and result.get("action") == "block"
    decision = result["hookSpecificOutput"]["permissionDecision"]
    return bool(decision == ("deny" if shim == "codex" else "ask"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CANONICAL, raising=False)
    monkeypatch.delenv(ALIAS, raising=False)


@pytest.fixture
def abs_a(tmp_path: Path) -> str:
    return str((tmp_path / "a.json").resolve())


@pytest.fixture
def abs_b(tmp_path: Path) -> str:
    return str((tmp_path / "b.json").resolve())


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_canonical_name_is_read(shim: str, abs_a: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CANONICAL, abs_a)
    assert _captured_path(shim) == abs_a


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_deprecated_alias_is_read(shim: str, abs_a: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALIAS, abs_a)
    assert _captured_path(shim) == abs_a


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_canonical_wins_when_both_set(
    shim: str, abs_a: str, abs_b: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CANONICAL, abs_a)
    monkeypatch.setenv(ALIAS, abs_b)
    assert _captured_path(shim) == abs_a


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_blank_canonical_falls_through_to_alias(
    shim: str, abs_a: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CANONICAL, "  ")
    monkeypatch.setenv(ALIAS, abs_a)
    assert _captured_path(shim) == abs_a


@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_default_is_absolute_and_independent_of_cwd(
    shim: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path(_captured_path(shim))
    assert path.is_absolute()
    assert path.is_file()
    assert path == REPO_GATE


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_absolute_path_from_each_source_reaches_the_gate(
    shim: str, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    arg = _select(source, str(REPO_GATE), monkeypatch)
    captured, _ = _call(shim, Mode.ENFORCE, arg)
    assert [r["question_set_path"] for r in captured] == [str(REPO_GATE)]


@pytest.mark.parametrize("mode", [Mode.SHADOW, Mode.ENFORCE])
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("shim", sorted(SHIMS))
def test_relative_path_from_each_source_is_rejected(
    shim: str, source: str, mode: Mode, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run from the repository root, where the relative path would resolve to a real file:
    # it must still be rejected, not resolved against the working directory.
    monkeypatch.chdir(REPO_GATE.parents[3])
    arg = _select(source, RELATIVE, monkeypatch)
    captured, result = _call(shim, mode, arg)
    assert captured == []  # never reaches the engine, never falls back to the default
    if mode is Mode.SHADOW:
        assert _is_no_decision(shim, result)
    else:
        assert _is_fail_closed(shim, result)
