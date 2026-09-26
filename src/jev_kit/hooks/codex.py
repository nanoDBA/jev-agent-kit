"""Codex PreToolUse hook shim (Phase 3).

Maps a Codex `PreToolUse` hook event to the shared core (`jev_kit.hooks.core`) and translates
the core's host-neutral `HookResult` into the JSON response Codex hooks expect on stdout. All
decision logic lives in `jev_kit.hooks.core`; this module only does I/O and field mapping, per
docs/specs/phase-3-4-plan.md decision E4.

Codex fails **open** on a hook that does not answer within its configured timeout (default
600s; see docs/research/06 section 9 and docs/specs/phase-3-4-plan.md safety amendment 1). This
shim never relies on that timeout to produce a safe outcome: the core owns a short
`self_deadline_s` (default `jev_kit.hooks.core.DEFAULT_SELF_DEADLINE_S`, far under 600s) and
always returns a real decision well before the host's window closes, and `main` actively signals
a deny -- both in the JSON body and via a non-zero exit code -- rather than ever staying silent.

Event fields consumed (Codex `PreToolUse`, per docs/research/06 section 9 and
https://learn.chatgpt.com/docs/hooks)::

    {
      "hook_event_name": "PreToolUse",
      "tool_name": "shell",                # or "apply_patch", an MCP tool name, ...
      "tool_input": {"command": "...", ...},
      "cwd": "...",
      "model": "...",
      "turn_id": "...",
      ...
    }

Mapping to `ToolCall` mirrors the Claude Code shim:

- `command`: `tool_input["command"]` when present and a string (shell / apply_patch calls carry
  the command text there); otherwise the tool name itself.
- `target`: the first of `tool_input["file_path"]`, `["path"]`, `["url"]`, `["notebook_path"]`
  that is a non-empty string, else `None`.
- `context`: "key=value; ..." carrying the tool name, the event's `cwd`, and `turn_id` when
  present.

Note on `commandWindows` / `command_windows`: that field configures which command Codex itself
runs to invoke *this hook program* on Windows (a `hooks.json` / `config.toml` concern owned by
the installer), not a field on the tool-call event this module parses.

Response shape: `hookSpecificOutput.permissionDecision` of `"allow"` or `"deny"`, matching the
documented Codex hook contract (docs/research/06 section 9). The core's ASK outcome maps to
`"deny"` here: ASK means "do not let this proceed without a human", `"deny"` is the documented
way to block a Codex tool call, and `"ask"` is not a documented Codex decision value. `main`
backs this up with a non-zero exit and a stderr reason on deny, since Codex documents exit code
2 as a hard block "regardless of JSON content"; the shim never depends on the 600s timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from jev_kit.hooks.core import (
    DEFAULT_SELF_DEADLINE_S,
    HookOutcome,
    Runner,
    ToolCall,
    decide_tool_call,
)
from jev_kit.types import Mode

DEFAULT_QUESTION_SET_PATH = "skills/jev-runtime/questions/tool-call-gate.json"

_MODE_ENV_VAR = "JEV_KIT_HOOK_MODE"
_QUESTION_SET_PATH_ENV_VAR = "JEV_KIT_QUESTION_SET_PATH"

_TARGET_FIELDS = ("file_path", "path", "url", "notebook_path")

# Codex documents exit code 2 as a hard block regardless of JSON output (the same convention
# Claude Code uses). Used as a fail-closed backstop for ASK/deny; never relied on alone.
_EXIT_DENY = 2


def _mode_from_env() -> Mode:
    raw = os.environ.get(_MODE_ENV_VAR, "").strip().lower()
    if raw == "enforce":
        return Mode.ENFORCE
    # Default and any unrecognized value fall back to shadow: never enforce by accident.
    return Mode.SHADOW


def _question_set_path_from_env() -> str:
    raw = os.environ.get(_QUESTION_SET_PATH_ENV_VAR, "").strip()
    return raw or DEFAULT_QUESTION_SET_PATH


def _first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_codex_call(event: dict[str, Any]) -> ToolCall | None:
    """Extract a `ToolCall` from a Codex `PreToolUse` event, or `None` if unparsable."""
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name or not isinstance(tool_input, dict):
        return None

    if isinstance(tool_input.get("command"), str):
        command = str(tool_input["command"])
    else:
        command = tool_name

    target = _first_str(tool_input, _TARGET_FIELDS)

    context_parts = [f"tool={tool_name}"]
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        context_parts.append(f"cwd={cwd}")
    turn_id = event.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        context_parts.append(f"turn_id={turn_id}")

    return ToolCall(command=command, target=target, context="; ".join(context_parts))


def _build_response(outcome: HookOutcome, *, reason: str | None) -> dict[str, Any]:
    inner: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if outcome is HookOutcome.ALLOW else "deny",
    }
    if outcome is HookOutcome.ASK:
        inner["permissionDecisionReason"] = reason or "jev-kit: needs human review"
    return {"hookSpecificOutput": inner}


def handle_codex_event(
    event: dict[str, Any],
    *,
    mode: Mode | None = None,
    question_set_path: str | None = None,
    runner: Runner | None = None,
    self_deadline_s: float = DEFAULT_SELF_DEADLINE_S,
) -> dict[str, Any]:
    """Decide one Codex `PreToolUse` event and return its JSON hook response.

    `mode` and `question_set_path` fall back to environment variables
    (`JEV_KIT_HOOK_MODE`, `JEV_KIT_QUESTION_SET_PATH`) and then to shadow mode and
    `DEFAULT_QUESTION_SET_PATH` when neither is given. `self_deadline_s` defaults to the core's
    own `DEFAULT_SELF_DEADLINE_S`, far under Codex's 600s fail-open window, so this shim owns its
    deadline rather than trusting the host timeout (phase-3-4-plan.md safety amendment 1).

    Never raises: an event that cannot be parsed fails closed to ASK (rendered as `"deny"`) in
    enforce and ALLOW in shadow, matching the core's own shadow-never-blocks convention, without
    invoking the engine.
    """
    resolved_mode = mode if mode is not None else _mode_from_env()
    resolved_question_set_path = (
        question_set_path if question_set_path is not None else _question_set_path_from_env()
    )

    call = _extract_codex_call(event)
    if call is None:
        outcome = HookOutcome.ALLOW if resolved_mode is Mode.SHADOW else HookOutcome.ASK
        return _build_response(outcome, reason="unparsable_event")

    result = decide_tool_call(
        call,
        mode=resolved_mode,
        question_set_path=resolved_question_set_path,
        runner=runner,
        self_deadline_s=self_deadline_s,
    )
    return _build_response(result.outcome, reason=result.reason)


def _parse_argv(argv: Sequence[str]) -> tuple[Mode | None, str | None]:
    parser = argparse.ArgumentParser(prog="jev-kit-hook-codex", add_help=False)
    parser.add_argument("--mode", choices=("shadow", "enforce"), default=None)
    parser.add_argument("--question-set-path", default=None)
    try:
        known, _ = parser.parse_known_args(list(argv))
    except SystemExit:
        # Never let a hook-startup argument problem crash the shim; fall back to env/defaults.
        return None, None
    mode = Mode(known.mode) if known.mode else None
    question_set_path = str(known.question_set_path) if known.question_set_path else None
    return mode, question_set_path


def main(argv: Sequence[str] | None = None) -> int:
    """Read one `PreToolUse` event JSON from stdin and write the hook response JSON.

    Exits 0 on allow. On deny (the core's ASK, fail-closed under enforce), also exits with
    `_EXIT_DENY` and writes the reason to stderr: Codex fails open on a hook timeout, so a
    silent or ambiguous response is never safe here, and the exit code is a documented hard
    block "regardless of JSON content" that backs up the JSON decision.
    """
    mode, question_set_path = _parse_argv(sys.argv[1:] if argv is None else argv)

    raw_text = sys.stdin.read()
    event: Any
    try:
        event = json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = {}
    if not isinstance(event, dict):
        event = {}

    response = handle_codex_event(event, mode=mode, question_set_path=question_set_path)
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")

    hook_output_raw: Any = response.get("hookSpecificOutput")
    hook_output: dict[str, Any] = hook_output_raw if isinstance(hook_output_raw, dict) else {}
    decision = hook_output.get("permissionDecision")
    if decision == "deny":
        reason = hook_output.get("permissionDecisionReason")
        sys.stderr.write(f"{reason or 'jev-kit: blocked pending human review'}\n")
        return _EXIT_DENY
    return 0


if __name__ == "__main__":
    sys.exit(main())
