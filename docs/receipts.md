# Receipts

Every decision is written to a JSONL receipt before the kit returns an answer. Receipts are
what you measure thresholds from. They hold decision details and digests, never raw state or
API keys.

`python examples/show_receipt.py` prints one, with long ids and digests shortened.

| Field | What it tells you |
| --- | --- |
| `question_id`, `question_set` | Which question was asked, from which question set and version. |
| `fingerprint` | Identifies the exact question, model and data rules, including, for the hooks, the code that built the request. A threshold belongs to one fingerprint, so changing any of them retires old measurements. |
| `requested_model`, `served_model` | The pinned model version, and the version that answered. A mismatch is rejected. |
| `model` | Who really answered. `mock` marks a replayed or scripted answer, so nobody can mistake it for a live Jev answer. |
| `distribution` | The whole answer, not just the top choice, so you can measure thresholds later. |
| `threshold_status` | Whether a measured threshold exists for this fingerprint. |
| `route` | What the kit told your code: `accept`, `ask` or `no_advice`. |
| `sent_digest` | A hash of the exact bytes sent. The request text itself is not stored. |

Each call's decision lines are followed by a commit marker, written and flushed together, so
a reader can tell a complete record from one cut short by a crash.

Receipts go to `JEV_KIT_RECEIPTS_DIR` when it is set to an absolute path, and otherwise to a
per-user data folder. The kit never resolves that location against the working directory:
if no absolute location can be found, the receipt write fails instead. It does not check
whether the chosen folder is inside a repository, so if `JEV_KIT_RECEIPTS_DIR`, your home
folder or your data folder lives inside one, receipts go there. Point
`JEV_KIT_RECEIPTS_DIR` outside any repository if that applies to you. The full format is in
[the receipt schema](schemas/receipt.schema.json).
