# Spec: Phase 1 Jev client (the engine the skill calls)

- Tracker: Beads `jak-p9j.5` (labels `ready-for-agent`, `phase-1`, `spec`). This file is the
  canonical text; the Beads issue mirrors it.
- Revision: 4 (2026-09-25). Revision 2 was reviewed by Codex
  (`docs/reviews/2026-09-25-codex-spec-review.md`, R01 to R18); revision 3 was re-reviewed
  (`docs/reviews/2026-09-25-codex-spec-review-r3.md`: 12 resolved, 6 partially resolved, new
  R19 and R20). Revision 4 addresses the residual and new findings. Mapping at the end.
- Governing decisions: ADR 0001 (own client), ADR 0002 with Amendment 1 (egress policy),
  ADR 0003 (Python, standard library only at runtime), and the owner decisions listed under
  "Owner decisions for this revision".
- Scope: this is the client engine. The agent skill (`SKILL.md`, question sets, installers) is
  Phase 2; host hook adapters are Phase 3.

## Owner decisions for this revision (2026-09-25)

1. **Routes mean evidence, not permission.** The positive route is `accept`: the answer cleared
   its calibrated threshold. Whether any action proceeds is decided by code or the host from an
   explicit label-to-action map. The engine never authorizes actions.
2. **Free text only from named source types** (ADR 0002 Amendment 1). The local source allowlist
   ships empty, so free text is blocked until the owner names sources.
3. **The deadline is cooperative in Phase 1.** The engine promises a best-effort deadline and
   never accepts late results. Enforce mode inside real host hooks is not permitted until Phase 3
   adapters run the engine as a child process with a hard kill and a host-specific margin.
4. **Two narrow extra test suites** are allowed as documented exceptions to the one-seam rule:
   a live-adapter suite against a local fake HTTP server, and receipt-write fault injection.
5. **Decided by Claude, open to owner override:** no production language profiles in Phase 1
   (tests use synthetic fixture profiles); callers supply an `action_id` now, and consume-once
   and freshness enforcement belong to Phase 3 adapters.
6. **Postal addresses are deferred** (owner, revision 4). Phase 1 does not mask them. A field
   declared as a postal address has no transform and is therefore blocked; addresses inside free
   text from a named source type are accepted residual risk (ADR 0002 Amendment 1).
7. **Decided by Claude for revision 4, open to owner override:**
   - `accept` always requires a calibrated, target-matched registry entry, for advisory and gate
     questions alike. Uncalibrated advisory questions return `no_advice`; callers can still rank
     on the full distribution in the record.
   - In shadow mode, successful non-mock evaluations return `no_advice`; failures and mock gates
     keep `ask` (failure first, in every mode). Adapters use the `mode` field to keep shadow from
     changing host behavior.
   - A live call without an affirmative DPA attestation is allowed only when every sent field is
     of a non-personal Tier 3 kind; any kind that may carry personal data (including HMAC
     tokens, which stay personal data) requires `dpa: true`.

## Problem Statement

The owner wants coding agents (Claude Code, Codex, Hermes) and ordinary automation to use
TypeSafe's Jev as a cheap evidence layer: rank, verify and gate. No existing client meets the
project's rules. The official Python SDK defaults to the moving `jev-latest` alias and leaves
validation, deadlines and failure semantics to the caller. The only PowerShell client
(dfinke/Jev) cannot tell mock answers from real ones and has no budget, redaction or egress
check. Every community client reviewed skips at least one of: pinning and checking the served
model, receipts, fail asymmetry, or measured thresholds (`docs/research/02`, `03`, `09`).

Anything built on a weaker client inherits silent failure modes: a mock or an outage that looks
like approval, a threshold reused after the question or its input changed, secrets or the API
key leaving the machine, and decisions nobody can audit.

## Solution

A Python package with a small public surface:

- `decide`: one state packet, one question set and a mode in; one decision record per question
  out (or a safe error envelope when the questions cannot be identified). It never raises for
  runtime conditions.
- `decide_batch`: several `decide` calls through a bounded worker pool with shared budgets.
- `record_outcome`: the caller appends what it did with a decision.
- A command-line entry point wrapping all three with JSON in and out.

Before anything leaves the machine, every dynamic value passes the question set's schema and
transforms, the request passes a token and size budget and the Tier 1 detectors, and the exact
checked bytes are what the transport sends. The live transport uses a fixed HTTPS endpoint,
refuses redirects and never forwards the key. Responses are validated per question type.
Answers are routed by threshold tables bound to a fingerprint of everything that could change
the answer. Failures resolve per question: advisory to `no_advice`, gate to `ask`. Every
decision gets a receipt; `accept` is returned only after its receipt is committed. Mock
evidence can never produce an effective `accept` for a gate.

