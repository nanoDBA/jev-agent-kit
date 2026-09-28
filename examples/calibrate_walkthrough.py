"""Walk one question from recorded answers to a registry entry, and see what changes. Offline.

This is the calibration workflow in docs/guides/calibration.md, run end to end on a small
INSTRUCTIONAL FIXTURE: the 60 synthetic messages and recorded jev-1.13.0 answers in
docs/research/data/claims-tests-passed-v1.json. The labels were written by an AI assistant,
not checked by a person, and there are far too few items. Nothing this script prints is a
threshold to deploy. It shows the mechanics and what an honest "not yet" looks like.

Steps:
1. Load (answer, label) pairs for one question fingerprint, dropping answers that timed out.
2. Split them with a fixed seed into a fit half and a frozen confirm half.
3. Report Brier score, expected calibration error and an isotonic (PAV) fit.
4. Ask the promotion evaluator for Noul bounds, then apply this script's own sample-size rule.
5. Show the insufficient-evidence path.
6. Write a registry entry to a temporary folder, load it, and show what the engine does with it.

Run it from the repository root:

    python examples/calibrate_walkthrough.py
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jev_kit.calibration.metrics import (  # noqa: E402 (after the src path setup)
    brier_score,
    coverage_at_threshold,
    expected_calibration_error,
)
from jev_kit.calibration.pav import pav_fit  # noqa: E402 (after the src path setup)
from jev_kit.calibration.promotion import select_bounds  # noqa: E402 (after the src path setup)
from jev_kit.engine import EngineConfig, decide  # noqa: E402 (after the src path setup)
from jev_kit.receipts import ReceiptWriter  # noqa: E402 (after the src path setup)
from jev_kit.transport import MockTransport  # noqa: E402 (after the src path setup)

DATA = REPO / "docs" / "research" / "data" / "claims-tests-passed-v1.json"

# The same one-question set as examples/verify_claim.py, so the fingerprint matches it.
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

SEED = 20260928
REFERENCE_ACCURACY = 0.95  # the accuracy you need on answered items; your choice, not the kit's
TOLERANCE = 0.0
# This script's own rule, on top of the evaluator: each class needs this many confirm items.
# 30 is a floor for a walkthrough, not a recommendation; set yours from the risk you accept.
MIN_PER_CLASS_CONFIRM = 30


def _reply(noul: float) -> bytes:
    return json.dumps({"model": "jev-1.13.0",
                       "answers": {"claims_tests_passed": {"type": "noul", "noul": noul}}}).encode()


def _decide(noul: float, *, mode: str, registry: Path | None) -> dict[str, object]:
    response = decide(
        {"schema_version": 1, "question_set": QUESTION_SET, "mode": mode,
         "state": {"message": "Fixed the pagination bug; all 42 tests pass."}},
        transport=MockTransport.replying(200, _reply(noul), {"x-typesafe-request-id": "demo-cal"}),
        config=EngineConfig(
            registry_path=str(registry) if registry is not None else None,
            source_allowlist=frozenset({"agent_output"}),
            writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
        ),
    )
    record: dict[str, object] = response["records"][0]
    return record


def main() -> None:
    items = json.loads(DATA.read_text(encoding="utf-8"))["items"]
    answered = [(float(i["p"]), int(i["label"])) for i in items if i["p"] is not None]
    print("Step 1  recorded answers (synthetic fixture, AI-written labels)")
    timed_out = len(items) - len(answered)
    print(f"  items: {len(items)}  answered: {len(answered)}  timed out: {timed_out}")

    shuffled = answered[:]
    random.Random(SEED).shuffle(shuffled)
    half = len(shuffled) // 2
    fit, confirm = shuffled[:half], shuffled[half:]
    print("Step 2  fixed-seed split")
    print(f"  fit: {len(fit)}  confirm: {len(confirm)}  (seed {SEED})")

    model = pav_fit(fit)
    calibrated = [(model.predict(p), y) for p, y in confirm]
    print("Step 3  metrics on the confirm half")
    print(f"  Brier raw: {brier_score(confirm):.3f}"
          f"  ECE raw: {expected_calibration_error(confirm):.3f}"
          f"  ECE after isotonic fit: {expected_calibration_error(calibrated):.3f}")

    result = select_bounds(fit, confirm, reference_accuracy=REFERENCE_ACCURACY,
                           tolerance=TOLERANCE)
    print("Step 4  promotion evaluator")
    print(f"  evaluator: {result.reason}  no_bound={result.no_bound}  yes_bound={result.yes_bound}"
          f"  coverage={result.coverage}  accuracy={result.accuracy}")
    per_class = min(sum(1 for _, y in confirm if y == 1), sum(1 for _, y in confirm if y == 0))
    promote = result.promotable and per_class >= MIN_PER_CLASS_CONFIRM
    print(f"  confirm items in the smaller class: {per_class} (this script requires "
          f"{MIN_PER_CLASS_CONFIRM})")
    print(f"  decision: {'promote' if promote else 'do not promote; collect more labeled answers'}")
    if result.no_bound is not None and result.yes_bound is not None:
        cov = coverage_at_threshold(confirm, low=result.no_bound, high=result.yes_bound)
        print(f"  bounds {result.yes_bound - result.no_bound:.2f} apart that answer "
              f"{cov.answered_fraction:.0%} of {len(confirm)} items: too few to tell")

    print("Step 5  insufficient evidence")
    empty = select_bounds([], confirm, reference_accuracy=REFERENCE_ACCURACY, tolerance=TOLERANCE)
    print(f"  no fit samples: {empty.reason}")

    print("Step 6  a registry entry, written to a temporary folder only")
    fingerprint = str(_decide(0.5, mode="shadow", registry=None)["fingerprint"])
    registry = Path(tempfile.mkdtemp(prefix="jev-registry-")) / "registry.json"
    entry = {
        "status": "calibrated",
        "type": "noul",
        "threshold": {"no_bound": 0.1, "yes_bound": 0.9},
        "date": "2026-09-28",
        "escalation_target": "human-review",
        "evidence_ref": "fixture-only claims-tests-passed-v1",
    }
    registry.write_text(json.dumps({"schema_version": 1, "entries": {fingerprint: entry}},
                                   indent=2), encoding="utf-8")
    print(f"  fingerprint: {fingerprint[:12]}...  bounds: no<=0.1  yes>=0.9  (illustrative)")
    before = _decide(0.97, mode="enforce", registry=None)
    after = _decide(0.97, mode="enforce", registry=registry)
    print(f"  without registry: threshold_status={before['threshold_status']}"
          f"  would_route={before['would_route']}  route={before['route']}")
    print(f"  with registry:    threshold_status={after['threshold_status']}"
          f"  would_route={after['would_route']}  route={after['route']}  model={after['model']}")
    print("  The answer here is replayed, so the kit never acts on it: route stays no_advice.")
    print("  With a live answer in enforce mode, would_route is what route would become.")


if __name__ == "__main__":
    main()
