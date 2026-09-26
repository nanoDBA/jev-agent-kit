"""Transport interface, the labeled mock, and (later) the live HTTPS transport.

The transport is the one injectable dependency (spec Implementation Decisions). Mock
provenance is a property of the transport object, never of the response body, so a scripted
body can never claim to be real (spec story 30; finding R01). The live transport is a private
opener that refuses redirects and never forwards the key (spec story 57; finding R04); it is
implemented in a later slice.
"""

from __future__ import annotations

import email.utils
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, runtime_checkable

from jev_kit.deadline import Deadline
from jev_kit.errors import FailReason, fail_reason_for_status


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class TransportFailure:
    reason: FailReason
    detail: str  # short and safe; never a raw server body or matched content
    retry_after: float | None = None  # seconds the server asked us to wait, if any


TransportResult = TransportResponse | TransportFailure


@runtime_checkable
class Transport(Protocol):
    is_mock: ClassVar[bool]

    def send(self, body: bytes, deadline: Deadline) -> TransportResult: ...


@dataclass
class MockTransport:
    """A scripted, recording transport for tests. Never touches the network.

    ``outcomes`` are returned in order, one per ``send``. Every request body is recorded so
    tests can assert exactly one request per call with the exact question set, and that the
    key never appears in the bytes. ``is_mock`` is a fixed class attribute, never a
    constructor field, so a scripted mock can never claim non-mock provenance and reach an
    effective accept (finding C01, spec story 32). Selection and cursor advance are locked so
    a shared mock under a thread pool consumes each scripted outcome exactly once (C22).
    """

    is_mock: ClassVar[bool] = True

    outcomes: list[TransportResult]
    requests: list[bytes] = field(default_factory=list)
    _index: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def send(self, body: bytes, deadline: Deadline) -> TransportResult:
        with self._lock:
            self.requests.append(body)
            if self._index >= len(self.outcomes):
                return TransportFailure(FailReason.TRANSPORT, "mock_exhausted")
            outcome = self.outcomes[self._index]
            self._index += 1
            return outcome

    @classmethod
    def replying(
        cls, status: int, body: bytes, headers: Mapping[str, str] | None = None
    ) -> MockTransport:
        return cls(outcomes=[TransportResponse(status, dict(headers or {}), body)])


DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. The default handler would forward a normally added
    Authorization header to the new host (finding R04)."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) ignores environment proxies unless one is configured explicitly.
    return urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB cap on any response body read


class LiveTransport:
    """POST to the fixed HTTPS endpoint with bearer auth. One attempt per send; the engine
    owns retries, backoff and the rate budget. The destination is fixed (no override reaches
    a shipped instance, finding C23); redirects are refused; the key is attached only as a
    request header and never appears in the body, digests or diagnostics."""

    is_mock: ClassVar[bool] = False

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._endpoint = DEFAULT_ENDPOINT
        self._opener = _build_opener()

    def send(self, body: bytes, deadline: Deadline) -> TransportResult:
        request = urllib.request.Request(self._endpoint, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {self._api_key}")
        timeout = deadline.remaining_for_work()
        if timeout <= 0:
            return TransportFailure(FailReason.TIMEOUT, "deadline_before_send")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status = response.status
                headers = {k.lower(): v for k, v in response.headers.items()}
                payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                return TransportFailure(FailReason.RESPONSE_MALFORMED, "response_too_large")
            return TransportResponse(status, headers, payload)
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            try:
                exc.read(MAX_RESPONSE_BYTES)  # drain and discard; body never surfaced
            except OSError:
                pass
            finally:
                exc.close()
            if 300 <= exc.code < 400:
                return TransportFailure(FailReason.REDIRECT_REFUSED, "redirect_refused")
            return _http_error_result(exc.code, headers)
        except TimeoutError:
            return TransportFailure(FailReason.TIMEOUT, "read_timeout")
        except (urllib.error.URLError, OSError, ValueError):
            return TransportFailure(FailReason.TRANSPORT, "transport_error")


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Parse retry-after-ms (preferred) or Retry-After (seconds or HTTP date). Malformed
    values are ignored (return None), so the engine falls back to its own backoff."""
    ms = headers.get("retry-after-ms")
    if ms is not None:
        try:
            value = float(ms)
            if value >= 0:
                return value / 1000.0
        except ValueError:
            pass
    ra = headers.get("retry-after")
    if ra is not None:
        try:
            value = float(ra)
            if value >= 0:
                return value
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(ra)
            if parsed is not None:
                delta = parsed.timestamp() - time.time()
                return max(0.0, delta)
    return None


def _http_error_result(status: int, headers: Mapping[str, str]) -> TransportResult:
    # A non-2xx status is a failure, never a body to validate as an answer (story 37).
    if 200 <= status < 300:
        return TransportFailure(FailReason.TRANSPORT, "unexpected_2xx_error")
    return TransportFailure(
        fail_reason_for_status(status), f"http_{status}", retry_after=parse_retry_after(headers)
    )
