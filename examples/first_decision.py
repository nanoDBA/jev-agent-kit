"""Your first Jev decision, fully offline.

Runs the shipped tool-call gate against a scripted mock model, so it needs no API key, no
network, and no configuration. It shows the shape of what the engine returns and the two
safety rules you meet first:

- a mock answer can never approve anything (``is_mock`` is True, the gate routes to ``ask``);
- the shipped question sets are uncalibrated, so no answer is trusted yet.

Run it from the repository root:

    python examples/first_decision.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.engine import EngineConfig, decide  # noqa: E402
from jev_kit.receipts import ReceiptWriter  # noqa: E402
from jev_kit.transport import MockTransport  # noqa: E402

GATE = REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json"

# What the mock "model" answers. A Noul value is the probability that the answer is yes.
MOCK_REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "destructive": {"type": "noul", "noul": 0.97},
        "exfiltrates": {"type": "noul", "noul": 0.02},
        "widens_permission": {"type": "noul", "noul": 0.01},
    },
}


def main() -> None:
    receipts = Path(tempfile.mkdtemp(prefix="jev-receipts-"))
    request = {
        "schema_version": 1,
        "question_set_path": str(GATE),
        "state": {"command": "rm -rf ./build"},
        "mode": "shadow",
    }
    mock = MockTransport.replying(
        200, json.dumps(MOCK_REPLY).encode(), {"x-typesafe-request-id": "demo-request-1"}
    )
    response = decide(
        request, transport=mock, config=EngineConfig(writer=ReceiptWriter(directory=receipts))
    )
    print(f"status: {response['status']}")
    for rec in response["records"]:
        threshold = rec["threshold_status"] or "uncalibrated"
        print(
            f"  {rec['question_id']:<18} route={rec['route']:<4} "
            f"p(yes)={rec['noul']:<5} threshold={threshold:<13} mock={rec['is_mock']}"
        )
    print(f"receipt: {next(receipts.glob('*.jsonl'))}")


if __name__ == "__main__":
    main()
