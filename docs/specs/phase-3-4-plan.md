# Phases 3 and 4 plan: gates and calibration

Draft for a joint decision with Codex. Phase 3 wires the engine into host pre-tool hooks;
Phase 4 builds the calibration path that lets a gate move from shadow to enforce. They can run
in parallel (Phase 4 depends only on the Phase 1 engine). This plan also marks, explicitly,
where the unattended loop reaches an owner-gated boundary and must pause per `docs/CHARTER.md`.

## What the loop CAN build unattended

- **P3 hook adapters, shadow by default.** For Claude Code (PreToolUse), Codex
  (`hooks.json`/`config.toml`) and Hermes (a `pre_tool_call` plugin): a thin adapter that maps
  the host's tool-call event to a decision request against the `tool-call-gate` set, calls the
  engine, and maps the route to the host's response. Shadow mode returns "allow" always while
  recording receipts; enforce maps `ask` to the host's ask/approve and `accept` to allow.
- **P3 the child-process watchdog** the Phase 1 spec requires before enforce: run the engine as
  a child with a hard kill and a host-specific margin (Hermes fails closed at 30s; Codex fails
  open on timeout), so enforce has a real wall-clock bound.
- **P4 calibration tooling** that operates on a supplied labeled corpus: per-fingerprint
  isotonic (or Platt) calibration, ECE, Brier and coverage reports, threshold selection with a
  held-out split, and a promotion-criteria evaluator (maximize coverage subject to accuracy
  within X of the reference, confirmed on a frozen split, invalid outputs counted as errors).
- **P4 receipts into DuckDB** for shadow-mode analysis (a reader over the JSONL receipts).
- Tests, ruff, mypy --strict throughout; no live API in tests.

## What is OWNER-GATED (the loop stops and asks; charter)

- **A real labeled corpus.** P4 calibration needs labeled decisions from real traffic; the loop
  can build the tooling and test it on synthetic fixtures, but real calibration needs the owner
  to supply or authorize collecting a corpus.
- **Enabling enforce against real traffic.** Never unilateral. Requires measured calibration
  (P4) plus explicit owner approval.
- **One live smoke run** to verify the real API contract (owner supplies key + attestation).
- **Installing hooks into the owner's live hosts.** The adapters and installer are built and
  tested, but arming them in the owner's Claude Code / Codex / Hermes is the owner's action.

## Decisions to settle jointly (E1-E5)

- **E1** Ship all three host adapters now, or start with Claude Code and add Codex/Hermes once
  the contract is proven? Proposal: build the shared adapter core once, plus all three thin
  host shims, since the shim differences are small and documented in `docs/research/06`.
- **E2** Watchdog design: child process via the CLI JSON entry point (already exists) with a
  hard kill, versus in-process cooperative only. Proposal: child-process via the CLI, since the
  spec says enforce must not rely on the cooperative in-process deadline.
- **E3** Calibration library: stdlib-only isotonic (pool-adjacent-violators is simple and
  stdlib-friendly) versus allowing a dev-only optional dependency. Proposal: stdlib-only PAV to
  keep ADR 0003 intact; DuckDB stays a dev/analysis tool invoked out of process, not a runtime
  import.
- **E4** Where hook adapters live: `src/jev_kit/hooks/` (importable, tested) with host shims,
  installed by the Phase 2 installer. Proposal: yes.
- **E5** Do we attempt any real calibration in this loop, or build tooling + synthetic-fixture
  tests only and stop at the owner boundary? Proposal: tooling + synthetic tests only; then
  report and pause for the owner to supply a corpus and decide on enforce.

## Non-goals

Mixture-of-agents (Phase 5) and the vendor adapter / degraded mode (Phase 6) come after a
calibrated gate exists. Enforce promotion and live runs are owner-gated.

## Decisions settled jointly with Codex (2026-09-26)

- **E1 AGREE.** Build a shared adapter core plus three thin host shims now; the differences are
  small and the fail-open/fail-closed asymmetry belongs in the core from the start.
- **E2 AGREE.** Enforce uses a child-process watchdog with a hard kill; the in-process
  cooperative deadline cannot bound a blocked syscall.
- **E3 AGREE.** stdlib pool-adjacent-violators for isotonic calibration (with explicit tie and
  monotonicity tests); DuckDB stays an out-of-process analysis tool, never a runtime import.
- **E4 AGREE.** Adapters live in `src/jev_kit/hooks/`; hook wiring is a separate installer
  artifact from skill-tree linking, and arming a host is owner-gated.
- **E5 AGREE (strongly).** Build calibration tooling and test it on synthetic fixtures only.
  Do not attempt real calibration or promote any threshold; a real corpus and enforce are
  owner-gated. Record the promotion rule now (maximize coverage subject to accuracy within X of
  the reference on a frozen disjoint split, invalid outputs counted as errors) without inventing
  per-gate numbers.

### Two amendments promoted to acceptance criteria (safety)

1. **The adapter always owns its own deadline.** Never rely on a host hook timeout to fail
   closed: Codex fails open at 600s, so a gate that waited on the host timeout would allow. The
   adapter self-deadlines and synthesizes ask on timeout.
2. **Shadow must never accidentally block on Hermes.** Hermes fails closed at 30s (plus a 60s
   suppression window), so the shadow adapter must finish and return allow well under 30s, using
   the same self-deadline as enforce.

### Build order (from Codex)

P3 spine first (Opus): `hooks/core.py` (event->request, records->response, shadow/enforce,
self-deadline) and `hooks/watchdog.py` (spawn + hard kill). Then `hooks/claude.py`,
`hooks/codex.py`, `hooks/hermes.py` in parallel (Sonnet, disjoint). Then the installer/CLI
extension for hook wiring. P4 runs fully parallel (engine-only dep): `calibration/pav.py`
(Opus), `calibration/metrics.py` (Sonnet), `calibration/promotion.py` (Opus), and a DuckDB
receipt reader under `tools/` (Sonnet).