## User Stories

### Calling the engine

1. As an automation author, I want one function that takes state, a question set and a mode, so that every Jev call goes through the same guardrails.
2. As an automation author, I want questions passed as a keyed set (id to question), so that answers come back under the ids I chose.
3. As an automation author, I want to mix Choice, Score and Noul questions in one call, so that all questions about one state cost one request.
4. As an automation author, I want to include speculative questions whose answers I may ignore, so that I avoid a second round trip.
5. As an automation author, I want each question tagged as advisory or gate, so that failures resolve by consequence.
6. As an automation author, I want question sets stored as versioned JSON files whose question text is fixed and reviewed, so that they can be diffed and fingerprinted like code and never carry per-call data.
7. As an automation author, I want the mode to default to shadow, so that nothing enforces by accident.
8. As an automation author, I want `decide_batch` to return results in input order, so that batch output lines up with batch input.
9. As a scheduler, CI or hook author, I want a command-line entry point that reads one JSON request on standard input and writes one JSON response on standard output, so that any host can call the engine without writing Python.
10. As a hook author, I want the command line to exit 0 whenever it wrote a valid response (decision records or a safe error envelope) and 2 only when the invocation itself is unusable (unreadable input, unknown arguments), so that a gate outcome is never confused with a crash.
11. As a hook or plugin author, I want the package to have no runtime dependencies, so that it installs into any host without a virtual environment and starts fast.

### Routes and routing

12. As an automation author, I want each record's effective `route` to be one of `accept`, `ask` or `no_advice`, where `accept` means only that the evidence cleared a calibrated, target-matched threshold (for advisory and gate questions alike), so that permission to act always stays with my code or the host.
13. As an automation author, I want each record to carry a separate `would_route` showing the candidate route before shadow, mock or failure rules applied, and `null` with an explicit reason when no evaluable threshold exists, so that I can observe what the engine would have done without it ever inventing a threshold.
14. As a gate author, I want shadow mode to set the effective route of every successful non-mock evaluation to `no_advice` (candidate in `would_route`), while failures and mock gates keep `ask`, and adapters use the `mode` field to suppress shadow effects, so that failure handling is identical in every mode and shadow never changes host behavior.
15. As an automation author, I want Noul routing defined by a `yes_bound` and a `no_bound` (with `no_bound` < `yes_bound`): `accept` with label `yes` when the value is at or above `yes_bound`, `accept` with label `no` when at or below `no_bound`, otherwise the review band, so that yes/no evidence has explicit boundaries.
16. As an automation author, I want Choice routing defined by a minimum confidence and a minimum top-two probability margin, with a tie (top two equal within tolerance) always in the review band, so that ambiguous choices are never accepted.
17. As an automation author, I want Score routing defined by labeled intervals over the level range, inclusive lower bound and exclusive upper bound (the top interval inclusive at the maximum), validated as non-overlapping, with any gap being the review band, plus a minimum confidence, so that score evidence maps to explicit labels.
18. As a gate author, I want the review band to route gates to `ask` and advisory questions to `no_advice`, so that uncertainty never becomes acceptance.
19. As a gate author, I want "never auto-accept" registry entries for question kinds known to be confidently wrong (correctness of code or reasoning, comparisons biased by verbosity), so that those questions never route to `accept`.
20. As a gate author, I want enforce mode to require a calibrated registry entry for every gate question, and a missing or uncalibrated entry to be a configuration failure, while an advisory question without a calibrated entry simply returns `no_advice` with reason `uncalibrated`, so that neither class can reach `accept` on an unmeasured number.
21. As a calibration author, I want each question set to declare its escalation target and the registry entry to name the target it was measured against, with a mismatch treated as a configuration failure, so that a threshold is not reused when the fallback changes.

### Failure semantics

22. As a gate author, I want any transport error, timeout, validation failure, egress block, configuration problem, budget exhaustion, receipt failure or unexpected exception to route gate questions to `ask`, so that nothing can produce acceptance by failing.
23. As an advisory author, I want the same failures to route advisory questions to `no_advice`, so that my workflow continues without pretending it has evidence.
24. As a gate author, I want a failure that hits a request containing both gate and advisory questions to resolve each question by its own class, so that a shared failure cannot leak acceptance.
25. As a gate author, I want a missing, extra or invalid answer for any question to fail the whole set, so that partial responses are never acted on.
26. As a hook author, I want a safe error envelope (schema version, `status: error`, closed-vocabulary reason, no records) when the question set is unreadable or its questions cannot be identified, and hosts told to treat it as `ask`, so that an empty result is never mistaken for approval.
27. As an automation author, I want `decide` never to raise for runtime conditions, and only for programmer errors in the call itself (wrong argument types), so that callers handle one result shape.

