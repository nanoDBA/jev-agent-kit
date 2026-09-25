"""The engine's JSON-facing entry point and the request/response contract.

This module holds the one function the CLI and the smoke script call. Its implementation
(the ``decide`` core: build request, budget, egress, transport, validate, route, receipt) is
being filled in on a later slice; the signature and JSON contract below are frozen so the CLI
and smoke work can be built against them (spec stories 7 to 10, 82).

JSON request (schema_version 1):
    {
      "schema_version": 1,
      "question_set_path": "<path>",         # or "question_set": {inline object}
      "state": { ... },                       # keyed object
      "mode": "shadow" | "enforce",           # default "shadow"
      "action_id": "<opaque id>"              # optional
    }

JSON response (schema_version 1):
    {
      "schema_version": 1,
      "status": "ok" | "error",
      "reason": "<fail reason>",              # only when status == "error"
      "records": [                            # present when status == "ok"
        {
          "decision_id": "...",
          "question_id": "...",
          "route": "accept" | "ask" | "no_advice",
          "would_route": "accept" | "ask" | "no_advice" | null,
          "label": "..." | null,
          "value": <number> | null,
          "distribution": { ... } | null,
          "confidence": <number> | null,
          "noul": <number> | null,
          "margin": <number> | null,
          "threshold_status": "calibrated" | "uncalibrated" | "never_auto_accept" | null,
          "mode": "shadow" | "enforce",
          "fail_reason": "<fail reason>" | null,
          "is_mock": true | false,
          "receipt_written": true | false
        }
      ]
    }

An "error" response is the safe envelope for when the questions cannot be identified
(unreadable question set, bad request shape). Hosts must treat it as "ask" (spec story 26).
"""

from __future__ import annotations

from typing import Any

from jev_kit.transport import Transport

SCHEMA_VERSION = 1


def run_json(request: dict[str, Any], *, transport: Transport | None = None) -> dict[str, Any]:
    """Run one decision request expressed as JSON and return the JSON response.

    ``transport`` None means build the configured transport (live). Tests and the smoke
    script pass a transport explicitly. This never raises for runtime conditions; a request
    that cannot be understood returns the error envelope.
    """
    raise NotImplementedError("engine.run_json is implemented in a later slice")
