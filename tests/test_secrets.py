"""Key-command bounds and secrecy tests (finding C18, spec stories 59 to 61)."""

from __future__ import annotations

import json
import sys
import time
import traceback

import pytest

from jev_kit.errors import FailReason, ValidationError
from jev_kit.secrets import resolve_secret

# argv is built with json.dumps so that a Windows sys.executable path (with backslashes) is
# escaped into VALID JSON. A hand-built f-string produced invalid JSON, which the resolver
# rejected before ever starting the child, so the intended code path was never exercised
# (finding H20: serialize test argv with json.dumps).


def test_command_output_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command that prints far more than the cap is rejected, not accepted truncated.
    script = "import sys; sys.stdout.write('x' * 100000)"
    argv = json.dumps([sys.executable, "-c", script])
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=5.0)
    assert exc.value.reason is FailReason.CONFIG
    assert exc.value.check == "key_command_output_too_large"  # the child DID start and overflow


def test_command_text_not_in_exception_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "SYNTHETIC_COMMAND_SENTINEL"
    argv = json.dumps([sys.executable, "-c", "import sys; sys.exit(3)", sentinel])
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    try:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=5.0)
    except ValidationError as exc:
        assert exc.check == "key_command_exit"  # the child started and exited non-zero
        assert sentinel not in traceback.format_exc()
    else:
        raise AssertionError("expected a ValidationError")


def test_both_sources_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("TYPESAFE_API_KEY_COMMAND", json.dumps([sys.executable, "-c", "print(1)"]))
    with pytest.raises(ValidationError):
        resolve_secret("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_COMMAND", timeout=5.0)


def test_key_command_that_never_closes_stdout_times_out_h20(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A child that writes a little then sleeps holding stdout open must not hang past the
    # deadline: the bounded read runs under the timeout, so it fails closed (finding H20).
    script = "import sys, time; sys.stdout.write('x'); sys.stdout.flush(); time.sleep(30)"
    argv = json.dumps([sys.executable, "-c", script])
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    start = time.monotonic()
    with pytest.raises(ValidationError) as exc:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=1.0)
    elapsed = time.monotonic() - start
    # The child actually started and the read blocked on the held-open pipe, so the timeout path
    # ran (elapsed near the 1 s budget) rather than an instant JSON-parse rejection (elapsed ~0).
    assert 0.8 <= elapsed < 10.0
    assert exc.value.reason is FailReason.CONFIG
    assert exc.value.check == "key_command_failed"


def test_key_command_total_deadline_not_doubled_h20(monkeypatch: pytest.MonkeyPatch) -> None:
    # A child that delays, closes stdout, then delays again before exit must not be able to
    # consume the budget once for the read and again for the wait (finding H20): one absolute
    # deadline spans both, so the whole call stays within a small multiple of the budget.
    script = (
        "import sys, time; time.sleep(0.6); sys.stdout.write('k'); sys.stdout.close(); "
        "time.sleep(0.6)"
    )
    argv = json.dumps([sys.executable, "-c", script])
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    start = time.monotonic()
    with pytest.raises(ValidationError):
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=0.6)
    # If read and wait each got the full 0.6 s budget the call could run ~1.2 s+; one shared
    # deadline keeps it well under that.
    assert time.monotonic() - start < 1.1


def test_key_command_late_completion_rejected_h20(monkeypatch: pytest.MonkeyPatch) -> None:
    # H20: even when the read and the exit wait each COMPLETE, if they completed only after the
    # absolute deadline (a slow process start / late scheduling), the produced key must not be
    # returned as a success. A controlled monotonic clock makes the final check see a time past
    # the deadline while the child itself runs fast. (threading.join binds its own time
    # reference at import, so patching time.monotonic here does not disturb the reader join.)
    ticks = iter([1000.0, 1000.01, 1000.02, 1000.5])  # end-calc, join, wait, final check
    monkeypatch.setattr("jev_kit.secrets.time.monotonic", lambda: next(ticks))
    argv = json.dumps([sys.executable, "-c", "print('k')"])  # fast, clean success
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=0.1)
    assert exc.value.reason is FailReason.CONFIG
    assert exc.value.check == "key_command_failed"
