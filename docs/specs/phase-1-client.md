# Spec: Phase 1 Jev client (the engine the skill calls)

- Tracker: Beads `jak-p9j.5` (labels `ready-for-agent`, `phase-1`, `spec`). This file is the
  canonical text; the Beads issue mirrors it.
- Revision: 2 (2026-09-25). Revision 1 was published to Beads only and was reviewed and
  refined into this version.
- Governing decisions: ADR 0001 (own client, no dfinke/Jev dependency), ADR 0002 (egress
  policy), ADR 0003 (Python, standard library only at runtime).
- Scope note: this is the client engine. The agent skill (`SKILL.md`, question sets,
  installers) is Phase 2 and calls this engine; host hooks are Phase 3.

## Problem Statement

The owner wants coding agents (Claude Code, Codex, Hermes) and ordinary automation to use
TypeSafe's Jev as a cheap evidence layer: rank, verify and gate. No existing client meets the
project's rules. The official Python SDK defaults to the moving `jev-latest` alias and leaves
validation, deadlines and failure semantics to the caller. The only PowerShell client
(dfinke/Jev) cannot tell mock answers from real ones, collapses all errors into one string,
ignores `Retry-After`, promotes bare labels without confidence, and has no state budget,
redaction or egress check. Every community client reviewed skips at least one of: pinning and
checking the served model, receipts, fail asymmetry, or measured thresholds
(`docs/research/02`, `03`, `09`).

Anything built on a weaker client (the skill, gates, calibration, mixture-of-agents) inherits
silent failure modes: a mock or an outage that looks like approval, a threshold reused after a
question or model changed, secrets leaving the machine, and decisions nobody can audit.

## Solution

A Python package with a small public surface:

- `decide`: takes one state packet, one question set and a mode, and returns one decision
  record per question. It never raises for runtime conditions; every outcome, including
  configuration problems, is a decision record with a route and a reason.
- `record_outcome`: lets the caller append whether it applied a decision, so receipts show
  what actually happened.
- A command-line entry point that wraps both, reading and writing JSON, for hooks, schedulers
  and CI.

Everything between input and output is enforced by code. The state passes an allowlist and
transforms, a token budget and Tier 1 detectors before anything leaves the machine. The request
pins a versioned model and refuses a mismatched served model. The response is validated per
question type. Each answer is routed by a threshold that belongs to that question's
fingerprint and carries its calibration status. Failures resolve per question by consequence
class: advisory questions fail open to "no advice", gate questions fail closed to "ask". A
receipt is written for every decision before the record is returned.

The network call is the only swappable dependency, so tests use a labeled mock transport and
never call the live API. A separate smoke script with a hard call cap exercises the live API
with synthetic states and records our own measurements.

From the user's point of view: one call gives typed, auditable evidence, and a mock, an outage
or an unmeasured threshold can never be mistaken for a real, calibrated answer.

## User Stories

### Calling the engine

1. As an automation author, I want one function that takes state, a question set and a mode, so that every Jev call goes through the same guardrails.
2. As an automation author, I want questions passed as a keyed set (id to question), so that answers come back under the ids I chose.
3. As an automation author, I want to mix Choice, Score and Noul questions in one call, so that all questions about one state cost one request.
4. As an automation author, I want to include speculative questions whose answers I may ignore, so that I avoid a second round trip.
5. As an automation author, I want each question tagged as advisory or gate, so that failures resolve by consequence.
6. As an automation author, I want question sets stored as versioned JSON files, so that they can be reviewed, diffed and fingerprinted like code.
7. As a scheduler, CI or hook author, I want a command-line entry point that reads a JSON request on standard input and writes JSON decision records on standard output, so that any host can call the engine without writing Python.
8. As a hook author, I want the command-line entry point to exit 0 whenever it produced decision records (including "ask" and "no advice"), and non-zero only for invalid invocation, so that a gate outcome is never confused with a crash.
9. As a hook or plugin author, I want the package to have no runtime dependencies, so that it installs into any host without a virtual environment and starts fast on every tool call.

### Failure semantics

