"""Resolve the API key and the HMAC key from an environment variable or a key command.

Spec stories 59 to 61. A key comes from a direct environment variable or from a command that
prints it (a JSON argument array run without a shell, so quoting is portable across platforms).
Setting both forms of one key is a configuration failure. Command output and command text are
never included in any error. No default key is ever substituted.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import subprocess
import threading

from jev_kit.errors import FailReason, ValidationError

_MAX_KEY_OUTPUT = 8192


def _run_key_command(spec: str, timeout: float) -> str:
    try:
        argv = json.loads(spec)
    except json.JSONDecodeError:
        # `from None`: the command text must never survive in the exception chain (finding C18).
        raise ValidationError(FailReason.CONFIG, "key_command_not_json") from None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValidationError(FailReason.CONFIG, "key_command_not_argv")
    deadline = max(0.1, timeout)
    proc = None
    try:
        proc = subprocess.Popen(  # shell=False by construction; argv is a validated list
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # discard stderr so it cannot leak
            shell=False,
        )
        assert proc.stdout is not None
        # The bounded read is itself run under the deadline. A plain proc.stdout.read() can
        # block forever when the child writes less than the cap but never closes stdout (for
        # example a lingering grandchild holding the pipe), so a later wait(timeout=...) would
        # never be reached and the deadline would not bind (finding H20). Reading in a thread
        # and joining with the deadline enforces the timeout across the read AND the wait, while
        # still capping the buffer at one byte past the limit.
        stdout = proc.stdout
        box: dict[str, bytes] = {}

        def _read() -> None:
            # errors are surfaced by an absent result key, never by leaking anything
            with contextlib.suppress(OSError, ValueError):
                box["raw"] = stdout.read(_MAX_KEY_OUTPUT + 1)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(deadline)
        if reader.is_alive() or "raw" not in box:
            raise subprocess.TimeoutExpired(argv[0], deadline)
        raw = box["raw"]
        proc.wait(timeout=deadline)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _terminate(proc)
        # No `from` clause: nothing about the command or its output reaches the error.
        raise ValidationError(FailReason.CONFIG, "key_command_failed") from None
    if proc.returncode != 0:
        raise ValidationError(FailReason.CONFIG, "key_command_exit")
    if len(raw) > _MAX_KEY_OUTPUT:
        raise ValidationError(FailReason.CONFIG, "key_command_output_too_large")
    out = raw.decode("utf-8", errors="replace").strip()
    if not out:
        raise ValidationError(FailReason.CONFIG, "key_command_empty")
    return out


def _terminate(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()
        proc.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def resolve_secret(direct_env: str, command_env: str, *, timeout: float) -> str | None:
    """Return the secret from direct_env, or by running command_env, or None if neither is set."""
    direct = os.environ.get(direct_env)
    command = os.environ.get(command_env)
    if direct is not None and command is not None:
        raise ValidationError(FailReason.CONFIG, "both_key_sources")
    if direct is not None:
        if not direct.strip():
            raise ValidationError(FailReason.CONFIG, "key_empty")
        return direct
    if command is not None:
        return _run_key_command(command, timeout)
    return None


def resolve_api_key(*, timeout: float) -> str | None:
    return resolve_secret("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_COMMAND", timeout=timeout)


def resolve_hmac_key(*, timeout: float) -> bytes | None:
    """Resolve the HMAC key and decode exactly 32 bytes from base64url (spec stories 59, 15)."""
    raw = resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=timeout)
    if raw is None:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(FailReason.CONFIG, "hmac_key_not_base64url") from exc
    if len(key) != 32:
        raise ValidationError(FailReason.CONFIG, "hmac_key_not_32_bytes")
    return key
