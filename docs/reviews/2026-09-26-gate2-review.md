# Independent adversarial review, round 3 (gate2)

- Date: 2026-09-26
- Scope: `git diff origin/phase-0...phase-1-impl` (whole package, 45 files, +7999) and the tree.
- Reviewer: fresh pass. Prior rounds: C01-C25 (codex), GATE-01..08 (gate-review). All marked fixed.
- Verdict: revise (one major). No blockers. The accept-path invariant holds.

## Gate results (observed)

- `pip install -e . --no-deps -q`: ok
- `python -m pytest -q`: all pass (297 tests, 0 failures)
- `python -m ruff check src tests scripts`: All checks passed
- `python -m mypy`: Success, no issues in 30 source files
- No `type: ignore` in `src/`; the two in tests are on deliberate misuse assertions.
  No `noqa`, `cast(`, or `# nosec`. `Any` appears only at JSON parse boundaries.

Note: `docs/reviews/2026-09-26-gate-review.md` (the prior gate review) is present but
**empty** on this branch (untracked stub). Its findings were read from the spec's resolution
tables and the code comments instead.

## Accept-path invariant: re-verified, holds

Effective `route == accept` is reachable only through `routing.resolve_route` step 5
(`routing.py:226`), which requires, in order: `failed=False`, `is_mock=False`, `mode=ENFORCE`,
a `CALIBRATED` entry with a `candidate` that `clears`. In `engine._decide` the candidate is
computed only when the entry is `CALIBRATED`, `escalation_target` matches the question set, and
the answer validated (`engine.py:474-480`). The send path additionally requires a real 2xx,
not-expired, model-matched, schema-valid response (`engine.py:431-451`), a passed egress scan
and, for a non-mock transport, a passed attestation/DPA check (`engine.py:408-415`). Accept is
only kept after the receipt fsyncs; a receipt failure downgrades to ask/no_advice
(`engine.py:594-605`). The one live accept test exercises this end to end
(`tests/test_live_transport.py:131`).

- GATE-01 re-verify: provenance is read from the transport TYPE (`type(transport).is_mock`,
  `engine.py:309`), so a per-instance `is_mock=False` cannot flip it
  (`tests/test_engine.py:365` proves a relabeled mock still routes ask). A subclass that sets
  `is_mock=False` on the class would bypass mock-routing, but such an object is a
  caller-authored transport, not scripted evidence; the accept path then still demands a
  calibrated entry, enforce mode, and a valid attestation. This matches the spec threat model
  (transport is the injectable seam) and is acceptable, not a finding.
- Mock never accepts: gates and advisory both fail-route under `is_mock`
  (`routing.py:213`); `would_route` stays visible. Correct.

## Findings

### MAJOR-1: decide_batch bypasses the process-wide call cap (GATE-05 regression)

`engine.decide_batch` creates a fresh `RateBudget()` whenever `config.rate_budget is None`
(`engine.py:672-674`), instead of the process-wide default returned by `_default_budget()`.
Single `decide`/`run_json` calls do share the default (`engine.py:341`), but batches do not, so
the per-process cap does not span batches or span batch-vs-single calls. Confirmed empirically:
with `_DEFAULT_BUDGET = RateBudget(max_calls=1)`, a 3-request `decide_batch` let all three
through and left the default's `calls_made == 0`. This defeats the runaway-prevention control of
spec story 67 ("shared per-process call cap") and contradicts both the GATE-05 fix comment at
`engine.py:340-341` ("hold across sequential calls, not only within one batch") and
decide_batch's own docstring ("the per-process cap ... apply across the whole batch"). Not an
accept-path/authority bypass; it is a cost/DoS-loop control that silently does not hold.

Fix: in `decide_batch`, use `_default_budget()` in place of `RateBudget()` when
`shared.rate_budget is None` (`engine.py:673`).

### MINOR-1: reported_tokens accepts a JSON boolean

`engine.py:446` gates on `isinstance(usage.get("input_tokens"), int)`; `bool` is an `int`
subclass, so `{"usage": {"input_tokens": true}}` records `reported_tokens: true` in the receipt.
Informational field only (estimator-error visibility), never routed on. Fix: also reject
`bool` (`and not isinstance(..., bool)`).

### NIT-1: never_auto_accept enforce gate labeled `uncalibrated`

An enforce gate with a `never_auto_accept` entry falls into `routing.py:221` and returns
`fail_reason=UNCALIBRATED`. The route (ask) is correct and matches the matrix; only the reason
string is imprecise for audit queries.

### NIT-2: LOG kind leaves a bare single-token word unmasked

`_reduce_log` masks quoted spans, `k=v` pairs, hyphenated slugs and contacts (`egress.py:136`),
but a lone non-hyphenated alphanumeric word (e.g. `password foobarbaz`) is not template-reduced
and only blocked if it independently trips a Tier 1 detector. Best-effort behavior consistent
with the spec's "codes and templates" wording; noted for completeness.

### NIT-3: METRIC kind sends bounded strings verbatim

A `metric` field passes any `_METRIC_TOKEN`-shaped string (<=64 chars, incl. `_`) unchanged
(`egress.py:186-190`); a short underscore-bearing secret declared as `metric` would not be
caught by Tier 1 (no `sk_`-style rule). This is operator misdeclaration of a documented
"Tier 3, non-personal, sent as-is" kind, and the token pattern bounds the blast radius.

## Re-verified clean

- GATE-02 command reduction: env-assignment, path, long/short-flag values all dropped
  (`egress.py:149`, tests 200-233).
- GATE-03 log/IPv6: bare hyphenated identifiers masked; full, compressed and `::1` IPv6 masked
  (`egress.py:102-133`, tests 239-256).
- GATE-04 split auth: key/value-split credentials caught by the structural pass and the
  value-only string scan (`egress.py:432-474`, tests 418-446).
- GATE-06 sent-digest only when a send left (`engine.py:428-430`, test 410).
- GATE-08 raising transport still yields per-question records (`engine.py:454-456`, test 421).
- Key safety: bearer header only, drained/closed error bodies, no key in bytes/receipts
  (`transport.py`, `secrets.py`; command text/output never in exceptions, `secrets.py:27,46`).
- Strict JSON (dup keys, non-finite) on registry, question set, response
  (`fingerprint.py`, `registry.py`).
- Concurrency: MockTransport cursor and receipt appends are locked; `decide_batch` shares one
  budget within a batch and preserves input order.
- Receipt fault injection covers open failure, fsync failure, and NaN (`tests/test_receipts.py`).