10. As a gate author, I want any transport error, timeout, validation failure, egress block, configuration problem or unexpected exception to route gate questions to "ask", so that nothing can widen a permission.
11. As an advisory author, I want the same failures to route advisory questions to an explicit "no advice", so that my workflow continues without pretending it has evidence.
12. As a gate author, I want a failure that hits a request containing both gate and advisory questions to resolve each question by its own class, so that a shared failure cannot leak an allow.
13. As a gate author, I want a missing or invalid answer for any question to fail the whole set, so that partial responses are never acted on.
14. As a gate author, I want a receipt-write failure to route every gate question to "ask", so that no decision is acted on without a record.
15. As an automation author, I want `decide` never to raise for runtime conditions, so that callers and hooks handle one result shape.

### Model pinning

16. As an operator, I want requests to use a versioned model id (default `jev-1.13.0`) and aliases such as `jev-latest` and `jev-preview` refused as configuration errors, so that thresholds stay attached to the model they were measured on.
17. As an operator, I want the served model read from the response and compared with the requested id, so that a silent change is detected and treated as a failure.

### Response validation

18. As an operator, I want every Choice answer validated (the choice is one of the offered options, probability keys equal the offered set exactly, values are finite and in [0, 1] and sum to 1 within a tolerance, the choice is the argmax, confidence is in [0, 1]), so that invented or malformed answers are rejected.
19. As an operator, I want every Score answer validated (level probabilities cover exactly the offered levels, are finite, in [0, 1] and sum to 1 within a tolerance; the score is finite and within the level range; the legend matches the offered levels), so that malformed scores are rejected.
20. As an operator, I want every Noul answer validated as a finite number in [0, 1], so that malformed yes/no answers are rejected.
21. As an operator, I want booleans rejected wherever a number is expected, so that `false` can never read as 0.0, "no danger".
22. As an operator, I want Choice limited to 2 to 255 options and Score to 2 to 10 levels before sending, so that invalid questions fail locally.

### State, budget and egress (ADR 0002)

23. As a security-minded operator, I want every question set to declare an allowlist of state fields, each with a content kind and a required transform, and every other field dropped, so that egress is deny by default.
24. As an automation author, I want code and query text normalized (literals replaced by placeholder tokens, comments stripped) by a language profile, and code in a language without a profile blocked unless the question set declares it as free text under judgment, so that literals in code never leave by accident.
25. As an operator, I want internal identifiers (hosts, services, repositories, projects, customers, accounts, database objects) replaced by HMAC-SHA256 tokens under a local key, with a reviewed allowlist of public or system names passed unchanged, so that internal structure is not disclosed but stays linkable across calls.
26. As an operator, I want personal contact data (email addresses, phone numbers, postal addresses, IP addresses) masked or tokenized, so that it does not leave by default.
27. As an operator, I want error and log lines reduced to codes, levels and message templates with quoted values, identifiers and contact data masked, so that diagnostics can be judged without leaking context.
28. As an operator, I want shell and tool commands reduced to command and flag names, and file paths and URLs tokenized, so that arguments, environment values and user paths stay local.
29. As an automation author judging free text (tickets, messages, documents, web content), I want to declare the field as the content under judgment, with contact data masked by default and any opt-out justified in the question set, so that general-purpose triage works without silently sending personal data.
30. As an operator, I want agent transcript excerpts disabled unless a question set enables them with a size cap, so that transcripts never leave by accident.
31. As a security-minded operator, I want pattern-detectable Tier 1 classes (secrets and credentials, connection strings carrying credentials, Luhn-valid card numbers, national identifier formats) detected over the final serialized request, with any hit blocking the whole request, so that nothing slips past a transform bug.
32. As a security-minded operator, I want classes that patterns cannot reliably detect (health information, GDPR special categories, raw data rows) prevented by policy instead: question sets must declare that they do not carry them, raw rows are never an allowed content kind, and free text gets the default masking, so that the residual risk is explicit rather than hidden behind a detector that cannot see it.
33. As an operator, I want long lists in state required to be keyed objects, not positional arrays, so that the documented positional-index accuracy trap is avoided.
34. As an operator, I want the request's token count estimated conservatively against a configurable budget well under the documented limits (32k for state plus the longest question, 64k for state plus all questions), so that oversized requests are refused before egress.
35. As an operator, I want the estimate compared with the `usage.input_tokens` the API reports and both recorded, so that estimator error is visible.
36. As a domain pack author, I want to add an egress profile (detectors, transforms, allowlists) that can only tighten the core policy, so that domain support plugs in without weakening the defaults.
37. As a maintainer, I want detector and transform rules stored as versioned data with the source pattern set they were adapted from recorded, so that changes are reviewable and attributable.

