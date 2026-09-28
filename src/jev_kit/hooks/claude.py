"""Claude Code PreToolUse hook shim (Phase 3).

Maps a Claude Code `PreToolUse` hook event to the shared core (`jev_kit.hooks.core`) and
translates the core's host-neutral `HookResult` into the JSON response Claude Code expects on
stdout. All decision logic (the tool-call-gate question set, shadow/enforce, the self-deadline,
fail-closed/fail-open) lives in `jev_kit.hooks.core`; this module only does I/O and field
mapping, per docs/specs/phase-3-4-plan.md decision E4.

Event fields consumed (Claude Code `PreToolUse`, per docs/research/06 section 9 and
https://code.claude.com/docs/en/hooks)::

    {
      "hook_event_name": "PreToolUse",
      "tool_name": "Bash",                 # or "Edit", "Write", "Read", "mcp__...", ...
      "tool_input": {"command": "...", "description": "...", ...},
      "cwd": "...",
      ...
    }

Mapping to `ToolCall`:

- `command`: the first non-empty string found in `tool_input` among `command`, `script`, and
  `code` (in that order) -- the actual command/script text that the tool-call-gate questions
  judge, whatever the field is named for a given tool (for example a `Bash` call carries it in
  `command`; a PowerShell/pwsh call may carry it in `command` or `script`). Only when none of
  those fields is present does this fall back to the tool name itself (for example "Edit"),
  since then there is genuinely no command text to inspect. A shell-executing tool must never be
  reduced to its bare name when it carries a command string: doing so would make a harmless and
  a destructive invocation of that tool indistinguishable to the gate (H13).
- `target`: the first of `tool_input["file_path"]`, `["path"]`, `["url"]`, `["notebook_path"]`
  that is a non-empty string, else `None`.
- `context`: a short "key=value; key=value" string carrying the tool name, an optional
  `tool_input["description"]`, for the gate's free-text context field. The event's `cwd` is
  never sent: folder names often name a project or client, and the gate does not need them.

Response shape (Claude Code `PreToolUse`, current documented contract): a
`hookSpecificOutput.permissionDecision` of `"allow"` or `"ask"`. Claude Code prompts the user on
`"ask"`, which is exactly the core's ASK outcome, so no lossy allow/block translation is needed.
The legacy top-level `{"decision": "approve"}` shape exists but is not used here in favor of the
current `hookSpecificOutput` mechanism, which is what actually distinguishes "ask" from "allow".
`main` always writes a response and exits 0, per the documented "exit 0: Claude Code reads JSON
output for decision" contract; it never depends on a non-zero exit to communicate the decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from jev_kit.hooks.core import (
    DEFAULT_QUESTION_SET_PATH as DEFAULT_QUESTION_SET_PATH,
)
from jev_kit.hooks.core import (
    DEFAULT_SELF_DEADLINE_S,
    HookOutcome,
    Runner,
    ToolCall,
    decide_tool_call,
    producer_id,
    question_set_path_from_env,
)
from jev_kit.types import Mode

_MODE_ENV_VAR = "JEV_KIT_HOOK_MODE"

_TARGET_FIELDS = ("file_path", "path", "url", "notebook_path")

# Fields that may carry the actual command/script text for a tool call, in priority order.
# Checked regardless of tool name: a shell-executing tool (Bash, PowerShell/pwsh, and any other
# tool a host names differently) is identified by carrying one of these fields, not by name, so
# a PowerShell call is never collapsed to its bare tool name while still carrying real script
# text (H13).
_COMMAND_FIELDS = ("command", "script", "code")

# Identity of this hook's state preprocessing; part of every gate fingerprint.
_PRODUCER = producer_id("claude", __file__)


def _mode_from_env() -> Mode:
    raw = os.environ.get(_MODE_ENV_VAR, "").strip().lower()
    if raw == "enforce":
        return Mode.ENFORCE
    # Default and any unrecognized value fall back to shadow: never enforce by accident.
    return Mode.SHADOW




def _first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_claude_call(event: dict[str, Any]) -> ToolCall | None:
    """Extract a `ToolCall` from a Claude Code `PreToolUse` event, or `None` if unparsable."""
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name or not isinstance(tool_input, dict):
        return None

    command_text = _first_str(tool_input, _COMMAND_FIELDS)
    command = command_text if command_text is not None else tool_name

    target = _first_str(tool_input, _TARGET_FIELDS)

    context_parts = [f"tool={tool_name}"]
    description = tool_input.get("description")
    if isinstance(description, str) and description:
        context_parts.append(f"description={description}")

    return ToolCall(command=command, target=target, context="; ".join(context_parts))


def _build_response(outcome: HookOutcome, *, reason: str | None) -> dict[str, Any]:
    # A gate only ever ADDS friction, never removes it: an affirmative "allow" would skip
    # Claude's normal permission prompt, so ALLOW returns a decision-free response and only ASK
    # emits a decision (finding H2). This holds in shadow and enforce alike.
    if outcome is HookOutcome.ALLOW:
        return {}
    inner: dict[str, Any] = {"hookEventName": "PreToolUse", "permissionDecision": "ask"}
    if reason:
        inner["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": inner}


def handle_claude_event(
    event: dict[str, Any],
    *,
    mode: Mode | None = None,
    question_set_path: str | None = None,
    runner: Runner | None = None,
    self_deadline_s: float = DEFAULT_SELF_DEADLINE_S,
) -> dict[str, Any]:
    """Decide one Claude Code `PreToolUse` event and return its JSON hook response.

    `mode` and `question_set_path` fall back to environment variables
    (`JEV_KIT_HOOK_MODE`, `JEV_KIT_HOOK_QUESTION_SET_PATH` or its deprecated alias
    `JEV_KIT_QUESTION_SET_PATH`) and then to shadow mode and
    `DEFAULT_QUESTION_SET_PATH` when neither is given. `self_deadline_s` defaults to the core's
    own `DEFAULT_SELF_DEADLINE_S` so this shim owns its deadline rather than trusting a host
    timeout (phase-3-4-plan.md safety amendment 1).

    Never raises: an event that cannot be parsed fails closed to ASK in enforce and ALLOW in
    shadow, matching the core's own shadow-never-blocks convention, without invoking the engine.
    """
    resolved_mode = mode if mode is not None else _mode_from_env()
    resolved_question_set_path = (
        question_set_path if question_set_path is not None else question_set_path_from_env()
    )

    call = _extract_claude_call(event)
    if call is None:
        outcome = HookOutcome.ALLOW if resolved_mode is Mode.SHADOW else HookOutcome.ASK
        return _build_response(outcome, reason="unparsable_event")

    result = decide_tool_call(
        call,
        mode=resolved_mode,
        question_set_path=resolved_question_set_path,
        producer=_PRODUCER,
        runner=runner,
        self_deadline_s=self_deadline_s,
    )
    return _build_response(result.outcome, reason=result.reason)


def _parse_argv(argv: Sequence[str]) -> tuple[Mode | None, str | None]:
    parser = argparse.ArgumentParser(prog="jev-kit-hook-claude", add_help=False)
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
    """Read one `PreToolUse` event JSON from stdin and write the hook response JSON, exit 0."""
    mode, question_set_path = _parse_argv(sys.argv[1:] if argv is None else argv)

    raw_text = sys.stdin.read()
    event: Any
    try:
        event = json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = {}
    if not isinstance(event, dict):
        event = {}

    response = handle_claude_event(event, mode=mode, question_set_path=question_set_path)
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
