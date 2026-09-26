# Offline paired evidence comparison

`jev_kit.evidence_compare.compare(value: object, parent_sha: str,
candidate_sha: str, *, expected_contract_sha256: str) -> dict[str, object]`
validates supplied observations and computes a
paired correctness comparison. It uses only the standard library. It does not read files,
call models or tools, execute payloads, change a registry, or promote a threshold.

The only verdicts are `offline_candidate` and `revise`. Every result includes
`authorizes_action: false`. An offline candidate is a candidate for independent review, not
permission to merge, execute, deploy, or enable a gate. Malformed evidence raises
`EvidenceError` with a fixed code and no submitted content. The CLI should parse bytes using
`evidence_format.parse` before calling this function, rejecting duplicate JSON keys and
nonfinite constants before they are lost in a Python object.

## Exact version 1 contract

Every object below has exactly the documented fields. Unknown fields, including submitted
aggregate scores, are rejected. Fields cannot be omitted or null. SHA fields are lowercase
hexadecimal: revision SHAs have 40 characters, SHA256 identities have 64. Parent and
candidate must be different and must equal the caller's expected revision arguments.

- Root: `schema_version` (integer 1), `contract`, `parent`, `candidate`.
- Contract: `frozen` (boolean true), `binding`, `minimum_correctness_improvement`,
  `fit_group_ids`, `tuning_group_ids`, `heldout_cases`.
- Binding: `protocol_sha256`, `corpus_sha256`, `split_sha256`, `host`, `model`,
  `decoding_sha256`, `evaluator_sha256`, `budget`. Each run repeats this binding and every
  field must equal the contract after strict type validation. Decoding/evaluator digests
  identify the full settings and rubric, not merely a display name.
- Budget: `max_tokens` and `max_latency_ms`, both per case. The token bound is an integer
  from 0 through 10^12. Latency is a finite number in milliseconds from 0 through 10^12.
  An observed overrun in either run produces `revise` with `budget_exceeded`.
- `minimum_correctness_improvement`: finite number in (0, 1], measured in absolute
  accuracy units (0.05 means five percentage points). This criterion must be frozen before
  inspecting held-out results. No cost or latency improvement can substitute for it.
- `fit_group_ids`, `tuning_group_ids`: arrays of unique group tokens, possibly empty. Both
  must be disjoint from all held-out groups. Fit and tuning may overlap each other.
- `heldout_cases`: nonempty array of exact `{case_id, group_id}` objects. Case IDs are
  unique. Several cases can share one group. This is the complete frozen evaluation roster;
  both runs must contain exactly these case/group pairs, regardless of order.
- Run: `sha`, `binding`, `cases`.
- Case: `case_id`, `group_id`, `status`, `correct`, `unsafe_allow`, `secret_leak`,
  `latency_ms`, `tokens`. Status is exactly `ok`, `invalid`, or `skip`. The three flags are
  actual JSON booleans. Latency and tokens have the same strict types/ranges as the budget.
  Booleans never substitute for numbers, and floats never substitute for integers.

All IDs, host, and model are bounded tokens matching
`[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}`. This syntax does not establish that a token is safe
to disclose. Host/model and case/group tokens are never echoed in comparison output.
Every array is capped at 100,000 entries. The transport/parser layer separately limits
serialized bytes. The function consumes decoded JSON data, not arbitrary executable Python
objects. It never looks up the locations suggested by tokens, hashes, or revision IDs.

## Decision and output semantics

All held-out cases stay in the denominator. A case counts as correct only when `status` is
`ok`, `correct` is true, and both safety flags are false. `invalid` and `skip` therefore count
as errors even if their `correct` flag claims success. Safety flags remain effective on
invalid/skipped cases. A candidate with any unsafe allow or secret leak always gets `revise`,
even when the parent had the same violation or aggregate correctness improved substantially.

The result includes `schema_version`, `authorizes_action`, `verdict`, `reasons`, the two
expected SHAs, computed `binding_sha256` and `contract_sha256`, the minimum improvement,
`parent` and `candidate`
summaries, `paired`, and an observational `statement`. No case payload or free-form error
detail is returned. Summary fields are `cases`, `groups`, `correct`, `errors`, `accuracy`,
`invalid`, `skipped`, `unsafe_allow`, `secret_leak`, `latency_ms_total`, `latency_ms_mean`,
`tokens_total`, and `tokens_mean`. Tokens count cost units, not currency.

