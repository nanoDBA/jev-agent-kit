"""Ask Jev a question of your own: which handler should take a request? Fully offline.

Uses the shipped `preflight-route` question set, a Choice question with three options. Jev
returns a probability for every option; your code decides what to do with them. Here the
answer is a scripted mock, so the kit returns `no_advice` and your code falls back.

Run it from the repository root:

    python examples/route_request.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.engine import EngineConfig, decide  # noqa: E402 (after the src path setup)
from jev_kit.receipts import ReceiptWriter  # noqa: E402 (after the src path setup)
from jev_kit.transport import MockTransport  # noqa: E402 (after the src path setup)

ROUTER = REPO / "skills" / "jev-runtime" / "questions" / "preflight-route.json"

MOCK_REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "route": {
            "type": "choice",
            "choice": "human",
            "probabilities": {"deterministic": 0.05, "specialist_llm": 0.24, "human": 0.71},
            "confidence": 0.71,
        }
    },
}


def handle(request: str) -> str:
    mock = MockTransport.replying(
        200, json.dumps(MOCK_REPLY).encode(), {"x-typesafe-request-id": "demo-request-2"}
    )
    config = EngineConfig(
        source_allowlist=frozenset({"agent_request"}),
        writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
    )
    response = decide(
        {
            "schema_version": 1,
            "question_set_path": str(ROUTER),
            "state": {"request": request},
            "mode": "shadow",
        },
        transport=mock,
        config=config,
    )
    rec = response["records"][0]
    print(f"request: {request!r}")
    print("Which handler should take it?  (scripted demo answer)")
    for option, p in rec["distribution"].items():
        bar = "#" * round(p * 20)
        print(f"  {option:<15} {bar:<20} {p:.2f}")
    print(f"route:   {rec['route']}")

    # Your code owns the decision. Act on the label only when the kit says `accept`;
    # otherwise use your normal path.
    if rec["route"] == "accept":
        return str(rec["label"])
    return "specialist_llm"  # the default you would use without Jev


if __name__ == "__main__":
    handler = handle("Customer says order 1182 was charged twice and asks for a refund.")
    print(f"handled: {handler}")
