# Evidence-backed skill foundations: verification record

Tracking: issue 41 in the private development repository (not public).
Implementation base: `ec3cda506ccc04652563ca62d1da993204dc9b8c` (`phase-0`).
Tested implementation: `bee7ec769eb06692553f8ac9b5b2bb327823e43a`.

Disposition: ready for independent PR review as an offline foundation. This does not approve
PR #40, claim real-traffic calibration, authorize merging, or activate a host. This record is
written after the tested implementation commit and changes documentation only.

## Implemented scope

- Runtime skill version 2 checks evidence sufficiency before inference and after permitted
  transforms, distinguishes missing evidence from a negative observation, and loads only
  the relevant contract. Its four question sets and their fingerprints are unchanged.
- A strict finding checker binds a separately controlled obligation contract to the expected
  candidate SHA, declared reviewer, host-specific checks, positive controls, three gates,
  and exact local artifact bytes. It reports consistency, not authenticated verification.
- A paired comparison checker binds an externally expected frozen-contract digest, matched
  cases and settings, and disjoint held-out groups. It derives aggregates from observations,
  counts invalid/skipped results as errors, and rejects any candidate safety violation.
- Maintenance guidance records attribution, bounded changes, failed attempts, independent
  closure, no-op deduplication, and escalation limits in the existing issue/PR graph.

No runtime engine, egress transform, question JSON, threshold registry, host configuration,
ADR, or activation policy changed. No live Jev call, dependency installation, credential
change, host arming, enforcement, merge, or replacement Beads store occurred. Beads
authentication remains unavailable; #41 is the temporary durable tracking node.

## Gates observed by the main implementation agent

Windows, Python 3.13.15. `PYTHONPATH` pointed at this isolated worktree's `src`; an import
check confirmed that `jev_kit.__file__` resolved inside this worktree, not the previously
installed editable review checkout. No global editable installation was changed.

| Gate | Observed result |
| --- | --- |
| `python -m pytest -o addopts='' -q` | 552 passed, 14.20 seconds, no skipped cases |
| `python -m ruff check src tests scripts tools` | All checks passed |
| `python -m mypy` | Success, 58 source files, repository strict configuration |
| `git diff --check` | No whitespace errors |

The full gate results above are main-agent observations, not claims that every independent
reviewer reran every gate. No new `Any`, `type: ignore`, or `noqa` bypass was added to runtime
code. Tests are synthetic and offline. Temporary installer targets check both symlink and
copy behavior without touching actual host skill directories.

## Independent review and repairs

Three separate reviewers inspected code they had not authored: finding checker and CLI;
paired comparator and shared readers; runtime skill/contracts and maintenance procedure.
They reviewed `fa7c0d49d10c9a1f2e0305fdba1cd0e38bc0913a` and the relevant subsequent deltas
included in the tested implementation commit above.

| ID | Severity | Observation | Resolution and independent evidence |
| --- | --- | --- | --- |
| E1 | minor | A non-pass gate status short-circuited exit-code type validation. | Integer validation now runs unconditionally. Added 20 mixed status/type tests; independent public-CLI probes rejected all 20 malformed combinations and preserved five valid-integer controls. VERIFIED. |
| E2 | minor, conditional | Pathname stat preceded open, leaving a regular-to-pipe replacement gap. | Nonblocking open where supported plus regular-file validation of the actual descriptor before reading. A real pipe substituted after a regular-file control was rejected before reading while its writer stayed open; the rejected descriptor closed. Independently reproduced. VERIFIED. |

No remaining blockers, majors, or minors were reported within those reviewed scopes.
The comparator reviewer independently ran all 117 scoped tests and probed contract tampering,
complete rosters, skip/invalid accounting, safety overrides, and exact threshold boundaries.
The skill reviewer independently ran eight structural checks with an invented-field negative
control; the main agent additionally ran two temporary installer integration tests.

## Skill behavior smoke comparison

The complete six written answers, assertion evidence, and methodological limits are retained
in [evidence-foundations-smoke.json](evidence-foundations-smoke.json). Prompts and expectations
are in `skills/jev-runtime/evals/evals.json`.

| Configuration | Observed assertions |
| --- | --- |
| Version 1 baseline | 9 of 9 passed |
| Version 2 candidate | 9 of 9 passed |

The observed difference is zero. The revised instructions are more explicit, but this test
does not establish a behavioral improvement. There was one agent session per configuration,
three scenarios in shared context, visible expected outputs, no live execution, and no
independent replicate-variance estimate. Timing/token metrics were unavailable, not zero.
Final skill text was reread after restoring the existing mirror-check and both-orders rules;
the scenarios were unaffected, but that reread is not another experimental replicate.

The skill-creator process supplied paired cases, independent grading and a local static
human-review viewer. Writing-for-agents kept runtime invariants in the core and task-specific
details in one reference. Evidence-integrity required positive controls and honest limits.

## Remaining boundaries

Human review of the revised skill is still needed. A stronger future behavior evaluation
must hide expected answers, use independent repeated runs and harder cases, exercise real
public paths offline, and separate development from untouched held-out groups. Do not
promote anything on this smoke comparison.

Consistent records can still be forged. The tools do not authenticate reviewers, prove that
commands ran on the asserted revision, establish complete coverage, or replace independent
observation. Artifact reads assume a stable local filesystem; descriptor checks do not give
a universal I/O deadline or a hostile-filesystem sandbox. Live Windows/Linux/macOS host
behavior and real-traffic calibration were not measured. Existing PR #40 findings are not
closed by this work, and all owner-gated boundaries remain intact.

## Follow-up: Claude's PR #42 E1

Claude independently reviewed `9a38459f5d4fe64776ee6be6cc680d8846798b97`, reran the original
552-test gates, and reported one non-blocking library-error finding in
a review comment on PR 42 in the private development repository.
This E1 is separate from the earlier internal E1 in the table above.

The main agent reproduced the raw path-bearing error through public `check()` before fixing
it: 17 selected cases failed and the existing NUL rejection control passed. The cause was
an artifact filesystem exception boundary present only in the CLI, not the library.
Non-printable artifact paths now reject before filesystem access; resolution/read failures
become fixed-code `EvidenceError("artifact_io")` with normal exception chaining suppressed.
Existing safe confinement, digest, file-type, and size errors retain their original codes.

Twenty added tests cover the reported control character, other non-printable characters,
missing roots/artifacts, observed injected read/resolution failures, and CLI non-disclosure.
Final main-agent gates: **572 passed in 16.59 seconds**, Ruff clean, strict mypy clean across
58 source files, and no whitespace errors. Source import was confirmed in this worktree.

A separate reviewer independently exercised 29 library cases, two CLI cases, and positive
controls before/after fault injection, reporting no actionable findings in the three-file
implementation/test/contract delta. Actual symlink-loop behavior on older Python versions
was not exercised; its `RuntimeError` path was injected. The guarantee excludes debuggers
that deliberately inspect exception contexts or locals. Claude is asked to verify the
pushed follow-up head before carrying forward the PR disposition. PR #40 is unchanged.