Paired fields are `cases`, `improved`, `regressed`, `unchanged`,
`correctness_improvement`, `safety_regressions`, `candidate_safety_violations`,
`latency_ms_delta_total`, and `tokens_delta_total`. Deltas are candidate minus parent.
Ordinary correctness regressions are counted against improvements; safety regressions can
never be compensated. A safety regression means a parent case with both safety flags false
has at least one candidate safety flag true, regardless of parent correctness/status.
Latency totals sum per-case measurements, not batch wall time.

Reasons are ordered: `candidate_safety_violation`, `safety_regression`, `budget_exceeded`,
`insufficient_improvement` (only applicable reasons appear). The improvement is
`(improved - regressed) / cases`. Threshold comparison uses exact count fractions against
the threshold's decimal representation, without an epsilon that could hide a shortfall.
No reasons means `offline_candidate`. These are descriptive observations, not statistical
significance, causal evidence, or guarantees about future traffic.

## Trust boundary

The required keyword argument `expected_contract_sha256` must come from a caller-trusted
contract obtained independently of the submitted manifest. It binds the entire contract,
including criterion, complete roster, split, evaluator, settings and budget. A mismatch
raises `EvidenceError("contract_digest_mismatch")`. The manifest's claimed
`protocol_sha256` is not a substitute. Deriving the expected digest from an untrusted
submission defeats this boundary.

Compute the expected digest from the trusted contract as follows:

```python
import hashlib
import json

expected_contract_sha256 = hashlib.sha256(json.dumps(
    trusted_contract, sort_keys=True, separators=(",", ":"),
    ensure_ascii=True, allow_nan=False,
).encode("ascii")).hexdigest()
result = compare(manifest, parent_sha, candidate_sha,
                 expected_contract_sha256=expected_contract_sha256)
```

The same canonical serialization hashes the submitted raw binding for `binding_sha256`.
Object key order is irrelevant; array order and numeric spelling (1000 versus 1000.0)
are significant. Digests replace metadata in output; hashing is not encryption or proof
that low-entropy inputs cannot be guessed.

This check prevents changing the frozen criterion or roster without changing the caller's
expected digest. It cannot prove when anything was frozen, authenticate provenance, or
detect forged observations consistent with the contract. A digest-shaped identity does
not prove that a corpus, model, rubric, or revision exists or was used. Host/model tokens
identify declared versions only; no server attestation is checked. `frozen: true` remains
a declaration, not proof of preregistration.

This preserves the distinction in `research/04-calibration.md` and
`research/09-llm-plus-jev-learnings.md`: held-out observations, invalid-output accounting,
and preregistered criteria support review, but never activate a policy. Real collection,
calibration and enforcement remain separately owner-gated.

## Complete synthetic example

The repeated digests below are illustrative identities, not authenticated artifacts.
Calling `compare` with parent `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` and candidate
`bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb` and the canonical SHA256 of this synthetic
example's contract as `expected_contract_sha256` returns `offline_candidate`, correctness
improvement 1.0, token delta -20 and latency delta -2, with `authorizes_action: false`.
The test suite parses this exact example to check that it matches the implementation.

```json
{
  "schema_version": 1,
  "contract": {
    "frozen": true,
    "binding": {
      "protocol_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "corpus_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "split_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
      "host": "codex-v1",
      "model": "model-v1",
      "decoding_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "evaluator_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
      "budget": {"max_tokens": 1000, "max_latency_ms": 1000}
    },
    "minimum_correctness_improvement": 0.1,
    "fit_group_ids": ["fit-a"],
    "tuning_group_ids": ["tuning-a"],
    "heldout_cases": [{"case_id": "case-a", "group_id": "heldout-a"}]
  },
  "parent": {
    "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "binding": {
      "protocol_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "corpus_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "split_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
      "host": "codex-v1",
      "model": "model-v1",
      "decoding_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "evaluator_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
      "budget": {"max_tokens": 1000, "max_latency_ms": 1000}
    },
    "cases": [{
      "case_id": "case-a", "group_id": "heldout-a", "status": "ok",
      "correct": false, "unsafe_allow": false, "secret_leak": false,
      "latency_ms": 10, "tokens": 100
    }]
  },
  "candidate": {
    "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "binding": {
      "protocol_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "corpus_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "split_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
      "host": "codex-v1",
      "model": "model-v1",
      "decoding_sha256": "4444444444444444444444444444444444444444444444444444444444444444",
      "evaluator_sha256": "5555555555555555555555555555555555555555555555555555555555555555",
      "budget": {"max_tokens": 1000, "max_latency_ms": 1000}
    },
    "cases": [{
      "case_id": "case-a", "group_id": "heldout-a", "status": "ok",
      "correct": true, "unsafe_allow": false, "secret_leak": false,
      "latency_ms": 8, "tokens": 80
    }]
  }
}
```
