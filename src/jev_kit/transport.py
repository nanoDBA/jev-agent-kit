"""Transport interface, the labeled mock, and (later) the live HTTPS transport.

The transport is the one injectable dependency (spec Implementation Decisions). Mock
provenance is a property of the transport object, never of the response body, so a scripted
body can never claim to be real (spec story 30; finding R01). The live transport is a private
opener that refuses redirects and never forwards the key (spec story 57; finding R04); it is
implemented in a later slice.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

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


class LiveTransport:
    """POST to the fixed HTTPS endpoint with bearer auth. One attempt per send; the engine
    owns retries, backoff and the rate budget. Redirects are refused; the key is attached only
    as a request header and never appears in the body, digests or diagnostics."""

    is_mock = False

    def __init__(self, api_key: str, *, endpoint: str = DEFAULT_ENDPOINT) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("endpoint must be https")
        self._api_key = api_key
        self._endpoint = endpoint
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
                payload = response.read()
            return TransportResponse(status, headers, payload)
        except urllib.error.HTTPError as exc:
            # A 3xx reaches here because _NoRedirect returned None; classify it as refused.
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            if 300 <= exc.code < 400:
                return TransportFailure(FailReason.REDIRECT_REFUSED, "redirect_refused")
            # Keep the status; the raw body stays with the exception and is not surfaced.
            return _http_error_result(exc.code, headers)
        except TimeoutError:
            return TransportFailure(FailReason.TIMEOUT, "read_timeout")
        except (urllib.error.URLError, OSError, ValueError):
            return TransportFailure(FailReason.TRANSPORT, "transport_error")


def _http_error_result(status: int, headers: Mapping[str, str]) -> TransportResult:
    # A non-2xx status is a failure, never a body to validate as an answer (finding, story 37).
    if 200 <= status < 300:
        return TransportFailure(FailReason.TRANSPORT, "unexpected_2xx_error")
    return TransportFailure(fail_reason_for_status(status), f"http_{status}")
