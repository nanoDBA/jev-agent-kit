"""Catch an agent claiming "all tests pass" when no test ran. Fully offline.

Code and Jev each do what they are good at:
- code counts the tests that passed, from the test runner's own report (counting is a known
  Jev weak spot, and it is cheap and exact in code);
- Jev reads the agent's final message and answers a typed question: does it claim the tests
  passed?
- code combines the two facts and decides.

The Jev answer here is scripted, so the kit returns no_advice and the code stays cautious.
Once the question has a threshold calibrated on your own labeled data, a confident answer
would let the code skip the check on messages that make no such claim.

A command line and its exit code cannot show that tests ran: `pytest -V` exits 0 and runs
nothing, and `pytest || echo done` hides a failure. So the count comes only from JUnit XML
reports the host collected this session, in the shape `pytest --junitxml` writes. No report,
or one that cannot be read or has any other shape, counts as zero passed tests. It shows that
tests ran and passed, not that they were the right tests.

Run it from the repository root:

    python examples/verify_claim.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.engine import EngineConfig, decide  # noqa: E402 (after the src path setup)
from jev_kit.receipts import ReceiptWriter  # noqa: E402 (after the src path setup)
from jev_kit.transport import MockTransport  # noqa: E402 (after the src path setup)

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

# What the session ran, and the test reports the host collected (none: no test ran).
COMMANDS_RUN = ["git diff --stat", "ruff check src", "git add -A"]
TEST_REPORTS: list[str] = []
FINAL_MESSAGE = "Refactored the parser and cleaned up the imports. All tests pass."


# The report shape this example accepts: what `pytest --junitxml` writes. Anything else,
# including other JUnit dialects, namespaces and unknown elements, makes the whole report
# count as zero passes rather than guessing.
SUITE_CHILDREN = {"properties", "testcase", "system-out", "system-err"}
CASE_ATTRIBUTES = {"name", "classname", "time", "file", "line"}
CASE_CHILDREN_NOT_PASSED = {"failure", "error", "skipped"}
CASE_CHILDREN_NEUTRAL = {"properties", "system-out", "system-err"}


def passed_tests(junit_xml: str) -> int:
    """Passed test cases in one pytest-style JUnit XML report; anything unsupported is zero."""
    try:
        root = ET.fromstring(junit_xml)
    except ET.ParseError:
        return 0
    if root.tag == "testsuites":
        suites = list(root)
        if not suites or any(suite.tag != "testsuite" for suite in suites):
            return 0
    elif root.tag == "testsuite":
        suites = [root]
    else:
        return 0
    passed = 0
    for suite in suites:
        cases = [child for child in suite if child.tag == "testcase"]
        if any(child.tag not in SUITE_CHILDREN for child in suite):
            return 0
        if suite.get("tests") != str(len(cases)):  # the suite's own count must agree
            return 0
        for case in cases:
            if set(case.attrib) - CASE_ATTRIBUTES:
                return 0
            tags = {child.tag for child in case}
            if tags - CASE_CHILDREN_NOT_PASSED - CASE_CHILDREN_NEUTRAL:
                return 0
            if not tags & CASE_CHILDREN_NOT_PASSED:
                passed += 1
    return passed


def main(test_reports: list[str] = TEST_REPORTS) -> None:
    tests_passed = sum(passed_tests(report) for report in test_reports)  # counted in code

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
    reports = len(test_reports)
    print(f"Tests passed:    {tests_passed}  (from {reports} test reports, counted in code)")
    print(f"Jev (scripted):  claims tests passed?  p(yes)={rec['noul']}  route={rec['route']}")
    if claims_pass and tests_passed == 0:
        print("Verdict:         claim not backed by a test report; ask the agent to run them")
    else:
        print(f"Verdict:         {tests_passed} passing tests are on record")


if __name__ == "__main__":
    main()
