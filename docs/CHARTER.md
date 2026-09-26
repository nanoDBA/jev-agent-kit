# Project charter: drive jev_agent_kit to completion, unattended

The single goal that governs the autonomous loop across all phases, the Claude + Codex
collaboration, and the limits of that autonomy. Read with `CLAUDE.md` (non-negotiables) and
`docs/plan.md` (phases). Set 2026-09-26.

## Overall goal (north star)

Deliver a reusable, versioned Jev runtime kit that Claude Code, Codex and Hermes can use
safely at runtime as a cheap evidence layer, complete through Phases 1 to 6, where:

- code owns authority and Jev only supplies evidence; gates fail closed to "ask" and never
  enforce on an uncalibrated threshold; advisory calls fail open to "no advice";
- every decision is auditable (receipts), every threshold is fingerprinted and measured, and
  nothing sensitive leaves the machine without passing the egress policy;
- each phase passes an independent adversarial review with zero blockers and zero majors, on
  green gates (pytest, ruff, mypy --strict);
- the work stays domain-neutral and stdlib-only at runtime (ADR 0003), with database support
  as an optional pack.

## Definition of done

1. Phases 1 to 6 in `docs/plan.md` each converged to the bar above.
2. The kit installs into all three hosts and its gates run in shadow, with a documented,
   measured path to enforce per gate (Phase 4).
3. One live smoke run has verified the real API contract (owner supplies key + attestation).
4. Merged to `main` with the owner's explicit approval.

## Autonomous execution loop (spans all phases)

Drive phases in dependency order (2 -> 3 and 4 in parallel where deps allow -> 5 -> 6). For
each phase:

1. Draft the phase plan with the open decisions; confer with Codex (or a Codex-role reviewer)
   to settle them jointly; record the agreed decisions.
2. Model the phase's work as a Beads graph mirrored to GitHub issues; run rounds that dispatch
   ready nodes (Opus for safety-critical, Sonnet subagents for mechanical work on disjoint
   files, in parallel), each node gated on the three checks plus a regression test.
3. Commit per node to the phase branch; open a PR into `phase-0`.
4. End the phase with an independent adversarial review; file any findings as nodes and
   continue until the review returns no blockers or majors.
5. Merge the phase PR into `phase-0`, advance to the next ready phase.

Round cap per convergence: 6. On a stall or the cap, stop and report.

## Autonomy: what the loop may decide and do

May, without asking:
- settle in-scope design decisions jointly with Codex, and record them as ADRs or plan notes;
- create and manage branches, commits, issues and pull requests; merge feature PRs into
  `phase-0` once converged and green;
- spawn subagents and run parallel efforts within the size guidance.

Must confer with Codex before acting (joint decision, recorded):
- any change to a non-negotiable, an ADR, or the egress policy;
- promotion criteria and any move from shadow toward enforce (Phase 4).

Must stop and ask the owner (never unilateral):
- merging `phase-0` into `main`;
- enabling enforce mode against real traffic, or any live API spend beyond one capped smoke;
- making the repo public, or sending data to any new external service;
- anything the owner's standing rules reserve.

## Reporting

Report at each phase boundary (converged, merged, what is next), and immediately on a stall,
a hit round cap, or a decision the loop and Codex cannot resolve. Otherwise run unattended.

## Status pointer

Progress lives in the Beads graph (top-level goal `jak-goal`, phase epics beneath it) and the
GitHub tracking issue. Phase 1 is complete and merged into `phase-0`.
