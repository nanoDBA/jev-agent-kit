# jev_agent_kit

Reusable skill and tooling that lets coding agents (Claude Code, Hermes Agent, Codex) use
TypeSafe's Jev typed-decision model at runtime, as a cheap evidence layer inside a
mixture-of-agents workflow. Private, owned by nanoDBA.

Implementation language: Python, standard library only at runtime (`docs/adr/0003-python-implementation.md`).

Start with `CLAUDE.md`, then `docs/plan.md`. Implementation and owner-gated activation status
are tracked in `docs/CHARTER.md`; a passing offline check does not authorize live use.

## Layout

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Rules for any agent working in this repo. Read first. |
| `docs/plan.md` | Phases, backlog (Beads import source), open questions |
| `docs/evidence-backed-maintenance.md` | Offline evidence contracts, finding verification, and skill revision workflow |
| `docs/research/` | Digested research, numbered in reading order |
| `docs/research/raw/` | Source material you drop in (PDF, transcripts, X JSON) |
| `sources.lock.json` | Every prior-art repo, pinned to the commit that was reviewed |
| `scripts/` | Helper scripts (prior-art sync, transcript and X pulls) |
| `skills/`, `src/`, `tests/` | Canonical runtime skill, Python implementation, and offline tests |

## Source material not in git yet

- The 12-page field guide PDF lives in Google Drive (file id `<private-file-id-removed>`).
  Drop a copy in `docs/research/raw/` if you want agents to read the original.
- Video transcripts: `scripts/Get-VideoTranscripts.ps1`
- X posts: `scripts/Get-XThread.ps1` (pay-per-use API, pennies; run before 2026-09-28 13:00 UTC
  for the Sep 21 thread to still be inside the 7-day search window)
