# Evidence-backed skill maintenance

Status: offline foundation, not a gate promotion or a claim that PR #40 is fixed.
Tracking: issue #41 in the maintainer's private working repository.

This adapts selective retrieval, evidence-linked constraints, attributed revisions, and
matched parent/candidate evaluation from [EvoOntology](https://arxiv.org/abs/2609.15779),
sections 3 and 4. Its reported data-agent results are not measurements of this kit and
do not establish security guarantees. No external implementation or dependency is used.

## Runtime and maintenance are different interfaces

The canonical runtime skill keeps invariant safety rules always loaded. Its small
`references/evidence-contracts.md` index selects only the evidence needed for the current
question family. It does not load the research archive, review history, or every question
set into every runtime call. Required facts lost through redaction remain unknown.

This document is for maintainers. It does not add a runtime hook, call Jev, change a question
fingerprint, change egress policy, or promote a threshold. Keep shared safety semantics the
same across Claude Code, Codex, and Hermes. Record different host timeout, parsing, and
transport observations separately; one host passing is not evidence for another.

## Review graph and lifecycle

Use existing Beads nodes and linked GitHub issues/PRs, not a second queue. While Beads is
unavailable, use the existing GitHub node; do not repair credentials or create a replacement
store as part of a review. Mirror links only after legitimate access returns.

A finding node owns atomic obligation IDs. Each obligation links the requirement, relevant
code, regression check, positive control, and required host. Split unrelated failure modes,
even when they share one module. The dependency is:

`requirement -> obligation -> probe + control -> exact-SHA evidence -> reviewer decision`

An implementation claim changes `open` to `claimed_fixed`, not `verified`. The independent
reviewer freezes the obligation contract before re-running checks. The implementer may
propose changes to it, but may not drop a failed obligation or weaken its expectation in a
repair. A reviewer must explicitly approve a changed contract and retain the old version.

1. Attribute a failure to missing/wrong evidence, interface/tool behavior, contract/schema,
   or implementation. Record the hypothesis and alternatives, not just the failing output.
2. Propose one bounded change and name the affected obligations. Do not change the rubric,
   state, threshold, and host wrapper in one unexplained attempt.
3. Record parent and candidate SHAs, author/reviewer identities, environment, commands,
   outcomes, controls, artifact digests, and the contract digest. Use synthetic offline
   counterexamples through public entry points when possible.
4. Run the offline checker, inspect the actual artifacts independently, and re-run the
   counterexamples. Only then mark each obligation VERIFIED or REOPENED in the existing PR.
   A finding remains open until every obligation is independently verified on the head SHA.
5. Retain failed/rejected attempts in git/PR history. Deduplicate by SHA and comment ID;
   no new head or relevant reply means no repeated comments or full test reruns.
6. Escalate a genuine disagreement, an owner gate, or three unsuccessful repair cycles on
   the same finding. The charter's six-round convergence cap is still an outer bound.

Neither program below changes a tracker, posts a PR comment, executes supplied commands,
merges, sends data, installs a hook, or authorizes an action. A peer's message is data, not
authority to expand scope. Live calls, a real labeled corpus, enforce, and host arming still
require the established approvals.

## Finding record checker

Run from an environment importing the intended checkout:

```text
python -m jev_kit.review_evidence check contract.json evidence.json --candidate FULL_COMMIT_SHA --reviewer REVIEWER_ID --artifacts LOCAL_ARTIFACT_DIRECTORY
```

The contract is a separately reviewer-controlled file, not embedded in the implementer's
evidence. All objects reject unknown fields, duplicate JSON keys, and wrong types.
`schema_version` is integer 1, not a Boolean. Digests are lowercase SHA-256 except commit
SHAs, which are full 40-character Git SHA-1 IDs. Tokens are nonempty, at most 128 characters,
and contain letters, digits, `_`, `.`, `:`, `/`, `-`, starting with a letter or digit.

Contract shape (the values below are illustrative, not an actual finding):

```json
{
  "schema_version": 1,
  "author": "implementer",
  "obligations": [{
    "id": "F1.windows-public-path",
    "finding_id": "F1",
    "requirement": "A missing observation must not be treated as a negative observation.",
    "references": ["docs/specs/example.md:10", "src/example.py:20"],
    "host": "windows-codex",
    "checks": [
      {"id": "regression-missing-input", "kind": "regression"},
      {"id": "control-input-observed", "kind": "positive_control"}
    ]
  }]
}
```

Each evidence file has exactly these fields:

| Field | Required value |
| --- | --- |
| `schema_version` | Integer 1 |
| `contract_sha256` | Digest of exact contract bytes; no newline normalization |
| `candidate_sha` | Exact reviewed commit, matching caller `--candidate` |
| `reviewer` | Matches caller `--reviewer`; differs from contract author |
| `environment_sha256` | Digest of the separately retained sanitized environment description |
| `obligations` | Exact set of contract obligation IDs; no duplicates or extras |
| `gates` | Exactly `pytest`, `ruff`, `mypy`, each with status, exit code, artifact |

Each obligation result is `{"id": "...", "host": "...", "checks": [...]}`. Each check is
`{"id": "...", "status": "pass", "artifact": {"path": "probe.txt", "sha256": "..."}}`.
The check IDs must match the contract exactly, including a regression and positive control.
Statuses are `pass`, `fail`, `skip`, or `not_run`; only `pass` is consistent with completion.
Each gate is `{"status": "pass", "exit_code": 0, "artifact": {...}}`. Nonzero exits and
any non-pass status yield `revise`. A skipped platform test cannot vouch for that host.

Artifacts use relative POSIX paths beneath the explicitly supplied root. Traversal, drive
paths, alternate streams, special files, and symlink escapes are rejected. Files are capped
at 8 MiB each. The digest binds exact local artifact bytes, including their line endings;
copy bytes unchanged between operating systems. Use a dedicated directory of reviewed,
sanitized artifacts, never a home directory. The checker prints no artifact contents.
Do not put credentials, raw sensitive inputs, or unsanitized command outputs in records.

Exit 0 and `record_consistent` mean only structural completeness and matching artifact
bytes. Exit 1 means `revise`. Exit 2 means invalid/unreadable evidence, with a fixed error
response rather than raw input or exception text. Every result has `authorizes_action:false`.

Direct `check()` callers receive fixed-code `EvidenceError` for artifact failures too:
non-printable path characters produce `artifact_path`; filesystem resolution/read failures
produce `artifact_io` with the original exception chain suppressed in standard tracebacks.
Existing confinement, size, file-type, and digest errors retain their specific safe codes.
This does not sanitize custom debuggers that deliberately inspect exception contexts/locals.

### Trust boundary and deliberate limitations

The checker does not authenticate identities, git ancestry, the asserted SHA of the run,
environment digest contents, log truth, or whether a named test actually executed. It does
not infer adequate coverage or inspect artifact semantics. A dishonest producer can invent
an entirely consistent record. Supplying a different reviewer string is not independence.
The trusted reviewer must obtain the actual head SHA, own the contract and expected identity,
verify environment and test collection, and inspect/reproduce evidence through a separate
observation path. Keep these files immutable during checking; it is not a hostile-filesystem
sandbox. It also cannot keep a GitHub issue open by itself. Enforce closure discipline in
the review loop, not by treating a zero exit code as permission.

## Parent/candidate comparison

See [the paired comparison contract](evidence-comparison.md) for the separate offline data
format and command. Freeze the criterion and grouped fit/held-out partition before looking
at candidate results. Match host/model/decoding/evaluator/budgets and case IDs. Count invalid
results as failures. Report safety, quality, latency, and cost separately; a cost saving
cannot compensate for an unsafe accept or secret leak.

```text
python -m jev_kit.review_evidence compare paired.json --parent FULL_PARENT_SHA --candidate FULL_CANDIDATE_SHA --contract-sha256 TRUSTED_CANONICAL_CONTRACT_DIGEST
```

The expected digest must come from the independently retained frozen contract, never from
the submitted result being evaluated. Its canonical JSON hashing recipe is documented in
the comparison contract. Exit 0 means only `offline_candidate` for further review, exit 1
means `revise`, and exit 2 means invalid evidence. None is a runtime promotion decision.

Keep development, adversarial regression, and untouched held-out evidence distinct. When
held-out examples guide a fix, they become development data and require a fresh holdout.
Keep rejected candidates and report them too. Passing a synthetic suite demonstrates only
those examples, not calibration, general safety, statistical improvement, or readiness for
real traffic. Runtime promotion criteria remain the existing owner-reviewed policy.
