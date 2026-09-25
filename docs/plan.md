# Plan

## Open questions for the owner (decide before Phase 1 code)

1. **dfinke/Jev strategy:** upstream PRs plus a thin wrapper module, or a wrapper only?
   Default: PRs for findings 1 to 5 in `research/03-dfinke-jev-review.md`, wrapper for
   receipts, egress, budgets and fingerprinted thresholds.
2. **Host priority:** Claude Code first, then Codex, then Hermes? (Default order.)
3. **Egress policy:** may production T-SQL text, object names or log lines leave the
   machine at all? Which data classes are always blocked?
4. **Repo name:** `jev-agent-kit` is a placeholder.

## Phases

### Phase 0: bootstrap
- P0-1 Create private repo `nanoDBA/<name>`, push this scaffold.
- P0-2 Initialize Beads and import this backlog with dependencies.
- P0-3 Run `scripts/Sync-PriorArt.ps1`; pin the pedramamini gist revision.
- P0-4 Pull video transcripts and X posts into `research/raw/`; write deltas to
  `research/07-videos-and-x.md`.
- P0-5 Re-verify facts that may have moved since 2026-09-25: current versioned model id,
  documented Choice/Score limits, error schema, Retry-After behavior, Codex and Hermes skill
  directory paths.

### Phase 1: PowerShell client layer (depends on P0-5 and open question 1)
- P1-1 Draft upstream PRs for dfinke/Jev under `docs/upstream/dfinke-jev/` (mock labeling,
  typed errors plus ErrorDetails body, confidence promotion, model pin, Retry-After plus
  jitter). Owner approves before submission.
- P1-2 Wrapper module: receipts (JSONL), state budget and redaction, deterministic egress
  check, question fingerprints, per-fingerprint threshold registry marked calibrated or not.
- P1-3 Labeled mock transport and Pester suite; no live calls in tests.
- P1-4 Smoke script with a hard call cap for live verification.

### Phase 2: runtime skill (depends on P1-2)
- P2-1 `skills/jev-runtime/SKILL.md`: when to reach for Jev, question design rules, fail
  asymmetry table, weak-spot guardrails, receipt format. One canonical copy.
- P2-2 Versioned question sets in `skills/jev-runtime/questions/`: preflight route,
  postflight verify, tool-call gate, T-SQL destructiveness, log-line triage.
- P2-3 `install.ps1` and `install.sh` linking the canonical skill into each host's skill
  directory (paths verified in P0-5).
- P2-4 Skill-library audit borrowed from aleksvega (prompt injection, dangerous commands).

### Phase 3: gates (depends on P2-1)
- P3-1 Claude Code PreToolUse hook: shadow by default, enforce fails closed to ask.
- P3-2 Codex hook with the same contract.
- P3-3 Hermes pre-tool plugin; account for the loader's own timeout behavior.

### Phase 4: calibration (depends on P1-2, can start in parallel with Phase 2)
- P4-1 Labeled corpus from our own traffic, grouped for fit/held-out splits.
- P4-2 Per-fingerprint isotonic calibration, ECE, Brier, coverage reports.
- P4-3 Receipts into DuckDB for shadow-mode analysis.
- P4-4 Promotion criteria documented per gate.

### Phase 5: mixture of agents (depends on P4-2)
- P5-1 Shared postflight question set applied to every proposer.
- P5-2 Self-MoA baseline: N samples from one strong model plus Jev selection.
- P5-3 Multi-model variant; keep only if it beats P5-2 on our tasks.

### Phase 6: resilience (depends on P1-2)
- P6-1 `system-one-adapter-python` as shadow comparator.
- P6-2 Degraded mode when Jev is unavailable: advisory off, gates to ask.