### Model pinning and mocks

28. As an operator, I want requests to use a versioned model id (default `jev-1.13.0`) and aliases such as `jev-latest` and `jev-preview` refused as configuration failures, so that thresholds stay attached to the model they were measured on.
29. As an operator, I want the served model read from the response and compared with the requested id, with a mismatch failing the whole set, so that a silent model change is caught.
30. As a test author, I want mock provenance carried out of band by the transport object, never by response JSON, so that a scripted body cannot claim to be real.
31. As a test author, I want mock responses to carry a simulated served model that goes through the same pin check (recorded as `simulated_model`), so that mismatch logic is testable without weakening pinning.
32. As a gate author, I want every mock-derived record to have `is_mock=true`, `model="mock"` and an effective route of `ask` for gates, with no configuration able to override that, so that a mock can never approve a real action; the candidate route stays visible in `would_route` for tests.

### Response validation

33. As an operator, I want each answer's `type` to match its question's type and the set of answer ids to equal the set of question ids exactly, so that mismatched or extra answers are rejected.
34. As an operator, I want every Choice answer validated (the choice is an offered option, probability keys equal the offered option keys exactly, values are finite, non-boolean, in [0, 1] and sum to 1 within a named tolerance, the choice is the argmax, confidence is finite, non-boolean and in [0, 1]), so that invented or malformed answers are rejected.
35. As an operator, I want every Score answer validated (probability and legend keys are exactly the strings `"0"` to `"n-1"`; `legend[str(i)]` equals the requested criterion string at index i exactly; probabilities are finite, non-boolean, in [0, 1] and sum to 1 within tolerance; the score is a finite, non-boolean number in [0, n-1] equal to the probability-weighted mean of level indices within a named tolerance; confidence is a present, finite, non-boolean number in [0, 1]), so that a score contradicting its distribution, a boolean or a swapped legend is rejected.
35a. As an operator, I want booleans rejected wherever any number is expected, in answers, registry bounds and configuration alike, so that `true` can never pass as 1 or `false` as 0.
36. As an operator, I want every Noul answer validated as a finite, non-boolean number in [0, 1], so that malformed yes/no answers are rejected.
37. As an operator, I want every non-2xx status, including redirects, 404 and 408, treated as a typed failure and never validated as an answer, so that error bodies can never be read as evidence.
38. As an operator, I want Choice limited to 2 to 255 options (the minimum is our rule; the API documents only the maximum) and Score to 2 to 10 levels with string descriptions only in Phase 1, so that invalid or ambiguous questions fail locally.

### Egress: request contents (ADR 0002 with Amendment 1)

