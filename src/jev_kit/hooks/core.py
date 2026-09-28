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
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jev_kit.types import Mode

# The default self-deadline, chosen to sit well under Hermes' 30s fail-closed hook timeout.
DEFAULT_SELF_DEADLINE_S = 8.0

#: Canonical environment variable naming the question set a hook uses. Matches the
#: `JEV_KIT_HOOK_MODE` prefix.
QUESTION_SET_PATH_ENV_VAR = "JEV_KIT_HOOK_QUESTION_SET_PATH"

#: Deprecated alias for QUESTION_SET_PATH_ENV_VAR, still read by every shim. When both are
#: set to a non-empty value, the canonical name wins.
QUESTION_SET_PATH_ENV_VAR_ALIAS = "JEV_KIT_QUESTION_SET_PATH"

# The skill's gate question set lives in the repository (it is not packaged into the wheel),
# at <repo>/skills/jev-runtime/questions/tool-call-gate.json, where this module sits at
# <repo>/src/jev_kit/hooks/core.py. Resolved from this module's own path, never from the
# host's working directory. If the file is not there (for example, an installed wheel without
# the repository), the path simply does not exist and the call fails the normal way: no
# decision in shadow, ask in enforce.
DEFAULT_QUESTION_SET_PATH = str(
    Path(__file__).resolve().parents[3]
    / "skills"
    / "jev-runtime"
    / "questions"
    / "tool-call-gate.json"
)


def question_set_path_from_env() -> str:
    """The hook question-set path: canonical env var, then the alias, then the default."""
    for name in (QUESTION_SET_PATH_ENV_VAR, QUESTION_SET_PATH_ENV_VAR_ALIAS):
        raw = os.environ.get(name, "").strip()
        if raw:
            return raw
    return DEFAULT_QUESTION_SET_PATH


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


PRODUCER_UNAVAILABLE = "unavailable"


def producer_id(name: str, shim_file: str) -> str:
    """Identity of a hook's state preprocessing: its own source plus this module's.

    Sent as the request's `producer`, which the engine binds into every fingerprint, so any
    change to how a hook builds state invalidates calibration made with the old code. Never
    raises: if the source cannot be read, returns PRODUCER_UNAVAILABLE, and the hook then
    fails the call through its normal path (allow in shadow, ask in enforce).
    """
    try:
        source = Path(shim_file).read_bytes() + bytes(1) + Path(__file__).read_bytes()
    except OSError:
        return PRODUCER_UNAVAILABLE
    # Normalize line endings so CRLF and LF checkouts of the same revision agree.
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return f"hook.{name}@src:{hashlib.sha256(normalized).hexdigest()[:28]}"


def decide_tool_call(
    call: ToolCall,
    *,
    mode: Mode,
    question_set_path: str,
    producer: str | None = None,
    runner: Runner | None = None,
    self_deadline_s: float = DEFAULT_SELF_DEADLINE_S,
) -> HookResult:
    """Judge one tool call. Returns ALLOW or ASK; never raises for runtime conditions."""
    if producer == PRODUCER_UNAVAILABLE:
        # Without an identity the call cannot be bound to its calibration; never send it
        # unbound (that would silently change the fingerprint) and never crash the host.
        return _fail_closed(mode, "producer_unavailable")
    if not Path(question_set_path).is_absolute():
        # A relative path would resolve against the host's working directory, letting the
        # project a hook runs in pick its own question and egress rules. Reject it through the
        # normal config-failure path; never resolve it and never fall back to the default.
        return _fail_closed(mode, "config")
    request = {
        "schema_version": 1,
        "op": "decide",
        "question_set_path": question_set_path,
        "mode": mode.value,
        **({"producer": producer} if producer else {}),
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
    # ALLOW only if EVERY record is a clean accept whose accepted label is explicitly listed
    # safe (kit.gate.allow_labels). accept alone is calibrated-and-clear, not permission; a
    # confident dangerous answer (label not in allow_labels) escalates (finding H1). Missing
    # allow_labels on an accept is a fail-closed backstop (asks).
    dict_records = [r for r in records if isinstance(r, dict)]
    if len(dict_records) != len(records) or not dict_records:
        return _fail_closed(mode, "malformed_records")

    def _may_allow(rec: dict[str, Any]) -> bool:
        if rec.get("route") != "accept":
            return False
        allow_labels = rec.get("allow_labels")
        if not isinstance(allow_labels, list):
            return False  # backstop: an accept without a declared allow-list never allows
        return rec.get("label") in allow_labels

    if all(_may_allow(rec) for rec in dict_records):
        return HookResult(HookOutcome.ALLOW, "accept", None, is_mock)
    reason = next(
        (r.get("fail_reason") for r in dict_records if r.get("fail_reason")),
        None,
    )
    return HookResult(HookOutcome.ASK, "ask", reason, is_mock)
