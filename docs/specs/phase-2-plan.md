# Phase 2 plan: the runtime skill

Draft for a joint decision with the reviewer (Codex role). Phase 2 turns the Phase 1 engine
into an agent skill: a `SKILL.md` that tells a coding agent when and how to reach for Jev,
versioned question sets the engine can load, an installer that links the skill into each host,
and a skill-library audit. It adds no new authority: the engine still owns validation, routing,
egress, receipts. Governed by ADR 0001-0003.

## Scope (from docs/plan.md, refined)

- **P2-1 `skills/jev-runtime/SKILL.md`** (one canonical copy): when to reach for Jev
  (classify, route, score, verify, gate); the question-design rules from the research
  (`docs/research/07`, `08`, `09`); the fail-asymmetry table; the weak-spot guardrails (keep
  math/counting/literals in code); the receipt and route contract; and the hard rule that a
  gate escalates to a human or deterministic check, never to another model (arXiv:2609.29769).
- **P2-2 versioned question sets** in `skills/jev-runtime/questions/`, domain-neutral, in the
  Phase 1 file shape (kit.consequence, criteria per type, state_schema, escalation_target):
  a preflight router, a postflight verifier, a tool-call gate, and a stop-or-continue set.
  Plus one optional SQL domain pack (T-SQL destructiveness, log-line triage) with its own
  egress profile that can only tighten the core.
- **P2-3 installer** (Python, per ADR 0003): links the one canonical skill into each host's
  skill directory (Claude Code `~/.claude/skills`, Codex/Hermes `.agents/skills` and
  `~/.hermes/skills`, per `docs/research/06`), idempotent, dry-run by default, never
  duplicating the skill.
- **P2-4 skill-library audit**, borrowed from aleksvega: a scanner that flags prompt-injection
  and dangerous-command patterns in a skill directory. Reuses the Phase 1 egress detectors.

## Decisions to settle jointly (D1-D6)

- **D1 Question-set count for v1.** Ship all four core sets now, or start with two (tool-call
  gate + postflight verifier) and add the router and stop-or-continue once calibrated?
  Proposal: ship all four as *uncalibrated* (shadow-only for gates), since the engine already
  forbids enforce without a calibrated threshold, so shipping them is safe and useful.
- **D2 SQL pack in Phase 2 or defer.** The owner made the kit domain-neutral. Proposal: build
  the SQL pack as the worked example of a domain profile (it exercises the profile mechanism),
  but clearly optional and off by default.
- **D3 Skill format.** One `SKILL.md` with YAML frontmatter (name, description, version) that
  works across hosts, plus a `questions/` dir. Proposal: yes; keep host-specific glue in the
  installer, not the skill.
- **D4 Installer safety.** Proposal: dry-run by default, `--apply` to write, symlink where the
  OS allows else copy-with-hash, refuse to overwrite a differing file without `--force`,
  print what it would do. No network.
- **D5 Audit output.** Proposal: the audit returns findings (path, rule id, severity) and a
  nonzero exit on any high-severity hit; it reuses `egress.scan_text` plus a small set of
  skill-specific injection patterns. Data, never executed.
- **D6 Tests and gates.** Same bar as Phase 1: pytest, ruff, mypy --strict; question sets are
  validated by loading them through `load_question_set`; the installer and audit are tested on
  temp dirs with no network.

## Non-goals (later phases)

Host hooks that call the engine (Phase 3), calibration corpus and thresholds (Phase 4),
mixture-of-agents (Phase 5), resilience and the vendor adapter (Phase 6).
