"""Show the receipt a decision leaves behind. Fully offline.

Every decision is written to a JSONL receipt before the kit returns an answer: what was
asked, which model answered, the full distribution, the threshold status and the route. The
request itself is not stored, only a digest of the exact bytes sent. This runs the routing
example once and prints its receipt, annotated. Random ids, digests and the timestamp are
shortened so the output is the same on every run.

Run it from the repository root:

    python examples/show_receipt.py
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
    "answers": {"route": {
        "type": "choice", "choice": "deterministic", "confidence": 0.52,
        "probabilities": {"deterministic": 0.68, "specialist_llm": 0.07, "human": 0.25},
    }},
}


def main() -> None:
    receipts = Path(tempfile.mkdtemp(prefix="jev-receipts-"))
    decide(
        {"schema_version": 1, "question_set_path": str(ROUTER), "mode": "shadow",
         "state": {"request": "Customer says order 1182 was charged twice and asks for a refund."}},
        transport=MockTransport.replying(
            200, json.dumps(MOCK_REPLY).encode(), {"x-typesafe-request-id": "demo-request-2"}
        ),
        config=EngineConfig(
            source_allowlist=frozenset({"agent_request"}),
            writer=ReceiptWriter(directory=receipts),
        ),
    )
    decision, commit = (json.loads(line) for line in
                        next(receipts.glob("*.jsonl")).read_text(encoding="utf-8").splitlines())
    dist = ", ".join(f"{k} {v}" for k, v in decision["distribution"].items())
    qs = decision["question_set"]
    rows = [
        ("question_id", decision["question_id"], "which question was asked"),
        ("question_set", f"{qs['id']} v{qs['version']}", "plus a digest of the whole set"),
        ("fingerprint", "sha256:...", "question + model + egress rules; thresholds bind to it"),
        ("requested_model", decision["requested_model"], "the pinned version"),
        ("served_model", decision["served_model"], "a mismatch is rejected"),
        ("model", decision["model"], "who really answered: a mock, not Jev"),
        ("distribution", dist, "the full answer, not just the top label"),
        ("threshold_status", decision["threshold_status"] or "none",
         "no calibrated threshold yet"),
        ("route", decision["route"], "what the kit told your code"),
        ("sent_digest", "sha256:...", "hash of the exact bytes sent; the text is not stored"),
    ]
    print("Decision receipt (one JSONL line; long ids and digests shortened):")
    for key, value, _note in rows:
        print(f"  {key:<17} {value}")
    print(f"Commit marker: {commit['lines']} decision line for this call, written durably.")


if __name__ == "__main__":
    main()
