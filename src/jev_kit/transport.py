"""Transport interface, the labeled mock, and (later) the live HTTPS transport.

The transport is the one injectable dependency (spec Implementation Decisions). Mock
provenance is a property of the transport object, never of the response body, so a scripted
body can never claim to be real (spec story 30; finding R01). The live transport is a private
opener that refuses redirects and never forwards the key (spec story 57; finding R04); it is
implemented in a later slice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from jev_kit.deadline import Deadline
from jev_kit.errors import FailReason


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class TransportFailure:
    reason: FailReason
    detail: str  # short and safe; never a raw server body or matched content


TransportResult = TransportResponse | TransportFailure


@runtime_checkable
class Transport(Protocol):
    is_mock: bool

    def send(self, body: bytes, deadline: Deadline) -> TransportResult: ...


@dataclass
class MockTransport:
    """A scripted, recording transport for tests. Never touches the network.

    ``outcomes`` are returned in order, one per ``send``. Every request body is recorded so
    tests can assert exactly one request per call with the exact question set, and that the
    key never appears in the bytes.
    """

    outcomes: list[TransportResult]
    is_mock: bool = True
    requests: list[bytes] = field(default_factory=list)
    _index: int = 0

    def send(self, body: bytes, deadline: Deadline) -> TransportResult:
        self.requests.append(body)
        if self._index >= len(self.outcomes):
            return TransportFailure(FailReason.TRANSPORT, "mock_exhausted")
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    @classmethod
    def replying(cls, status: int, body: bytes, headers: Mapping[str, str] | None = None) -> MockTransport:
        return cls(outcomes=[TransportResponse(status, dict(headers or {}), body)])