### Secrets

38. As an operator, I want the API key read from `TYPESAFE_API_KEY` or from `TYPESAFE_API_KEY_COMMAND` (a command that prints the key from any secret store), so that keys are never in scripts or config files.
39. As an operator, I want the HMAC key supplied the same way under its own names and never sent, so that tokens cannot be reversed by the vendor.
40. As an operator, I want keys never written to receipts, logs, exception messages or command-line output, so that diagnostics cannot leak them.

### Errors, retries and deadlines

41. As an operator, I want errors typed as auth (401, 403), validation (422, 400), rate limit (429), overloaded (529), server (5xx), timeout and transport, with the raw error body and the `x-typesafe-request-id` header kept, so that callers and receipts can tell them apart and the offending field is visible.
42. As an operator, I want 429, 529 and 5xx retried with exponential backoff and jitter, honoring `Retry-After` and `retry-after-ms`, so that transient pressure is absorbed.
43. As a hook author, I want one total deadline per call that includes every retry and wait, defaulting low enough for every supported host (Hermes blocks the tool at its 30 second timeout; Codex continues on timeout), so that the engine always answers before the host gives up.

### Fingerprints and thresholds

44. As a calibration author, I want each question's fingerprint computed from its instructions, criteria, type, option or level set, model version, and the versions of the egress policy and profiles that transformed its state, so that any change that could shift the answer voids its threshold.
45. As a calibration author, I want a threshold registry keyed by fingerprint, with each entry's status (calibrated, uncalibrated, never auto-accept), the escalation target it was measured against, evidence reference and date, so that thresholds are never shared across questions or reused when the fallback changes.
46. As an automation author, I want threshold shapes defined per type: a Noul has a yes bound and a no bound with a review band between; a Choice has a minimum confidence and a minimum top-two margin; a Score has per-level bounds on the score, so that routing rules fit what each type returns.
47. As a gate author, I want "never auto-accept" entries for question kinds known to be confidently wrong (correctness of code or reasoning, comparisons biased by verbosity), so that those questions always route to "ask".
48. As a gate author, I want enforce mode to require a calibrated registry entry for every gate question, and to treat a missing or uncalibrated entry as a configuration failure that routes to "ask", so that enforcement never rests on an unmeasured number.
49. As a gate author, I want shadow mode to compute and record the would-be route without the caller treating it as a decision, so that behavior can be compared before promotion.

### Decision records, receipts and outcomes

50. As an automation author, I want each decision record to carry a decision id, the question id, the label or value, the full distribution, confidence (Choice, Score) or the noul value, the top-two margin (Choice), the threshold and its status, the route (`act`, `ask` or `no_advice`), mode, fail reason if any, and `is_mock`, so that I branch on evidence rather than a bare label.
51. As an auditor, I want one JSONL receipt line per decision with timestamp, decision id, request digest (of the request as sent), question-set id, version and fingerprint, requested and served model, request id, full distribution, threshold and status, route, mode, fail reason, egress rule ids and transform names (never matched text), estimated and reported tokens, and `is_mock`, so that every decision can be reconstructed without the receipt itself leaking.
52. As an auditor, I want receipts written before decision records are returned, so that no decision exists without a record.
53. As an auditor, I want receipts from one request to share the request digest and request id, so that decisions made together can be grouped.
54. As an auditor, I want fail reasons drawn from a closed vocabulary, so that receipts are queryable.
55. As an automation author, I want `record_outcome(decision_id, applied, note)` to append an outcome line to the receipts, so that receipts show whether each decision was actually applied.
56. As an operator, I want receipts written to a directory set by `JEV_KIT_RECEIPTS_DIR`, defaulting to the user's local application data directory, so that receipts never land in a repository by accident.
57. As a future skill and hook author, I want the question-set, decision-record, receipt and outcome formats documented with schema versions, so that later phases can depend on them.

