"""Tests for the Claude Code and Codex PreToolUse hook shims (Phase 3).

All decision logic lives in ``jev_kit.hooks.core``; these tests only check that each shim maps
events to `ToolCall`s and `HookResult`s to the right host response shape, using fake runners
injected through the ``runner`` parameter. No test spawns the watchdog child process or makes a
network call: whenever a case does not inject a runner, it exercises the "event cannot be
parsed" path, which never calls the engine at all.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from jev_kit.hooks import claude as claude_shim
from jev_kit.hooks import codex as codex_shim
from jev_kit.types import Mode

_QUESTION_SET_PATH = "skills/jev-runtime/questions/tool-call-gate.json"

_CLAUDE_EVENT: dict[str, Any] = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /tmp/x", "description": "cleanup"},
    "cwd": "/repo",
}

_CODEX_EVENT: dict[str, Any] = {
    "hook_event_name": "PreToolUse",
    "tool_name": "shell",
    "tool_input": {"command": "rm -rf /tmp/x"},
    "cwd": "/repo",
    "turn_id": "t1",
}

_UNPARSABLE_EVENT: dict[str, Any] = {"hook_event_name": "PreToolUse", "no_tool_name_here": True}


def _accept_runner(_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "records": [{"route": "accept", "would_route": "accept", "is_mock": True}],
    }


def _ask_runner(_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "records": [
            {
                "route": "ask",
                "would_route": "ask",
                "fail_reason": "low_confidence",
                "is_mock": True,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Claude Code shim
# ---------------------------------------------------------------------------


def test_claude_allow_on_accept_enforce() -> None:
    response = claude_shim.handle_claude_event(
        _CLAUDE_EVENT,
        mode=Mode.ENFORCE,
        question_set_path=_QUESTION_SET_PATH,
        runner=_accept_runner,
    )
    assert response == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def test_claude_asks_on_gate_ask_enforce() -> None:
    response = claude_shim.handle_claude_event(
        _CLAUDE_EVENT,
        mode=Mode.ENFORCE,
        question_set_path=_QUESTION_SET_PATH,
        runner=_ask_runner,
    )
    inner = response["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "ask"
    assert inner["permissionDecisionReason"] == "low_confidence"


def test_claude_shadow_allows_despite_ask_runner() -> None:
    response = claude_shim.handle_claude_event(
        _CLAUDE_EVENT,
        mode=Mode.SHADOW,
        question_set_path=_QUESTION_SET_PATH,
        runner=_ask_runner,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_claude_unparsable_event_fails_safely() -> None:
    shadow_response = claude_shim.handle_claude_event(
        _UNPARSABLE_EVENT, mode=Mode.SHADOW, question_set_path=_QUESTION_SET_PATH
    )
    assert shadow_response["hookSpecificOutput"]["permissionDecision"] == "allow"

    enforce_response = claude_shim.handle_claude_event(
        _UNPARSABLE_EVENT, mode=Mode.ENFORCE, question_set_path=_QUESTION_SET_PATH
    )
    assert enforce_response["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_claude_main_defaults_to_shadow_allow_on_unparsable_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JEV_KIT_HOOK_MODE", raising=False)
    monkeypatch.delenv("JEV_KIT_QUESTION_SET_PATH", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("not valid json"))

    exit_code = claude_shim.main([])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_claude_main_enforce_via_argv_asks_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    exit_code = claude_shim.main(["--mode", "enforce"])

    # Claude Code always reads the JSON decision on exit 0; it never depends on a non-zero exit.
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


# ---------------------------------------------------------------------------
# Codex shim
# ---------------------------------------------------------------------------


def test_codex_allow_on_accept_enforce() -> None:
    response = codex_shim.handle_codex_event(
        _CODEX_EVENT,
        mode=Mode.ENFORCE,
        question_set_path=_QUESTION_SET_PATH,
        runner=_accept_runner,
    )
    assert response == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def test_codex_denies_on_gate_ask_enforce() -> None:
    response = codex_shim.handle_codex_event(
        _CODEX_EVENT,
        mode=Mode.ENFORCE,
        question_set_path=_QUESTION_SET_PATH,
        runner=_ask_runner,
    )
    inner = response["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    # Codex has no documented "ask" decision value, so the core's ASK maps to "deny".
    assert inner["permissionDecision"] == "deny"
    assert inner["permissionDecisionReason"] == "low_confidence"


def test_codex_shadow_allows_despite_ask_runner() -> None:
    response = codex_shim.handle_codex_event(
        _CODEX_EVENT,
        mode=Mode.SHADOW,
        question_set_path=_QUESTION_SET_PATH,
        runner=_ask_runner,
    )
    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_codex_unparsable_event_fails_safely() -> None:
    shadow_response = codex_shim.handle_codex_event(
        _UNPARSABLE_EVENT, mode=Mode.SHADOW, question_set_path=_QUESTION_SET_PATH
    )
    assert shadow_response["hookSpecificOutput"]["permissionDecision"] == "allow"

    enforce_response = codex_shim.handle_codex_event(
        _UNPARSABLE_EVENT, mode=Mode.ENFORCE, question_set_path=_QUESTION_SET_PATH
    )
    assert enforce_response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_main_defaults_to_shadow_allow_on_unparsable_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JEV_KIT_HOOK_MODE", raising=False)
    monkeypatch.delenv("JEV_KIT_QUESTION_SET_PATH", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("not valid json"))

    exit_code = codex_shim.main([])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_codex_main_enforce_via_argv_denies_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    exit_code = codex_shim.main(["--mode", "enforce"])

    # Codex fails open on a hook timeout, so a deny must actively signal via a non-zero exit
    # rather than ever depending on the host's 600s timeout to produce a safe outcome.
    assert exit_code == codex_shim._EXIT_DENY
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert captured.err.strip() != ""
