"""Hook core tests (Phase 3): shadow never blocks, enforce fails closed to ask, the adapter
owns its own deadline. All use an injected fake runner; no engine, no network."""

from __future__ import annotations

import time
from typing import Any

from jev_kit.hooks.core import HookOutcome, ToolCall, decide_tool_call
from jev_kit.types import Mode

CALL = ToolCall(command="rm -rf /data", target="db01", context="prod")
QS = "skills/jev-runtime/questions/tool-call-gate.json"


def ok(records: list[dict[str, Any]]) -> Any:
    return lambda req: {"schema_version": 1, "status": "ok", "records": records}


def test_shadow_always_allows_even_on_failure() -> None:
    for runner in (
        ok([{"route": "ask", "fail_reason": "uncalibrated", "is_mock": False}]),
        lambda req: {"schema_version": 1, "status": "error", "reason": "transport", "records": []},
        lambda req: (_ for _ in ()).throw(RuntimeError("boom")),  # runner raises
    ):
        res = decide_tool_call(CALL, mode=Mode.SHADOW, question_set_path=QS, runner=runner)
        assert res.outcome is HookOutcome.ALLOW


def test_enforce_all_accept_allows() -> None:
    runner = ok([
        {"route": "accept", "is_mock": False},
        {"route": "accept", "is_mock": False},
    ])
    res = decide_tool_call(CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner)
    assert res.outcome is HookOutcome.ALLOW


def test_enforce_any_ask_escalates() -> None:
    runner = ok([
        {"route": "accept", "is_mock": False},
        {"route": "ask", "fail_reason": "uncalibrated", "is_mock": False},
    ])
    res = decide_tool_call(CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner)
    assert res.outcome is HookOutcome.ASK
    assert res.reason == "uncalibrated"


def test_enforce_failure_is_ask() -> None:
    def runner(req: dict[str, Any]) -> dict[str, Any]:
        return {"schema_version": 1, "status": "error", "reason": "egress_blocked", "records": []}

    res = decide_tool_call(CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner)
    assert res.outcome is HookOutcome.ASK


def test_enforce_mock_never_allows() -> None:
    # A mock record can only carry route ask (engine forces it); confirm the hook escalates.
    runner = ok([{"route": "ask", "is_mock": True, "fail_reason": None}])
    res = decide_tool_call(CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=runner)
    assert res.outcome is HookOutcome.ASK
    assert res.is_mock is True


def test_adapter_owns_deadline_enforce_slow_runner_asks() -> None:
    def slow(req: dict[str, Any]) -> dict[str, Any]:
        time.sleep(2.0)
        return {"schema_version": 1, "status": "ok", "records": [{"route": "accept"}]}

    res = decide_tool_call(
        CALL, mode=Mode.ENFORCE, question_set_path=QS, runner=slow, self_deadline_s=0.2
    )
    assert res.outcome is HookOutcome.ASK
    assert res.timed_out is True


def test_adapter_owns_deadline_shadow_slow_runner_allows() -> None:
    def slow(req: dict[str, Any]) -> dict[str, Any]:
        time.sleep(2.0)
        return {"schema_version": 1, "status": "ok", "records": [{"route": "accept"}]}

    res = decide_tool_call(
        CALL, mode=Mode.SHADOW, question_set_path=QS, runner=slow, self_deadline_s=0.2
    )
    assert res.outcome is HookOutcome.ALLOW  # shadow never blocks, even on timeout
    assert res.timed_out is True
