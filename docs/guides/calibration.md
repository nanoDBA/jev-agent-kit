# Calibrating a question

Until a question has a measured threshold, the kit never acts on its answers: advisory
questions return `no_advice` and gates return `ask`. This guide takes one yes/no (Noul)
question from shadow-mode answers to a registry entry, and shows what to do when the evidence
is not good enough, which is the usual outcome at first.

To see every step run offline first:

```sh
python examples/calibrate_walkthrough.py
```

It uses the recorded answers from our [first live evaluation](../research/12-live-evaluation.md)
as an instructional fixture. Those labels were written by an AI assistant and there are only
57 answered items, so the walkthrough ends by declining. Do not copy its numbers anywhere.

## 1. Collect answers in shadow mode

Configure live calls as in [Live calls](live-calls.md), keep `"mode": "shadow"`, and run the
question on real traffic. Every decision is written to a receipt JSONL file in
`JEV_KIT_RECEIPTS_DIR` (or the per-user default), named `<start time>-<pid>.jsonl`.

Receipts hold no request text. To label an answer later you need to know what was asked, so
record each decision's `decision_id` (it is in every record the engine returns) next to your
own copy of the input or a pointer to it, kept wherever your data rules allow.

**You have:** receipt files, and a private map from `decision_id` to the input.

## 2. Read the committed decisions

```sh
python tools/receipts_report.py path/to/receipts/*.jsonl
python tools/receipts_report.py path/to/receipts/a.jsonl --duckdb decisions.duckdb
```

The first prints a JSON summary: counts by `route`, `fail_reason`, `mode` and `is_mock`, and a
per-fingerprint count with the mean `noul`. It only counts call batches that have their
commit marker, so a file cut short by a crash never contributes half a call. `--duckdb`
(optional; needs the `duckdb` package) also loads the first file's decisions into a table.

In Python, `read_receipts(path)` from the same file returns the committed decisions as dicts.
Keep only rows where `fingerprint` is the question you are calibrating, `is_mock` is false,
`served_model` equals `requested_model`, and `noul` is a number.

**You have:** a list of `(decision_id, noul)` for one fingerprint.

## 3. Label independently

