# Unattended review-and-fix loop

How the Phase 1 code-review findings are driven to convergence without manual relay, using a
dependency graph of issues (Beads plus GitHub) and a bounded control loop. Set up 2026-09-25.

## Roles

The owner chose a single-driver loop: one agent (Claude) plays both the fixer and, each round,
an independent adversarial reviewer (a fresh subagent given the reviewer contract, so its read
is not colored by the fixer's intent). There is no external Codex runner in this configuration.

## Graph engineering

Each open finding is a node. Nodes carry an owner label (`agent:opus` for safety or
correctness work, `agent:sonnet` for mechanical work on disjoint files) and edges to the nodes
that must finish first. The graph is the single source of truth for what is workable now:

- Beads holds the real edges (`bd ready` returns only nodes whose dependencies are closed).
- GitHub issues mirror the nodes for visibility and audit; the Beads bead's `external-ref` is
  the GitHub issue number.

Current edges: C16 -> C08; C20 -> C12; C21 -> C12, C16, C19; GATE -> every finding.

Disjoint-file rule: two workers never edit the same file in the same round. `agent:sonnet`
nodes are only dispatched when their files do not overlap an in-flight `agent:opus` node.

## Loop engineering

One round:

1. Pull ready nodes (`bd ready`, filtered to `finding`).
2. Dispatch: Opus nodes done in-session serially (shared engine files); Sonnet nodes launched
   as background subagents on their own files, in parallel.
3. On each node's completion, run the gates in the repo root:
   `python -m pytest -q`, `python -m ruff check src tests scripts`, `python -m mypy`.
   A node is done only when all three pass and it has a regression test for its finding.
4. Commit the node's change to `phase-1-impl` (pushed to PR #1), close its Beads bead and its
   GitHub issue, relabel `status:done`.
5. Repeat until no ready findings remain.

Then the GATE node: a fresh subagent reviews the whole `phase-0...phase-1-impl` diff against
the spec with the reviewer contract. If it returns blockers or majors, each becomes a new
finding node (Beads + GitHub) and the loop continues. If it returns none, the loop converges.

## Convergence and stop criteria

- Converged: graph drained and the GATE review returns no blockers or majors, with all three
  gates green. The loop then reports and stops.
- Round cap: at most 6 fix rounds. If not converged by then, stop and report the open nodes.
- Stall: if a round closes no nodes and files no new ones, stop and report.
- The loop may commit and push to `phase-1-impl` and update PR #1, open and update issues, and
  manage feature branches. It does not merge into `main` without the owner's approval.

## Status protocol

GitHub labels: `status:ready`, `status:in-progress`, `status:review`, `status:done`. Beads
status mirrors it (open, in_progress, closed). Every commit that closes a node names the
finding id and ends with the standard attribution.
