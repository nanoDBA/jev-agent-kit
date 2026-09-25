# ADR 0001: Own Jev client; dfinke/Jev is a reference, not a dependency

- Status: accepted
- Date: 2026-09-25
- Decided by: owner (answers open question 1 in `docs/plan.md`)

## Context

dfinke/Jev is the only PowerShell-native Jev client (`docs/research/03-dfinke-jev-review.md`).
The review found eight defects that conflict with our non-negotiables: unlabeled mocks, one
untyped error string, a lost error body on PowerShell 7, bare-label promotion, `jev-latest` by
default, no `Retry-After` or jitter, a serial per-record array method attached to every array,
and no state budget or redaction. The plan's default was upstream PRs plus a thin wrapper.

## Decision

- The skill and client layer in this repo do **not** depend on dfinke/Jev at runtime, at
  install time, or in tests.
- dfinke/Jev is a **reference**: its strengths (native `Invoke-RestMethod`, fail-closed HTTP
  errors, capped backoff, question validation, pipeline ergonomics) and its defects become
  design requirements for our own client.
- Upstream PRs are not part of the plan. P1-1 is deferred; the owner may revive it later as
  goodwill, drafted under `docs/upstream/` and never submitted without approval.
- dfinke/Jev is MIT licensed. Adapting a specific piece of its code is allowed only with
  attribution (copyright notice and license text kept with the adapted code) and a note in
  `sources.lock.json` naming the commit it came from.

## Note (2026-09-25)

ADR 0003 moved the implementation to Python. This decision still holds; dfinke/Jev remains a
reference for client defects to avoid.

## Consequences

- The live transport behind the Phase 1 decision command posts directly to `/v1/systemone`.
- We own every fix listed in `03` from day one instead of waiting on upstream.
- We lose shared maintenance with the only other PowerShell client. Watch its upstream for
  ideas via `Sync-PriorArt.ps1 -CheckUpstream`.
