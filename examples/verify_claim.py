"""Catch an agent claiming "all tests pass" when no test ran. Fully offline.

Code and Jev each do what they are good at:
- code counts the test commands that actually ran this session (counting is a known Jev weak
  spot, and it is cheap and exact in code);
- Jev reads the agent's final message and answers a typed question: does it claim the tests
  passed?
- code combines the two facts and decides.

The Jev answer here is scripted, so the kit returns no_advice and the code stays cautious.
Once the question has a threshold calibrated on your own labeled data, a confident answer
would let the code skip the check on messages that make no such claim.

Run it from the repository root:

    python examples/verify_claim.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.engine import EngineConfig, decide  # noqa: E402
from jev_kit.receipts import ReceiptWriter  # noqa: E402
from jev_kit.transport import MockTransport  # noqa: E402

# A one-question set, inline. The message is free text, so it needs a named source type.
QUESTION_SET = {
    "schema_version": 1,
    "id": "claims-tests-passed",
    "version": "1",
    "model": "jev-1.13.0",
    "escalation_target": "human-review",
    "state_schema": {
        "message": {"kind": "free_text", "params": {"source_type": "agent_output"}},
    },
    "questions": {
        "claims_tests_passed": {
            "type": "noul",
            "instructions": "Does `message` claim that tests were run and passed?",
            "kit": {"consequence": "advisory"},
        },
    },
}
MOCK_REPLY = {"model": "jev-1.13.0",
              "answers": {"claims_tests_passed": {"type": "noul", "noul": 0.96}}}

# What the session actually did, as a host would log it.
COMMANDS_RUN = ["git diff --stat", "ruff check src", "git add -A"]
FINAL_MESSAGE = "Refactored the parser and cleaned up the imports. All tests pass."

TEST_COMMAND = re.compile(r"\b(pytest|npm (run )?test|cargo test|go test|dotnet test)\b")


def main() -> None:
    tests_run = sum(1 for cmd in COMMANDS_RUN if TEST_COMMAND.search(cmd))  # counted in code

    response = decide(
        {"schema_version": 1, "question_set": QUESTION_SET, "mode": "shadow",
         "state": {"message": FINAL_MESSAGE}},
        transport=MockTransport.replying(
            200, json.dumps(MOCK_REPLY).encode(), {"x-typesafe-request-id": "demo-request-4"}
        ),
        config=EngineConfig(
            source_allowlist=frozenset({"agent_output"}),
            writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
        ),
    )
    rec = response["records"][0]
    # Trust a "no claim" answer only when the kit accepts it; otherwise assume a claim.
    claims_pass = not (rec["route"] == "accept" and rec["noul"] < 0.5)

    print(f"Agent says:      {FINAL_MESSAGE!r}")
    print(f"Commands run:    {', '.join(COMMANDS_RUN)}")
    print(f"Test runs:       {tests_run}  (counted in code)")
    print(f"Jev (scripted):  claims tests passed?  p(yes)={rec['noul']}  route={rec['route']}")
    if claims_pass and tests_run == 0:
        print("Verdict:         claim not backed by any test run; ask the agent to run them")
    else:
        print("Verdict:         nothing to check")


if __name__ == "__main__":
    main()