### Mocks and tests

58. As a test author, I want a mock transport whose every answer carries `is_mock` true and model "mock", so that mock output can never be mistaken for a real answer.
59. As a hook author, I want mock-derived records routed to "ask" for gates in enforce mode unless an explicit test-only switch is set, so that a mock can never approve a real action.
60. As a test author, I want the mock able to script responses, errors, delays, malformed bodies, missing answers, booleans, invented options, bad sums and model mismatches, and to record every request, so that every failure path is testable through the public surface.
61. As a maintainer, I want the test suite to make no live API calls and to pass `ruff` and `mypy --strict` on Python 3.11+ on Windows, Linux and macOS, so that it is free, deterministic, portable and meets the repo conventions.

### Live verification, batching and spend

62. As an operator, I want a smoke script that makes live calls only with synthetic states, refuses to run without an explicit call cap, and writes dated results under `docs/`, so that live verification is bounded and recorded.
63. As an operator, I want the smoke script to verify the served model, error typing for a deliberate validation error, the Choice confidence formula, and the response shapes per type, so that assumptions taken from the docs are confirmed.
64. As an operator, I want the smoke script to measure latency as question count grows, answer stability batched versus single, and concurrent single calls versus one batched call, so that we replace vendor numbers with our own.
65. As an automation author, I want to run several states concurrently with a bounded worker pool and a shared per-process call cap, so that batch jobs stay under documented rate limits and a loop bug cannot generate unbounded spend.

## Implementation Decisions

- **Package shape (ADR 0003):** one Python 3.11+ package, standard library only at runtime.
  Public surface: `decide`, `record_outcome`, a batch helper, and a command-line entry point
  wrapping them. Everything else (egress, budget, validation, fingerprinting, registry lookup,
  request building, retries, served-model check, fail resolution, receipt writing) is private.
- **Tested seam (agreed with the owner):** the public surface is the tested surface; the
  transport is the only injectable dependency. No clock seam: retry and deadline behavior is
  tested with small caps and mock-scripted `Retry-After` values.
- **Transport contract:** receives the serialized request and a deadline; returns a raw
  response (status, headers, body) or a typed transport failure. The live transport posts to
  `https://api.typesafe.ai/v1/systemone` with bearer auth using `urllib.request`. The mock
  transport is scripted per test, records requests, and labels all output as mock.
- **Result, not exceptions:** `decide` converts every runtime condition into decision records
  with a fail reason. Only programmer errors in the call itself (wrong argument types) may raise.
- **Question-set file:** JSON, with id, version, questions (type, instructions, criteria,
  options or levels, consequence class), state field allowlist (field, content kind,
  transform, profile), declared excluded data classes, and optional transcript settings.
- **Model pinning:** versioned ids only; aliases are a configuration failure. Served model must
  equal the requested id.
- **Validation per type:** as in stories 18 to 22; tolerance for probability sums is a named
  constant recorded in receipts' schema documentation.
- **Fingerprint:** sha256 over a canonical JSON serialization of instructions, criteria, type,
  option or level set, model version, egress policy version and profile versions. Question ids
  and the question-set version are not part of it.
- **Threshold registry:** a versioned JSON file keyed by fingerprint. Entry: status, threshold
  shape per type (story 46), escalation target, evidence reference, date. Enforce mode requires
  calibrated entries for gates; "never auto-accept" always routes to "ask".
- **Routes:** `act` (with the label or value), `ask`, `no_advice`. Shadow mode records the
  would-be route in the same field and marks the record as shadow.
- **Egress (ADR 0002):** allowlist per question set, deny by default; transform per content
  kind; profiles may only tighten; Tier 1 pattern detectors over the final serialized request;
  policy-level prevention for classes patterns cannot detect; receipts carry rule ids only.