39. As a security-minded operator, I want question text, criteria, option keys and question ids to come only from the reviewed question-set file and to match a safe-identifier pattern where they are identifiers, with all per-call data confined to state, so that no dynamic data bypasses the state transforms.
40. As a security-minded operator, I want each question set to declare a state schema (field paths, content kind, transform and parameters, source type for free text), with undeclared fields and undeclared nested descendants dropped, so that allowing a parent never admits arbitrary children.
41. As an automation author, I want code and query text normalized by a language profile (literals to placeholder tokens, comments stripped), and code in a language without a profile blocked unless its field is declared free text from a named source type, so that literals in code never leave by accident.
42. As an operator, I want internal identifiers replaced by HMAC-SHA256 tokens under a local key, with a reviewed allowlist of public or system names passed unchanged, so that internal structure stays private but linkable.
43. As an operator, I want personal contact data (email addresses, phone numbers, IP addresses) masked or tokenized, and a field declared as a postal address blocked because Phase 1 has no transform for it (addresses inside named-source free text are accepted residual risk, owner decision 6), so that contact data does not leave by default and the address gap is explicit.
44. As an operator, I want error and log lines reduced to codes, levels and message templates with quoted values, identifiers and contact data masked, so that diagnostics can be judged without leaking context.
45. As an operator, I want shell and tool commands reduced to command and flag names, and file paths and URLs tokenized, so that arguments, environment values and user paths stay local.
46. As an automation author judging free text, I want the field to declare a `source_type` that must appear in my local source allowlist (which ships empty), with contact data masked by default, so that free text leaves only from sources I have explicitly accepted.
47. As an operator, I want agent transcript excerpts disabled unless a question set enables them with a size cap, and then treated as free text from a named source type, so that transcripts never leave by accident.
48. As a security-minded operator, I want every transform to document the input types and syntax it supports, and an unknown transform, an unsupported input or a transform error to block the request, so that a failed transform never becomes passthrough.
49. As a security-minded operator, I want the Tier 1 pattern detectors (secrets and credentials including escaped multiline keys, credentialed connection strings, Luhn-valid card numbers, card track data, US SSN format) run over the exact serialized body and over every decoded key and string value, with any hit or detector error blocking the whole request, so that nothing slips past a transform bug or JSON escaping.
50. As a security-minded operator, I want the exact bytes that passed detection to be the bytes the transport sends, never reserialized afterwards, so that the check and the egress cannot diverge.
51. As a security-minded operator, I want input size, nesting depth, list length and detector work bounded, so that a large or adversarial input cannot stall the engine or defeat a detector.
52. As an operator, I want the live transport to refuse to send unless the local attestation file records, with dates, that TypeSafe is in my data-flow inventory, and to require `dpa: true` whenever any sent field is of a kind that may carry personal data (identifiers including HMAC tokens, contact data, logs, commands, paths, code, free text, transcripts), allowing `dpa: false` only when every sent field is a non-personal Tier 3 kind, so that ADR 0002's live-use preconditions are enforced per call.
53. As a domain pack author, I want to add an egress profile that can only tighten the core policy, so that domain support plugs in without weakening defaults.
54. As a maintainer, I want detector and transform rules stored as versioned data with the source pattern set they were adapted from recorded, so that changes are reviewable and attributable.

### Budget

55. As an operator, I want the request's token count estimated with a versioned heuristic estimator, checked separately against a whole-request budget and a state-plus-longest-question budget (defaults well under the documented 64k and 32k), with the estimate described as a policy limit rather than a guaranteed bound on vendor tokens, and a vendor rejection for size mapped to a typed failure, so that oversized requests are unlikely to leave and never pass silently.
56. As an operator, I want estimated and reported (`usage.input_tokens`) token counts both recorded, so that estimator error is visible and the estimate is never presented as exact.

### Transport and key safety

57. As a security-minded operator, I want the live transport to use a private opener with verified TLS, the fixed HTTPS endpoint, `Content-Type: application/json`, environment proxies ignored unless a proxy is explicitly configured, HTTP debug output off, and every redirect refused as a transport failure, so that the request and key cannot be diverted.
58. As a security-minded operator, I want the API key attached only as a non-redirected authorization header, and never included in the body, digests, recorded mock requests, receipts, logs, exception text or command-line output, so that the key has exactly one destination.
59. As an operator, I want the API key from `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND`, and the HMAC key from `JEV_KIT_HMAC_KEY` or `JEV_KIT_HMAC_KEY_COMMAND` (base64url for exactly 32 bytes), with both forms of the same key set at once being a configuration failure, so that key sources are unambiguous.
60. As an operator, I want key commands given as a JSON argument array executed without a shell, with runtime bounded by the remaining deadline, output size bounded, stderr discarded, and non-zero exit, empty or invalid output treated as failure with neither output nor command text in any error, so that key retrieval is portable and cannot leak.
61. As an operator, I want the API key resolved only for the live transport, the HMAC key resolved whenever a selected transform needs it (including mock-backed calls, where tests supply an explicit synthetic 32-byte key through the same configuration interface), and neither ever replaced by a default, so that transforms behave identically under every transport and missing keys fail visibly.

### Errors, retries, deadlines and rate budgets