Label each input by hand, or by a process that does not see Jev's answer and was not written
by whoever wrote the question. Write the rule down first (for example: "claims tests were run
and passed for the current change"), and decide ambiguous cases by the rule, not by Jev.
Store labels as `0` or `1` against `decision_id`.

**You have:** `(noul, label)` pairs.

## 4. Split, fit and measure

Split the pairs once, before looking at results, into a fit half and a frozen confirm half. If
several items come from the same session or source, keep them in the same half.

```python
from jev_kit.calibration.metrics import brier_score, expected_calibration_error
from jev_kit.calibration.pav import pav_fit

model = pav_fit(fit)                       # isotonic calibration of raw noul
print(brier_score(confirm), expected_calibration_error(confirm))
print(expected_calibration_error([(model.predict(p), y) for p, y in confirm]))
```

The registry's bounds apply to the raw `noul` Jev returns, so choose bounds on raw values.
The isotonic fit tells you how far raw values are from true frequencies.

## 5. Ask the promotion evaluator

```python
from jev_kit.calibration.promotion import select_bounds

result = select_bounds(fit, confirm, reference_accuracy=0.95, tolerance=0.0)
```

It picks the `no_bound` and `yes_bound` that answer the most fit items while accuracy on
answered items stays at or above `reference_accuracy - tolerance`, then checks the same bounds
on the confirm half. `result.reason` is one of:

| Reason | Meaning |
| --- | --- |
| `meets_criteria` | Both halves met the floor. A recommendation, not a promotion. |
| `insufficient_data` | One of the halves is empty. |
| `invalid_criteria` | The accuracy or tolerance you passed is out of range. |
| `no_bounds_meet_accuracy_floor` | No bounds reach the floor on the fit half. |
| `confirm_split_below_floor` | The chosen bounds fail on the confirm half. |

`meets_criteria` does not check that you have enough items. Add your own rule for that; the
walkthrough requires 30 items of each class in the confirm half and so declines:

```text
Step 1  recorded answers (synthetic fixture, AI-written labels)
  items: 60  answered: 57  timed out: 3
Step 2  fixed-seed split
  fit: 28  confirm: 29  (seed 20260928)
Step 3  metrics on the confirm half
  Brier raw: 0.000  ECE raw: 0.019  ECE after isotonic fit: 0.000
Step 4  promotion evaluator
  evaluator: meets_criteria  no_bound=0.12  yes_bound=0.13  coverage=1.0  accuracy=1.0
  confirm items in the smaller class: 14 (this script requires 30)
  decision: do not promote; collect more labeled answers
  bounds 0.01 apart that answer 100% of 29 items: too few to tell
Step 5  insufficient evidence
  no fit samples: insufficient_data
```

Perfect scores on 29 items with bounds 0.01 apart mean the sample is too small, not that the
question is solved. The evaluator handles Noul questions only; for Choice and Score questions
you choose `min_confidence` and `min_margin` or intervals yourself with the same split.

**You have:** a recommendation with its confirm-half coverage and accuracy, or a reason to
keep collecting.

## 6. Register the threshold

The registry is a JSON file keyed by question fingerprint:

```json
{
  "schema_version": 1,
  "entries": {
    "<64 hex fingerprint>": {
      "status": "calibrated",
      "type": "noul",
      "threshold": {"no_bound": 0.1, "yes_bound": 0.9},
      "date": "2026-09-28",
      "escalation_target": "human-review",
      "evidence_ref": "claims-tests-passed eval 2026-09-28"
    }
  }
}
```

- `status` is `calibrated`, `uncalibrated` or `never_auto_accept`. Only `calibrated` needs
  `type`, `threshold` and `date`.
- `threshold` is `{"no_bound", "yes_bound"}` for Noul, `{"min_confidence", "min_margin"}` for
  Choice, and `{"min_confidence", "intervals": [{"lower", "upper", "label"}]}` for Score.
- `escalation_target` must equal the question set's `escalation_target`, or every answer for
  that question fails with a configuration error.
- `evidence_ref` points at your record of the measurement: plain letters, digits, spaces and
  `._:-`, up to 200 characters.
- Unknown fields, duplicate keys and non-finite numbers are rejected. A missing file means no
  thresholds; a malformed one fails every call.

The engine reads the file from `EngineConfig(registry_path=...)` or, for the CLI and hooks,
from `JEV_KIT_REGISTRY`. Take the fingerprint from a receipt of the exact question you
measured. Changing the wording, options, model or data rules changes the fingerprint, and the
entry no longer applies.

## 7. Check runtime behavior

With the registry loaded, run a few known inputs in shadow mode and read the receipts:
`threshold_status` should be `calibrated`, `threshold` should show your bounds, and
`would_route` shows what enforce mode would do. The walkthrough shows the same with a replayed
answer:

```text
Step 6  a registry entry, written to a temporary folder only
  fingerprint: b22c92471286...  bounds: no<=0.1  yes>=0.9  (illustrative)
  without registry: threshold_status=None  would_route=None  route=no_advice
  with registry:    threshold_status=calibrated  would_route=accept  route=no_advice  model=mock
  The answer here is replayed, so the kit never acts on it: route stays no_advice.
  With a live answer in enforce mode, would_route is what route would become.
```

Compare `would_route` with your labels over a period of shadow traffic before switching the
question to `"mode": "enforce"`. Enforce mode needs the environment owner's approval. For a
gate, Jev's answer can still only let a call proceed as far as your agent's own permissions
allow.

## When promotion is declined

Keep the question in shadow mode; nothing else changes. Then, depending on the reason:

- Too few items, or `insufficient_data`: collect and label more, and rerun on a fresh split.
- `no_bounds_meet_accuracy_floor` or `confirm_split_below_floor`: read the misses. They often
  show a question that is ambiguous (for example, whether "tests passed yesterday" counts), so
  sharpen the wording or the labeling rule. A new wording is a new fingerprint, so start the
  measurement again.
- The question may simply not be one Jev answers well. Recording `never_auto_accept` for its
  fingerprint keeps it advisory permanently.