- **Budget:** conservative local estimate (the vendor tokenizer is not public); default budget
  well under 32k; both estimate and reported usage recorded.
- **Errors and retries:** as stories 41 to 43. Proposed defaults, to be confirmed by the smoke
  script: total deadline 10 seconds, per-attempt timeout 5 seconds, at most 2 retries.
- **Concurrency:** bounded `ThreadPoolExecutor`, default 4 workers, shared per-process call cap
  (default 100, configurable). Caps are per process, not global; documented as such.
- **Receipts:** JSONL, schema versioned, written and flushed before records are returned;
  outcome lines appended by `record_outcome`. Rotation and retention are out of scope.
- **Keys:** environment variable or key command (ADR 0003); never logged.
- **Smoke script:** separate from the package, synthetic states only, explicit call cap
  required.

## Testing Decisions

- Good tests exercise only external behavior: they call `decide`, `record_outcome` or the
  command-line entry point with the mock transport, and assert on returned records, receipts
  written and requests the mock recorded. They never call private functions.
- Everything (validation per type, budget, egress, fingerprinting, registry lookup, routes,
  retries, deadlines, served-model check, fail resolution, receipts, outcomes) is tested
  through that surface.
- Tools: `pytest`; `ruff` and `mypy --strict` in CI; matrix of Python 3.11 and the latest
  release on Windows, Linux and macOS.
- Egress fixtures: fake secrets, card numbers, national ids and credentialed connection strings
  must block; placeholders such as `Password=***` and `<password>` must pass; code with literals
  leaves only with placeholder tokens (tested with at least two language profiles); code in a
  language without a profile is blocked; receipts never contain matched text.
- Required patterns from prior art (`docs/research/09`): recording stub asserting exactly one
  request per state with the exact question set; "policy never absolves" (no failure path turns
  a gate into `act`); "bool never zero"; "invented choice rejected"; "partial answers fail the
  whole set"; "mock output is always labeled"; "mock never approves in enforce".
- Prior art in this repo: none yet. External: hermes-jev offline gate tests, jev-ultrafast
  response-validation tests, Gilbert09/jev-cli fail-asymmetry tests.

## Out of Scope

- The skill itself, core question sets, domain packs and installers (Phase 2).
- Claude Code, Codex and Hermes hook adapters (Phase 3), including how each host maps `ask`.
- Calibration corpus, fitting and reports (Phase 4); this spec only stores and enforces status.
- Pairwise order averaging for comparison questions (a Phase 2 question-set rule).
- Mixture-of-agents (Phase 5); `system-one-adapter-python` comparator and degraded mode (Phase 6).
- Receipt rotation and retention.
- Any dependency on dfinke/Jev or the official SDK (ADR 0001, ADR 0003).
- Jev-based context compaction (open decision `jak-ao8`).

## Further Notes

- Evidence: `docs/research/06` (verified API facts: 422 and 529, token limits, served-model
  field, host timeouts), `08` (parallel usage), `09` (source reads; arXiv:2609.29769 and
  2609.26550: an LLM fallback does not catch Jev's confident errors, which is why gates route
  to "ask" and never to another model).
- Residual risk accepted by ADR 0002: sensitive classes that patterns cannot detect are
  controlled by declaration and allowlisting, not detection.
- Prose conventions: no em dashes in docs, comments or commit messages.

## Changes from revision 1

- Validation is per question type; Score no longer uses argmax.
- Threshold shapes defined per type; routes named `act`, `ask`, `no_advice`.
- Aliases always refused (revision 1 allowed them without thresholds, which conflicted with the
  served-model check).
- Fingerprint now includes egress policy and profile versions (revision 1 contradicted itself).
- `decide` never raises; configuration problems become records.
- Added `record_outcome`, receipt-write failure handling, receipt location, mock-in-enforce
  rule, command-line contract and exit codes, question-set file format, default deadlines and
  concurrency.
- Egress: code without a language profile is blocked; classes patterns cannot detect are
  handled by declaration, not claimed detection.
- User stories renumbered 1 to 65 and grouped.
