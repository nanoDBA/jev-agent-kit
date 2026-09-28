"""Hermes host shim tests (Phase 3): allow maps to `None`, ask maps to a Hermes block payload,
shadow never blocks (even on a slow runner), and `register` wires the plugin hook contract. All
use an injected fake runner (via the core's `runner` param); no engine, no network."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from jev_kit.hooks import hermes
from jev_kit.types import Mode

QS = str(
    Path(__file__).resolve().parents[1]
    / "skills"
    / "jev-runtime"
    / "questions"
    / "tool-call-gate.json"
)

TOOL_CALL: dict[str, Any] = {
    "tool_name": "terminal",
    "args": {"command": "rm -rf /data", "target": "db01"},
    "task_id": "prod",
}


class FakeCtx:
    """A minimal stand-in for a Hermes plugin context."""

    def __init__(self) -> None:
        self.hooks: dict[str, Any] = {}

    def register_hook(self, event: str, callback: Any) -> None:
        self.hooks[event] = callback


def ok(records: list[dict[str, Any]]) -> Any:
    return lambda req: {"schema_version": 1, "status": "ok", "records": records}


def test_enforce_all_accept_allows() -> None:
    runner = ok([{"route": "accept", "label": "no", "allow_labels": ["no"], "is_mock": False}])
    result = hermes.pre_tool_call(
        FakeCtx(), TOOL_CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner
    )
    assert result is None


def test_enforce_ask_blocks() -> None:
    runner = ok([{"route": "ask", "fail_reason": "uncalibrated", "is_mock": False}])
    result = hermes.pre_tool_call(
        FakeCtx(), TOOL_CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner
    )
    assert result == {
        "action": "block",
        "message": "jev-kit gate: blocked for human review. (reason: uncalibrated)",
    }


def test_shadow_never_blocks_even_when_gate_would_ask() -> None:
    runner = ok([{"route": "ask", "fail_reason": "uncalibrated", "is_mock": False}])
    result = hermes.pre_tool_call(
        FakeCtx(), TOOL_CALL, mode=Mode.SHADOW, question_set_path=QS, runner=runner
    )
    assert result is None


def test_shadow_never_blocks_even_on_slow_runner_past_self_deadline() -> None:
    def slow(req: dict[str, Any]) -> dict[str, Any]:
        time.sleep(2.0)
        return {"schema_version": 1, "status": "ok", "records": [{"route": "accept"}]}

    result = hermes.pre_tool_call(
        FakeCtx(),
        TOOL_CALL,
        mode=Mode.SHADOW,
        question_set_path=QS,
        runner=slow,
        self_deadline_s=0.2,
    )
    assert result is None


def test_enforce_slow_runner_past_self_deadline_blocks() -> None:
    def slow(req: dict[str, Any]) -> dict[str, Any]:
        time.sleep(2.0)
        return {"schema_version": 1, "status": "ok", "records": [{"route": "accept"}]}

    result = hermes.pre_tool_call(
        FakeCtx(),
        TOOL_CALL,
        mode=Mode.ENFORCE,
        question_set_path=QS,
        runner=slow,
        self_deadline_s=0.2,
    )
    assert result is not None
    assert result["action"] == "block"


def test_unparseable_event_shadow_allows() -> None:
    result = hermes.pre_tool_call(
        FakeCtx(), object(), mode=Mode.SHADOW, question_set_path=QS, runner=ok([])
    )
    assert result is None


def test_unparseable_event_enforce_blocks() -> None:
    result = hermes.pre_tool_call(
        FakeCtx(), {"nothing": "useful"}, mode=Mode.ENFORCE, question_set_path=QS, runner=ok([])
    )
    assert result == {
        "action": "block",
        "message": "jev-kit gate: blocked for human review. (reason: unparseable_event)",
    }


def test_register_wires_pre_tool_call_hook() -> None:
    ctx = FakeCtx()
    hermes.register(ctx, mode=Mode.ENFORCE, question_set_path=QS,
                    runner=ok([{"route": "accept", "label": "no", "allow_labels": ["no"]}]))
    assert "pre_tool_call" in ctx.hooks

    result = ctx.hooks["pre_tool_call"](TOOL_CALL)
    assert result is None


def test_register_hook_accepts_kwarg_calling_convention() -> None:
    ctx = FakeCtx()
    runner = ok([{"route": "ask", "fail_reason": "uncalibrated", "is_mock": False}])
    hermes.register(ctx, mode=Mode.ENFORCE, question_set_path=QS, runner=runner)

    handler = ctx.hooks["pre_tool_call"]
    result = handler(tool_name="terminal", args={"command": "rm -rf /data"}, task_id="prod")
    assert result is not None
    assert result["action"] == "block"


def test_register_defaults_to_shadow_and_never_blocks(monkeypatch: Any) -> None:
    monkeypatch.delenv("JEV_KIT_HOOK_MODE", raising=False)
    ctx = FakeCtx()
    runner = ok([{"route": "ask", "fail_reason": "uncalibrated", "is_mock": False}])
    hermes.register(ctx, question_set_path=QS, runner=runner)

    result = ctx.hooks["pre_tool_call"](TOOL_CALL)
    assert result is None
