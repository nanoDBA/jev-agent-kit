"""The shared hook core: tool-call event -> decision -> host-neutral outcome (Phase 3).

Two outcomes, never a raw allow-on-uncertainty: `ALLOW` or `ASK` (there is no BLOCK; a gate
fails to ASK, which a host maps to its human-approval path). Safety rules baked in here so
every host shim inherits them:

- Shadow mode never changes host behavior: it always returns ALLOW, even on failure or
  timeout, so it can never accidentally block (Hermes fails closed at 30s).
- The core owns its own deadline. It runs the engine through the injected runner in a worker
  and abandons a result that misses the deadline, synthesizing the fail-closed outcome, so it
  never depends on a host hook timeout to fail closed (Codex fails open at 600s).
- Enforce fails closed to ASK on any failure, timeout, or gate that did not clear.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jev_kit.types import Mode

# The default self-deadline, chosen to sit well under Hermes' 30s fail-closed hook timeout.
DEFAULT_SELF_DEADLINE_S = 8.0

# A runner takes a JSON decision request and returns a JSON response. The default is a
# child-process runner (watchdog); tests inject a fake.
Runner = Callable[[dict[str, Any]], dict[str, Any]]


class HookOutcome(StrEnum):
    ALLOW = "allow"
    ASK = "ask"


@dataclass(frozen=True)
class ToolCall:
    command: str
    target: str | None = None
    context: str | None = None


@dataclass(frozen=True)
class HookResult:
    outcome: HookOutcome
    route: str | None  # the engine route behind the outcome, for logging (may be None)
    reason: str | None  # fail reason when the outcome is a failure
    is_mock: bool
    timed_out: bool = False


def _default_runner(request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    from jev_kit.hooks.watchdog import run_child

    # The child gets slightly less than the core deadline so its own kill fires first.
    return run_child(request, timeout_s=max(0.1, timeout_s - 0.5))


def _fail_closed(
    mode: Mode, reason: str, *, is_mock: bool = False, timed_out: bool = False
) -> HookResult:
    # Shadow never changes behavior; enforce fails closed to ask.
    outcome = HookOutcome.ALLOW if mode is Mode.SHADOW else HookOutcome.ASK
    return HookResult(outcome, None, reason, is_mock, timed_out)


def decide_tool_call(
    call: ToolCall,
    *,
    mode: Mode,
    question_set_path: str,
    runner: Runner | None = None,
    self_deadline_s: float = DEFAULT_SELF_DEADLINE_S,
) -> HookResult:
    """Judge one tool call. Returns ALLOW or ASK; never raises for runtime conditions."""
    request = {
        "schema_version": 1,
        "op": "decide",
        "question_set_path": question_set_path,
        "mode": mode.value,
        "state": {
            "command": call.command,
            "target": call.target,
            "context": call.context,
        },
    }
    run: Callable[[dict[str, Any]], dict[str, Any]]
    run = runner if runner is not None else (lambda req: _default_runner(req, self_deadline_s))

    # Own the deadline independently of the runner: on a miss, abandon the worker without
    # joining it (a plain `with` block would join on exit and defeat the bound), so the core
    # itself returns within self_deadline even if the runner hangs. The watchdog child is also
    # killed on its own shorter timeout (finding Phase3/4 MAJOR-2).
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(run, request)
        try:
            response = future.result(timeout=self_deadline_s)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False, cancel_futures=True)
            return _fail_closed(mode, "timeout", timed_out=True)
        except Exception:
            pool.shutdown(wait=False, cancel_futures=True)
            return _fail_closed(mode, "internal")
    finally:
        # Do not block on a still-running worker; a hung runner must not hang the hook.
        pool.shutdown(wait=False, cancel_futures=True)

    return _map_response(response, mode)


def _map_response(response: dict[str, Any], mode: Mode) -> HookResult:
    if not isinstance(response, dict) or response.get("status") != "ok":
        reason = response.get("reason") if isinstance(response, dict) else None
        return _fail_closed(mode, reason or "error")

    records = response.get("records")
    if not isinstance(records, list) or not records:
        return _fail_closed(mode, "no_records")

    is_mock = any(bool(r.get("is_mock")) for r in records if isinstance(r, dict))

    if mode is Mode.SHADOW:
        # Shadow never changes behavior: allow regardless, but surface the would-be route.
        would = next(
            (r.get("would_route") for r in records if isinstance(r, dict) and r.get("would_route")),
            None,
        )
        return HookResult(HookOutcome.ALLOW, would, None, is_mock)

    # Enforce: any gate not clearing to accept means ask (fail closed). The tool-call-gate set
    # is all gate questions, so a single non-accept route escalates.
    routes = [r.get("route") for r in records if isinstance(r, dict)]
    # `routes` must be non-empty: an all-non-dict records list would make all() vacuously true
    # and allow in enforce (finding Phase3/4 MAJOR-1). No route may be missing either.
    if routes and len(routes) == len(records) and all(route == "accept" for route in routes):
        return HookResult(HookOutcome.ALLOW, "accept", None, is_mock)
    reason = next(
        (r.get("fail_reason") for r in records if isinstance(r, dict) and r.get("fail_reason")),
        None,
    )
    return HookResult(HookOutcome.ASK, "ask", reason, is_mock)
