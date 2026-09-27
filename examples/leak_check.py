"""Stop a leaked secret before it leaves your machine. Fully offline.

An agent is about to run a command, and its context happens to contain an AWS access key.
jev-kit checks the exact bytes of the outgoing request and blocks it: nothing is sent.
The same call without the key goes through, reduced to what the model would actually see.

Run it from the repository root:

    python examples/leak_check.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.engine import EngineConfig, decide  # noqa: E402
from jev_kit.receipts import ReceiptWriter  # noqa: E402
from jev_kit.transport import MockTransport  # noqa: E402

GATE = REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json"
LEAKED_KEY = "AKIAIOSFODNN7EXAMPLE"  # AWS's published example key, not a real credential

MOCK_REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "destructive": {"type": "noul", "noul": 0.03},
        "exfiltrates": {"type": "noul", "noul": 0.04},
        "widens_permission": {"type": "noul", "noul": 0.01},
    },
}


def send(state: dict[str, Any]) -> tuple[dict[str, Any], MockTransport]:
    mock = MockTransport.replying(
        200, json.dumps(MOCK_REPLY).encode(), {"x-typesafe-request-id": "demo-request-3"}
    )
    config = EngineConfig(
        hmac_key=base64.urlsafe_b64decode("ZGVtby1rZXktZGVtby1rZXktZGVtby1rZXktZGVtby0="),
        source_allowlist=frozenset({"agent_context"}),
        writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
    )
    request = {"schema_version": 1, "question_set_path": str(GATE), "state": state,
               "mode": "shadow"}
    return decide(request, transport=mock, config=config), mock


def main() -> None:
    leaky = {
        "command": "git push origin main",
        "target": "/home/alice/app",
        "context": f"tool=Bash; description=Push after setting AWS_ACCESS_KEY_ID={LEAKED_KEY}",
    }
    response, mock = send(leaky)
    record = response["records"][0]
    print("The agent's tool call carries a leaked key:")
    print(f"  context: {leaky['context']}")
    print()
    print("jev-kit checks the exact outgoing bytes before sending:")
    print(f"  BLOCKED  fail_reason={record['fail_reason']}  requests sent: {len(mock.requests)}")
    print()

    clean = dict(leaky, context="tool=Bash; description=Push the release branch")
    _, mock = send(clean)
    sent = json.loads(mock.requests[0])["state"]
    print("The same call without the key is sent, reduced to what the model sees:")
    print(f"  {json.dumps(sent)}")


if __name__ == "__main__":
    main()