62. As an operator, I want errors typed as auth (401, 403), validation (422; 400 as SDK-compatible), rate limit (429), overloaded (529), not found (404), timeout (408 and local), server (5xx), redirect refused and transport, so that callers and receipts can tell them apart.
63. As an operator, I want raw error bodies kept privately, bounded in size, and excluded from string forms, exception chains, JSON, receipts and command-line output, with public diagnostics limited to typed fields, so that error echoes of submitted data cannot leak.
64. As an operator, I want 429, 529 and 5xx retried with exponential backoff and jitter, `retry-after-ms` taking precedence over `Retry-After` (seconds or HTTP date), malformed values ignored in favor of backoff, and no retry when the required delay does not fit the remaining deadline, so that retries are bounded and honest.
65. As a hook author, I want one monotonic deadline started at entry covering preparation, key retrieval, rate waits, retries, reads and receipt writing, with time reserved for the receipt and any result arriving after expiry discarded, so that the engine answers within its budget whenever the operating system lets it.
66. As a hook author, I want the documentation to state plainly that Phase 1's deadline is cooperative and cannot interrupt a blocked system call, and that enforce mode inside host hooks waits for the Phase 3 child-process watchdog, so that nobody relies on a guarantee the engine does not give.
67. As an operator, I want every outbound attempt, including retries and smoke probes, to reserve a permit atomically from a shared per-process call cap and a local requests-per-minute and tokens-per-second budget, with exhaustion producing class-appropriate failure records, so that loops and retries cannot run away.
68. As an operator, I want it documented that caps and rate budgets are per process and local, not account-wide, so that nobody assumes they prevent every 429.

### Fingerprints and registry

69. As a calibration author, I want each question's fingerprint computed over the complete effective contract: its instructions, criteria, type, option or level set in order, the pinned model, and the full effective egress contract for its state (field paths, kinds, transforms and their parameters, allowlists, opt-outs and justifications, source types, transcript settings, and the content hashes of the policy and profile rule files), so that any change that could shift the answer voids its threshold.
70. As a calibration author, I want a separate question-set digest over the whole file, recorded alongside each question fingerprint, so that receipts identify both the question and the set it came from.
71. As a calibration author, I want canonical serialization defined (UTF-8, sorted object keys, preserved array order, finite numbers only, duplicate keys rejected), with question ids, per-call state and key material excluded, so that fingerprints are reproducible across implementations.
72. As a calibration author, I want the registry selected by an explicit path or `JEV_KIT_REGISTRY`, a missing registry meaning every question is uncalibrated, and registry entries validated (per-type threshold shape, ordered and non-overlapping bounds, status, escalation target, evidence reference, date), so that a malformed registry is a configuration failure, not a silent default.

### Decision records, receipts and outcomes

73. As an automation author, I want each decision record to carry a decision id, question id, label or value, full distribution, confidence (Choice, Score) or noul value, top-two margin (Choice), threshold and status, `route`, `would_route`, mode, fail reason if any, `is_mock`, and `receipt_written`, so that I branch on evidence rather than a bare label.
74. As an auditor, I want one JSONL receipt line per decision with: timestamp, local call id, decision id, question fingerprint, question-set digest and version, requested model, served or simulated model, attempt ids and server request ids (null when nothing was sent), digest of the bytes sent (null when nothing was sent), full distribution, selected label or value, threshold and status, `would_route` and `route`, mode, fail reason, egress rule ids with transform names and counts (never matched text), estimated and reported tokens, `is_mock`, and the caller's `action_id` when given, so that every decision can be reconstructed without the receipt leaking.
75. As an auditor, I want all receipt lines for one call written in a single append followed by a commit marker line, flushed and synced to disk, before any record with route `accept` is returned, so that no accepted decision exists without a durable record and partial writes are detectable.
76. As a gate author, I want a receipt failure to return safe records (`ask` or `no_advice`) marked `receipt_written=false`, with any best-effort error logging unable to recurse, so that an unrecorded decision is never accepted.
77. As an auditor, I want receipts written to one file per process (named by start time and process id) with a lock around appends within the process, so that concurrent workers and separate hook processes never interleave lines.
78. As an operator, I want receipts written to `JEV_KIT_RECEIPTS_DIR` (must be absolute) or the platform default (a relative `LOCALAPPDATA` or `XDG_STATE_HOME` is ignored in favor of the home-based default) (Windows `%LOCALAPPDATA%\jev-agent-kit\receipts`, macOS `~/Library/Application Support/jev-agent-kit/receipts`, Linux `$XDG_STATE_HOME/jev-agent-kit/receipts` or `~/.local/state/jev-agent-kit/receipts`), with a lookup failure or a location that is not absolute (for example a relative or empty home) treated as a receipt failure, so that receipts never land in a repository by accident. (A pre-rename `jev_agent_kit` folder keeps being used while no `jev-agent-kit` folder exists.)
79. As an auditor, I want failed or pre-egress receipts never to copy state values or rejected content, so that the audit trail of a blocked request cannot leak what was blocked.
79a. As an auditor, I want every string persisted in receipts or returned in records to follow a receipt-safe metadata schema: `action_id` is an opaque identifier (for example from a `new_action_id()` helper) matching a bounded safe pattern and rejected without echo otherwise; server request ids are validated for pattern and length and omitted with a closed diagnostic reason when unsafe; registry labels and evidence references are validated when the registry loads; and the Tier 1 detectors also run over this metadata, independently of request egress, so that no side channel carries secrets or user text into the audit trail.
80. As an automation author, I want `record_outcome(decision_id, outcome_code, action_id)` to append an outcome line using a closed vocabulary of outcome codes (for example `applied`, `not_applied`, `overridden_by_host`, `overridden_by_human`), returning a result rather than raising for an unknown decision id, and allowing repeated outcomes as separate lines, so that receipts show what actually happened without free-text leakage.
81. As an auditor, I want fail reasons and outcome codes drawn from closed vocabularies, so that receipts are queryable.
82. As a future skill and hook author, I want JSON Schemas and worked examples for the question set, registry, attestation file, command-line request and response, decision record, error envelope, receipt and outcome, each with a schema version, so that later phases can depend on them.

