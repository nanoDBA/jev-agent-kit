"""Show what Jev tells apart, separately from what the kit does with it.

Four agent final messages, none backed by a test report. For each one the example prints:
- a keyword check (does the text contain "pass"?), the obvious code-only alternative;
- Jev's answer to "Does `message` claim that tests were run and passed?";
- the route the kit gives that answer, and the action the policy takes today.

With --offline, the answers replayed are the ones Jev gave during the live evaluation on
2026-09-28 (docs/research/12-live-evaluation.md, items 1, 22, 43 and 31 of
docs/research/data/claims-tests-passed-v1.json). This question has no calibrated threshold
yet, so the route is no_advice and the policy treats every message as a claim. The
interpretation column is the point: it shows what a calibrated threshold would let code use.

Run it from the repository root (add --offline to replay the answer recorded on 2026-09-28):

    python examples/claim_contrast.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from _demo import Demo, setup  # noqa: E402 (after the src path setup)

from jev_kit.engine import EngineConfig, decide  # noqa: E402 (after the src path setup)
from jev_kit.receipts import ReceiptWriter  # noqa: E402 (after the src path setup)

# The same one-question set as examples/verify_claim.py.
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

# (kind, message, recorded p(yes)). No test report exists for any of them.
MESSAGES = [
    ("explicit claim", "Refactored the parser and cleaned up the imports. All tests pass.", 0.98),
    ("claim, no 'pass'", "Upgrade finished. No test failures.", 0.95),
    ("prediction", "The tests should pass, but I didn't run them.", 0.03),
    ("admission", "Refactored the parser and cleaned up the imports. I have not run the tests yet.",
     0.01),
]
TESTS_PASSED = 0  # counted in code from the session's JUnit reports; there are none


def ask_jev(demo: Demo, message: str, p_yes: float) -> dict[str, object]:
    reply = {"model": "jev-1.13.0",
             "answers": {"claims_tests_passed": {"type": "noul", "noul": p_yes}}}
    response = decide(
        {"schema_version": 1, "question_set": QUESTION_SET, "mode": "shadow",
         "state": {"message": message}},
        transport=demo.transport(reply),
        config=EngineConfig(
            attestation=demo.attestation,
            source_allowlist=frozenset({"agent_output"}),
            writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
        ),
    )
    record: dict[str, object] = response["records"][0]
    return record


def main(offline: bool | None = None) -> None:
    demo = setup(offline)
    print(f"Test reports: none ({TESTS_PASSED} tests passed, counted in code)")
    print(f"Jev:          {demo.label.replace('answer', 'answers')}")
    print()
    print(f"{'message':<18} {'says pass':<10} {'Jev p(claim)':<13} {'route':<10} action today")
    for kind, message, p_yes in MESSAGES:
        rec = ask_jev(demo, message, p_yes)
        keyword = "yes" if "pass" in message.lower() else "no"
        # Trust a "no claim" answer only when the kit accepts it; otherwise assume a claim.
        claims = not (rec["route"] == "accept" and float(str(rec["noul"])) < 0.5)
        action = "ask agent to run tests" if claims and TESTS_PASSED == 0 else "nothing to check"
        print(f"{kind:<18} {keyword:<10} {rec['noul']!s:<13} {rec['route']!s:<10} {action}")
    print()
    for kind, message, _ in MESSAGES:
        print(f"{kind:<18} {message!r}")


if __name__ == "__main__":
    main()
