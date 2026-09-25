"""Live-transport suite against a local fake HTTP server (spec story 85; documented exception
to the one-seam rule). Never touches the real Jev API.

Covers what a mock cannot: real redirect refusal (finding R04), status mapping, and the accept
path end to end (an accept requires a non-mock transport, per story 32). The fake server speaks
plain HTTP; LiveTransport requires https, so a small https-relaxed subclass is used only here.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from jev_kit.deadline import Deadline
from jev_kit.engine import EngineConfig, decide
from jev_kit.errors import FailReason
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import ReceiptWriter
from jev_kit.transport import LiveTransport, TransportFailure, TransportResponse

HMAC_KEY = b"0" * 32


class _Handler(BaseHTTPRequestHandler):
    behavior: str = "ok"
    payload: bytes = b"{}"

    def log_message(self, *args: Any) -> None:  # silence
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.behavior == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/evil")
            self.end_headers()
            return
        if self.behavior == "429":
            self.send_response(429)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)


class _NoRedirectForTest(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class _HttpLiveTransport(LiveTransport):
    """LiveTransport that accepts an http:// endpoint, for the local fake server only."""

    def __init__(self, api_key: str, endpoint: str) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._opener = urllib.request.build_opener(
            _NoRedirectForTest(), urllib.request.ProxyHandler({})
        )


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()


def endpoint(server: HTTPServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host!s}:{port}/v1/systemone"


def test_redirect_is_refused(server: HTTPServer) -> None:
    _Handler.behavior = "redirect"
    transport = _HttpLiveTransport("SYNTHETIC_KEY", endpoint(server))
    result = transport.send(b"{}", Deadline(5.0))
    assert isinstance(result, TransportFailure)
    assert result.reason is FailReason.REDIRECT_REFUSED


def test_429_mapped_to_rate_limit(server: HTTPServer) -> None:
    _Handler.behavior = "429"
    transport = _HttpLiveTransport("SYNTHETIC_KEY", endpoint(server))
    result = transport.send(b"{}", Deadline(5.0))
    assert isinstance(result, TransportFailure)
    assert result.reason is FailReason.RATE_LIMIT


def test_200_returns_response(server: HTTPServer) -> None:
    _Handler.behavior = "ok"
    _Handler.payload = b'{"model": "jev-1.13.0", "answers": {}}'
    transport = _HttpLiveTransport("SYNTHETIC_KEY", endpoint(server))
    result = transport.send(b"{}", Deadline(5.0))
    assert isinstance(result, TransportResponse)
    assert result.status == 200


def _registry_file(tmp_path: Any, fingerprint: str) -> str:
    reg = {
        "schema_version": 1,
        "entries": {
            fingerprint: {
                "status": "calibrated",
                "escalation_target": "gpt-6",
                "evidence_ref": "ev-1",
                "type": "noul",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
            }
        },
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(reg), encoding="utf-8")
    return str(path)


def test_accept_path_through_fake_server(server: HTTPServer, tmp_path: Any) -> None:
    # The one accept path: a non-mock transport, calibrated threshold, enforce, clear answer.
    from jev_kit.fingerprint import question_fingerprint
    from jev_kit.questionset import egress_contract, load_question_set

    qset_obj = {
        "schema_version": 1,
        "id": "triage",
        "version": "1",
        "model": "jev-1.13.0",
        "escalation_target": "gpt-6",
        "questions": {
            "destructive": {"type": "noul", "instructions": "Destructive?", "consequence": "gate"}
        },
        "state_schema": {"cmd": {"kind": "command"}},
    }
    qset = load_question_set(qset_obj)
    fp = question_fingerprint(
        instructions="Destructive?",
        criteria=None,
        question_type="noul",
        option_or_level_set=[],
        model="jev-1.13.0",
        egress_contract=egress_contract(qset),
    )
    _Handler.behavior = "ok"
    _Handler.payload = json.dumps(
        {"model": "jev-1.13.0", "answers": {"destructive": {"noul": 0.02}}}
    ).encode()
    transport = _HttpLiveTransport("SYNTHETIC_KEY", endpoint(server))
    config = EngineConfig(
        hmac_key=HMAC_KEY,
        writer=ReceiptWriter(directory=tmp_path),
        rate_budget=RateBudget(),
        registry_path=_registry_file(tmp_path, fp),
    )
    request = {
        "schema_version": 1,
        "question_set": qset_obj,
        "state": {"cmd": "ls -la"},
        "mode": "enforce",
    }
    resp = decide(request, transport=transport, config=config)
    rec = resp["records"][0]
    assert rec["route"] == "accept"
    assert rec["label"] == "no"  # noul 0.02 <= no_bound 0.1
    assert rec["is_mock"] is False
    assert rec["receipt_written"] is True
