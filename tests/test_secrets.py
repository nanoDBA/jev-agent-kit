"""Key-command bounds and secrecy tests (finding C18, spec stories 59 to 61)."""

from __future__ import annotations

import sys
import time
import traceback

import pytest

from jev_kit.errors import FailReason, ValidationError
from jev_kit.secrets import resolve_secret


def test_command_output_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command that prints far more than the cap is rejected, not accepted truncated.
    script = "import sys; sys.stdout.write('x' * 100000)"
    argv = f'["{sys.executable}", "-c", {script!r}]'
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=5.0)
    assert exc.value.reason is FailReason.CONFIG


def test_command_text_not_in_exception_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "SYNTHETIC_COMMAND_SENTINEL"
    argv = f'["{sys.executable}", "-c", "import sys; sys.exit(3)", "{sentinel}"]'
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    try:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=5.0)
    except ValidationError:
        assert sentinel not in traceback.format_exc()
    else:
        raise AssertionError("expected a ValidationError")


def test_both_sources_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("TYPESAFE_API_KEY_COMMAND", '["python","-c","print(1)"]')
    with pytest.raises(ValidationError):
        resolve_secret("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_COMMAND", timeout=5.0)


def test_key_command_that_never_closes_stdout_times_out_h20(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A child that writes a little then sleeps holding stdout open must not hang past the
    # deadline: the bounded read runs under the timeout, so it fails closed (finding H20).
    script = "import sys, time; sys.stdout.write('x'); sys.stdout.flush(); time.sleep(30)"
    argv = f'["{sys.executable}", "-c", "{script}"]'
    monkeypatch.setenv("JEV_KIT_HMAC_KEY_COMMAND", argv)
    monkeypatch.delenv("JEV_KIT_HMAC_KEY", raising=False)
    start = time.monotonic()
    with pytest.raises(ValidationError) as exc:
        resolve_secret("JEV_KIT_HMAC_KEY", "JEV_KIT_HMAC_KEY_COMMAND", timeout=1.0)
    assert time.monotonic() - start < 10.0
    assert exc.value.reason is FailReason.CONFIG
