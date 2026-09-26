"""Run the engine as a child process with a hard wall-clock kill (Phase 3, decision E2).

The Phase 1 in-process deadline is cooperative and cannot interrupt a blocked system call
(`deadline.py`). Enforce mode needs a real bound, so a hook runs the engine through the CLI in
a child process and kills it at a hard timeout. On timeout or any failure this returns an error
envelope, which the hook core maps to the fail-closed outcome. The child needs the API key in
its environment for a live call; that, and arming a host, are owner-gated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

_ERROR_TIMEOUT: dict[str, Any] = {
    "schema_version": 1,
    "status": "error",
    "reason": "timeout",
    "records": [],
}
_ERROR_INTERNAL: dict[str, Any] = {
    "schema_version": 1,
    "status": "error",
    "reason": "internal",
    "records": [],
}

_MAX_CHILD_OUTPUT = 4_194_304  # 4 MiB cap on the child's stdout


def run_child(
    request: dict[str, Any],
    *,
    timeout_s: float,
    python_exe: str | None = None,
) -> dict[str, Any]:
    """Run one decision request through `python -m jev_kit.cli` and return its JSON response.

    Enforces a hard wall-clock timeout: on expiry the child is killed and reaped and a timeout
    error envelope is returned. Never raises for runtime conditions.
    """
    argv = [python_exe or sys.executable, "-m", "jev_kit.cli"]
    payload = json.dumps(request).encode("utf-8")
    proc = None
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        out, _ = proc.communicate(input=payload, timeout=max(0.1, timeout_s))
    except subprocess.TimeoutExpired:
        _kill(proc)
        return dict(_ERROR_TIMEOUT)
    except (OSError, ValueError):
        _kill(proc)
        return dict(_ERROR_INTERNAL)
    if proc.returncode != 0:
        return dict(_ERROR_INTERNAL)
    try:
        parsed = json.loads(out[:_MAX_CHILD_OUTPUT].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return dict(_ERROR_INTERNAL)
    if not isinstance(parsed, dict):
        return dict(_ERROR_INTERNAL)
    return parsed


def _kill(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()
        proc.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        pass
