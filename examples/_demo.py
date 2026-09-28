"""Shared by the examples: ask Jev live by default, or replay a recorded answer with --offline.

Live mode needs `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND`. The examples send only the
fixed sample text written in this repository, never your data, so they carry their own
in-memory attestation instead of requiring your attestation file (ADR 0002, Amendment 2).
Everything else in the kit, hooks and your own questions included, still requires the file.

`--offline` replays the answer Jev gave to the same input on 2026-09-28. Replayed answers are
marked `model: mock` and the kit never acts on them. Tests always use `--offline`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from jev_kit.deadline import Deadline
from jev_kit.secrets import resolve_api_key
from jev_kit.transport import LiveTransport, MockTransport, Transport, TransportResult

RECORDED_ON = "2026-09-28"
NO_KEY = ("Set TYPESAFE_API_KEY (or TYPESAFE_API_KEY_COMMAND) to ask Jev live, "
          "or run with --offline to replay recorded answers.")


class RecordingTransport:
    """Wraps the live transport and keeps each request body, so an example can show it."""

    is_mock: ClassVar[bool] = False

    def __init__(self, inner: LiveTransport) -> None:
        self._inner = inner
        self.requests: list[bytes] = []

    def send(self, body: bytes, deadline: Deadline) -> TransportResult:
        self.requests.append(body)
        return self._inner.send(body, deadline)


@dataclass
class Demo:
    live: bool
    _live_transport: RecordingTransport | None = None
    requests: list[bytes] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "live Jev answer" if self.live else f"recorded Jev answer from {RECORDED_ON}"

    @property
    def attestation(self) -> dict[str, Any] | None:
        if not self.live:
            return None
        # Scope: fixed sample text from this repository only (ADR 0002, Amendment 2).
        return {"inventory": True, "inventory_date": date.today().isoformat(), "dpa": True,
                "scope": "jev-agent-kit examples: fixed repository sample text"}

    def transport(self, recorded_reply: dict[str, Any]) -> Transport:
        """The transport for one call: live, or a replay of `recorded_reply`."""
        if self._live_transport is not None:
            return self._live_transport
        mock = MockTransport.replying(200, json.dumps(recorded_reply).encode(),
                                      {"x-typesafe-request-id": "demo-request"})
        self.requests = mock.requests
        return mock

    def sent(self) -> list[bytes]:
        return self._live_transport.requests if self._live_transport is not None else self.requests


def setup(offline: bool | None = None) -> Demo:
    """Live unless `--offline` is on the command line (or `offline=True`)."""
    if offline is None:
        offline = "--offline" in sys.argv[1:]
    if offline:
        return Demo(live=False)
    key = resolve_api_key(timeout=20.0)
    if not key:
        raise SystemExit(NO_KEY)
    return Demo(live=True, _live_transport=RecordingTransport(LiveTransport(key)))