### Tests and verification

83. As a test author, I want the mock able to script responses, statuses, headers, delays, malformed bodies, missing or extra answers, wrong types, booleans, invented options, bad sums, inconsistent scores and simulated model mismatches, and to record the exact bytes of every request, so that every failure path is testable through the public surface.
84. As a maintainer, I want the main test suite to make no network calls, and to pass `ruff` and `mypy --strict` on Python 3.11 and the latest release on Windows, Linux and macOS, so that it is free, portable and meets the repo conventions.
85. As a maintainer, I want a live-adapter suite that runs the real transport against a local fake HTTP server (never the real API), covering redirect refusal, authorization handling, TLS configuration, status mapping, error-body privacy and header parsing, so that the part the mock replaces is still verified.
86. As a maintainer, I want receipt-write fault injection covering failure on open, after the first line and on flush, so that the receipt guarantees are tested, not assumed.
87. As a maintainer, I want command-line tests that run the entry point as a subprocess with a mock-backed offline configuration, so that the JSON contract and exit codes are verified end to end.
88. As a maintainer, I want deadline tests to use generous margins and to claim only cooperative behavior, so that tests do not pretend to prove hard cancellation.

### Live smoke and batching

89. As an operator, I want a smoke script that makes live calls only with synthetic states, refuses to run without an explicit call cap and a valid attestation, and writes dated results under `docs/`, so that live verification is bounded, lawful and recorded.
90. As an operator, I want the smoke script to verify the served model and response shapes per type, record Choice confidence alongside probabilities (without asserting the documentation's approximate formula), and probe error typing through a smoke-only transport path that does not bypass production validation, so that documented assumptions are checked honestly.
91. As an operator, I want the smoke script to measure latency as question count grows, stability batched versus single, and concurrent single calls versus one batched call, so that we replace vendor numbers with our own.

## Implementation Decisions

- **Public surface:** `decide`, `decide_batch`, `record_outcome`, the command-line entry point,
  the transport interface, and the published JSON Schemas. Everything else is private.
- **Tested seam (agreed with the owner):** the public surface with an injectable transport is
  the main tested surface; no clock seam. Documented exceptions: the live-adapter suite (local
  fake server) and receipt-write fault injection.
- **Question-set file:** JSON, schema-versioned. Each question holds the API fields exactly as
  sent (`type`, `instructions`, `criteria`) plus a separate `kit` object (consequence class,
  registry hints) that is never sent. Question-level text is static; state carries all per-call
  data. The file also declares the state schema, excluded data classes, escalation target and
  transcript settings.
- **Strict JSON everywhere:** duplicate keys rejected, non-finite numbers rejected on read and
  write, unknown schema versions, modes, types and fields rejected before egress.
- **Transport contract:** receives the checked request bytes and the remaining deadline; returns
  a raw response or a typed failure. Mock provenance is a property of the transport object. The
  live transport is a private `urllib` opener with no redirect handler (3xx is a failure), no
  environment proxies unless configured, verified default TLS context, the fixed endpoint, and
  the key as a non-redirected header.
- **Routes:** effective `route` in {`accept`, `ask`, `no_advice`}; candidate `would_route`
  (or `null` with a reason). Precedence: failure, then mock gate rule, then shadow, then
  calibration status and "never auto-accept", then thresholds. Resulting matrix:

  | Situation | Gate, shadow | Gate, enforce | Advisory, shadow | Advisory, enforce |
  | --- | --- | --- | --- | --- |
  | Any failure | `ask` | `ask` | `no_advice` | `no_advice` |
  | Mock-derived | `ask` | `ask` | `no_advice` | `no_advice` |
  | No registry entry or uncalibrated | `no_advice` | `ask` (configuration failure) | `no_advice` | `no_advice` |
  | Never auto-accept | `no_advice` | `ask` | `no_advice` | `no_advice` |
  | Calibrated, target mismatch | `ask` (configuration failure) | `ask` (configuration failure) | `no_advice` | `no_advice` |
  | Calibrated, review band | `no_advice` | `ask` | `no_advice` | `no_advice` |
  | Calibrated, clears threshold | `no_advice` | `accept` | `no_advice` | `accept` |

  `would_route` shows the candidate from the last two rows whenever bounds exist.
- **Routing tables:** per type as stories 15 to 18. Bounds validated when the registry loads.
- **Validation:** per type as stories 33 to 38; tolerances are named constants documented with
  the schemas.
- **Fingerprints:** stories 69 to 71. Question fingerprint and question-set digest are both
  SHA-256 over canonical JSON.
- **Egress:** stories 39 to 54. Detectors run on the checked bytes and on decoded keys and
  strings. National identifiers in Phase 1: US SSN only; others arrive with profiles. No
  production language profiles in Phase 1; tests use at least two synthetic fixture profiles.
- **Budget defaults (proposed; the smoke script characterizes but cannot prove them):** estimator
  v1 is an uncalibrated heuristic, UTF-8 bytes divided by 3 rounded up; whole-request budget
  16,000 estimated tokens; state plus longest question budget 12,000 estimated tokens; maximum body 48 KiB; maximum
  depth 16; keyed lists up to 200 entries; positional arrays longer than 20 rejected.
- **Deadline defaults (proposed):** total 10 seconds, per-attempt timeout 5 seconds, at most 2
  retries, 500 milliseconds reserved for receipt writing. Cooperative only (owner decision 3).
- **Concurrency and rate defaults (proposed):** 4 workers; per-process cap 100 attempts;
  local budget 600 requests per minute and 100,000 tokens per second (both below the documented
  account limits of 1,200 and 250,000).
- **Receipts:** stories 73 to 81. One file per process, single append per call plus commit
  marker, flush and fsync before returning `accept`.
- **Secrets:** stories 57 to 61. Receipt-safe metadata: story 79a.
- **Attestation file:** local JSON at a platform config path or `JEV_KIT_ATTESTATION`, recording
  the inventory date and DPA status; required by the live transport only.

## Testing Decisions

- Good tests exercise only external behavior through the public surface: returned records and
  envelopes, receipts written, and the exact request bytes the mock recorded. They never call
  private functions.
- Main suite: `pytest` with the scripted mock, no network, no clock seam, generous timing
  margins. `ruff` and `mypy --strict` in CI on Python 3.11 and latest, on Windows, Linux and
  macOS.
- Documented exception suites: live adapter against a local fake HTTP server; receipt-write
  fault injection.
- Command line tested as a subprocess with an offline mock-backed configuration.
- Required fixtures and properties:
  - "policy never absolves": no failure, mock or shadow path yields an effective `accept` for a
    gate;
  - "bool never zero"; "invented choice rejected"; "partial or extra answers fail the whole
    set"; "score contradicting its distribution rejected";
  - "mock output is always labeled" and "mock never accepts a gate";
  - recording mock asserts exactly one request per call in the successful no-retry case (retry
    cases scripted separately) with the exact question set, that the API key never appears in
    recorded bytes, and that the API key command never runs for mock calls;
  - boolean Score score and confidence, boolean registry bounds, and swapped or altered legend
    values are rejected;
  - the routing matrix, row by row, including `would_route: null` without bounds;
  - HMAC output is identical across transports for the same explicit synthetic key, and missing
    HMAC material yields safe failure records;
  - a `dpa: false` attestation allows a Tier 3-only contract and blocks contracts with
    identifiers, HMAC tokens or named-source free text (the local fake server records that
    nothing was sent);
  - synthetic key and command sentinels in `action_id` and in response request-id headers never
    reach receipts or records;
  - a field declared as a postal address is blocked;
  - egress: synthetic secrets (including escaped multiline private keys), credentialed
    connection strings, Luhn-valid card numbers, track data and SSNs block; placeholders such as
    `Password=***` pass; personal data placed in question instructions is rejected by the static
    question rule; a sensitive keyed-list id is tokenized or rejected; code without a profile is
    blocked unless declared free text from a named source; free text from an unnamed source is
    blocked;
  - fingerprint changes when any effective egress setting changes, and not when only question
    ids change;
  - receipts never contain matched text, state values or key material.
- Prior art: hermes-jev offline gate tests, jev-ultrafast response validation, Gilbert09/jev-cli
  fail-asymmetry tests (`docs/research/09`).

## Out of Scope

- The skill itself, core question sets, domain packs, production language profiles, installers
  (Phase 2).
- Host hook adapters, including the child-process watchdog, consume-once and freshness
  enforcement, and each host's mapping of `ask` (Phase 3). Enforce mode inside host hooks is
  not permitted before Phase 3.
- Calibration corpus, fitting and reports (Phase 4); this spec stores and enforces status only.
- Pairwise order averaging (a Phase 2 question-set rule).
- Mixture-of-agents (Phase 5); `system-one-adapter-python` comparator and degraded mode
  (Phase 6).
- Receipt rotation and retention.
- Any dependency on dfinke/Jev or the official SDK (ADRs 0001, 0003).
- Jev-based context compaction (open decision `jak-ao8`).

## Further Notes

- Evidence: `docs/research/06` (verified API facts), `08` (parallel usage), `09` (source reads,
  arXiv:2609.29769 and 2609.26550), and the Codex review. The redirect and strict-JSON findings
  were reproduced locally on Python 3.13: a 302 carries a normally added Authorization header
  to a different host over plain HTTP; `json.loads` keeps the last duplicate key; `json.dumps`
  emits `NaN`.
- Residual risk accepted by the owner in ADR 0002 Amendment 1: named free-text sources may
  contain sensitive classes no detector recognizes.
- Prose conventions: no em dashes in docs, comments or commit messages.

## Resolution of review findings (revision 3)

| Finding | Resolution (stories) |
| --- | --- |
| R01 mock approval and pinning conflict | No override switch; out-of-band provenance; simulated model; `would_route` (30 to 32) |
| R02 fingerprint misses egress config | Full effective contract, set digest, canonical rules (69 to 71) |
| R03 metadata bypasses transforms | Static reviewed question text, safe ids, schema with nested rules, checked bytes sent (39, 40, 50) |
| R04 redirect forwards key | Private opener, redirects refused, non-redirected header, no env proxies (57, 58) |
| R05 deadline not enforceable | Monotonic cooperative deadline; honest limit; enforce waits for Phase 3 (65, 66) |
| R06 score validation hole | Type match, string level keys, weighted-mean check, confidence required (33 to 35) |
| R07 routing underspecified | Routing tables, shadow semantics, evidence not permission, escalation binding (12 to 21) |
| R08 diagnostic leaks | Private bounded error bodies, closed outcome codes, no copied content (63, 79, 80) |
| R09 declaration not ADR-approved | ADR 0002 Amendment 1; named sources; class accounting; attestation (46, 49, 52) |
| R10 serialization and transform failures | Documented inputs, fail-closed transforms, decoded scanning, bounds (48 to 51) |
| R11 receipt contract | Commit marker, fsync before `accept`, per-process files, null unsent fields (74 to 79) |
| R12 seam cannot verify everything | Two exception suites, subprocess CLI tests, honest timing tests (85 to 88) |
| R13 caps are not rate budgets | Atomic per-attempt permits, local rate budgets, header precedence (64, 67, 68) |
| R14 schemas and malformed config | Strict JSON, error envelope, exit codes, defaults, registry selection, schemas (7 to 10, 26, 72, 82) |
| R15 key provider protocol | Named variables, 32-byte HMAC key, argv without shell, bounded, precedence (59 to 61) |
| R16 Choice minimum and formula | Wording fixed; smoke records rather than asserts (38, 90) |
| R17 profile test contradiction | Fixture profiles; free-text exception tested (41, Testing Decisions) |
| R18 paths and budgets vague | Platform paths, absolute override, estimator and defaults (55, 78, Implementation Decisions) |

## Resolution of follow-up findings (revision 4)

| Finding | Resolution |
| --- | --- |
| R06 residual: booleans, legend values | Stories 35 and 35a; fixtures in Testing Decisions |
| R07 residual: uncalibrated and missing thresholds | Stories 12, 13, 20; routing matrix |
| R08 residual: metadata side channels | Story 79a |
| R09 residual: DPA enforcement | Story 52; owner decision 7 |
| R15 residual: HMAC under mocks | Story 61 |
| R18 residual: estimator wording, rates, XDG | Stories 55 and 78; budget and rate defaults |
| R19 new: postal addresses | Owner decision 6; story 43; ADR 0002 Amendment 1 |
| R20 new: shadow precedence | Story 14; routing matrix |
