"""Hermes host shim for the `pre_tool_call` hook (Phase 3).

A Hermes plugin registers a callback via `ctx.register_hook("pre_tool_call", fn)`. The callback
allows a tool call by returning `None`, or blocks it by returning `{"action": "block",
"message": ...}`, which stops the call and returns the message to the model. The shim
deliberately does not use Hermes' `approve` action: Hermes keys approvals by tool name (one
grant covers later calls of that tool) and auto-approves under YOLO, approvals off and some
unattended policies, so `block` is the only response that holds everywhere. Hermes fails
closed at a
`plugins.hook_callback_timeout` (default 30s) plus a 60s suppression window on timeout or an
uncaught exception (`docs/research/06-reverified-facts.md` section 9).

This shim holds no decision logic of its own: it extracts a `ToolCall` from the Hermes event and
delegates entirely to `hooks.core.decide_tool_call`, which owns the self-deadline and the
shadow/enforce mapping. Per the phase 3/4 safety amendments
(`docs/specs/phase-3-4-plan.md`), shadow must never accidentally block on Hermes, so this shim
always runs with the core's default self-deadline (well under Hermes' 30s) and never waits on
the host timeout to fail closed.

The Hermes `pre_tool_call` event shape is not pinned by a verified schema (see the "Unverified"
note in `docs/research/06-reverified-facts.md` section 9), so the event is read defensively: a
mapping-like object exposing a tool name/command and its arguments. An event that cannot be
parsed fails closed to ask in enforce and allow in shadow, and this shim never raises.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

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
_QUESTION_SET_ENV_VAR = "JEV_KIT_HOOK_QUESTION_SET_PATH"

_BLOCK_MESSAGE = "jev-kit gate: blocked for human review."


class HermesContext(Protocol):
    """The slice of the Hermes plugin context this shim depends on."""

    def register_hook(self, event: str, callback: Any) -> None: ...


def _mode_from_env() -> Mode:
    raw = os.environ.get(_MODE_ENV_VAR, Mode.SHADOW.value).strip().lower()
    try:
        return Mode(raw)
    except ValueError:
        # An unrecognized value must not silently arm enforce.
        return Mode.SHADOW


def _question_set_path_from_env() -> str:
    return os.environ.get(_QUESTION_SET_ENV_VAR, DEFAULT_QUESTION_SET_PATH)


def _resolve(mode: Mode | None, question_set_path: str | None) -> tuple[Mode, str]:
    resolved_mode = mode if mode is not None else _mode_from_env()
    resolved_path = (
        question_set_path if question_set_path is not None else _question_set_path_from_env()
    )
    return resolved_mode, resolved_path


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _extract_tool_call(event: Any) -> ToolCall | None:
    """Build a `ToolCall` from a Hermes `pre_tool_call` event.

    The event is expected to behave like a mapping carrying a tool name (or command) and its
    arguments. Hermes event shapes are not pinned by a verified schema, so this reads
    defensively (`.get`, never attribute access) and returns `None` for anything unrecognized
    rather than raising; the caller fails closed (or allows, in shadow) on `None`.
    """
    get = getattr(event, "get", None)
    if not callable(get):
        return None
    try:
        args = event.get("args")
        args_map: dict[str, Any] = args if isinstance(args, dict) else {}

        command = (
            args_map.get("command")
            or args_map.get("content")
            or event.get("command")
            or event.get("tool_name")
            or event.get("name")
            or event.get("tool")
        )
        command_str = _as_str(command)
        if not command_str:
            return None

        target = args_map.get("target") or args_map.get("path") or args_map.get("file")
        context = event.get("context") or event.get("task_id")

        return ToolCall(command=command_str, target=_as_str(target), context=_as_str(context))
    except Exception:
        return None


def _block(reason: str | None) -> dict[str, str]:
    if reason is None:
        return {"action": "block", "message": _BLOCK_MESSAGE}
    return {"action": "block", "message": f"{_BLOCK_MESSAGE} (reason: {reason})"}


def pre_tool_call(
    ctx: Any,
    tool_call: Any,
    *,
    mode: Mode | None = None,
    question_set_path: str | None = None,
    runner: Runner | None = None,
    self_deadline_s: float = DEFAULT_SELF_DEADLINE_S,
) -> dict[str, str] | None:
    """Hermes `pre_tool_call` callback: allow (`None`) or block (a Hermes block payload).

    `ctx` is accepted but unused beyond being part of the Hermes callback shape; this shim keeps
    no state on it. All decision logic lives in `hooks.core.decide_tool_call`. `mode` and
    `question_set_path` default from the environment when not given (`JEV_KIT_HOOK_MODE`,
    default shadow; `JEV_KIT_HOOK_QUESTION_SET_PATH`, default the tool-call-gate set).

    Never raises: an unparseable event or any internal failure fails closed to a block in
    enforce and to allow in shadow, matching the core's own fail-closed behavior.
    """
    del ctx
    resolved_mode, resolved_path = _resolve(mode, question_set_path)

    call = _extract_tool_call(tool_call)
    if call is None:
        return None if resolved_mode is Mode.SHADOW else _block("unparseable_event")

    try:
        result = decide_tool_call(
            call,
            mode=resolved_mode,
            question_set_path=resolved_path,
            runner=runner,
            self_deadline_s=self_deadline_s,
        )
    except Exception:
        # decide_tool_call never raises for runtime conditions; this is a last-resort net so the
        # shim itself can never crash a Hermes turn.
        return None if resolved_mode is Mode.SHADOW else _block("internal")

    if result.outcome is HookOutcome.ALLOW:
        return None
    return _block(result.reason)


def register(
    ctx: HermesContext,
    *,
    mode: Mode | None = None,
    question_set_path: str | None = None,
    runner: Runner | None = None,
) -> None:
    """Register the gate as a Hermes `pre_tool_call` plugin hook.

    Resolves mode and the question-set path once, at registration time, from the given
    arguments or the environment, then wires a closure over `ctx` so the registered callback
    matches Hermes' calling convention (the event only; `ctx` is captured, not re-passed).
    Accepts either a single event argument or Hermes' keyword form
    (`tool_name=...`, `args=...`, `task_id=...`) since the exact calling convention is not
    pinned by a verified schema.
    """
    resolved_mode, resolved_path = _resolve(mode, question_set_path)

    def _on_pre_tool_call(tool_call: Any = None, **kwargs: Any) -> dict[str, str] | None:
        event = tool_call if tool_call is not None else kwargs
        return pre_tool_call(
            ctx,
            event,
            mode=resolved_mode,
            question_set_path=resolved_path,
            runner=runner,
        )

    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
