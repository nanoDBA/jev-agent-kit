# Issue tracker: Beads, mirrored to GitHub Issues

Beads (`bd`) is the source of truth for work in this repo. Its database is private, so
anything outside contributors need to see or discuss is mirrored to GitHub Issues on
nanoDBA/jev-agent-kit and linked back with `--external-ref gh-<number>`.

## Conventions (Beads)

- **Create**: `bd create "Title" -t task -l needs-triage --body-file -` (types: bug, feature,
  task, epic, chore, decision, spike). Use a heredoc for multi-line bodies.
- **Read**: `bd show <id> --json --include-comments`.
- **List**: `bd list --status open --json`, filtered with `-l <label>`.
- **Comment**: `bd comments add <id> "..."`.
- **Apply / remove labels**: `bd label add <id> <label>` / `bd label remove <id> <label>`.
- **Close**: `bd close <id> --reason "..."`.

Check `bd <command> --help` before using a flag not listed here; do not guess.

## Mirroring to GitHub

- Mirror an item when it is public-facing (a reported bug, a contributor request, or work a
  pull request will reference): `gh issue create --title "..." --body "..."`, then
  `bd update <id> --external-ref gh-<number>`.
- A new GitHub issue from someone else is triaged, then imported:
  `bd create ... --external-ref gh-<number>`.
- Close both sides together, and comment on the GitHub issue with the outcome.
- Never copy private details (server addresses, local paths, owner-gated decisions) into a
  GitHub issue.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature
requests; `/triage` reads this flag.)_

## When a skill says "publish to the issue tracker"

Create a bead. Also mirror it to GitHub if it is public-facing.

## When a skill says "fetch the relevant ticket"

Run `bd show <id> --json --include-comments`. For a `#<number>` reference, use
`gh issue view <number> --comments` (GitHub shares one number space across issues and PRs,
so fall back to `gh pr view <number>`).

## Wayfinding operations

Used by `/wayfinder`. The **map** is a Beads epic with **child** beads as tickets.

- **Map**: `bd create "Map: ..." -t epic -l wayfinder:map`, holding the Notes /
  Decisions-so-far / Fog body.
- **Child ticket**: `bd create "..." --parent <map-id> -l wayfinder:<type>` where `<type>` is
  `research`, `prototype`, `grilling`, or `task`.
- **Blocking**: `bd dep add <child> --blocked-by <blocker>`. A ticket is unblocked when every
  blocker is closed.
- **Frontier query**: `bd ready --json` (open, no active blockers), restricted to the map's
  children (`bd children <map-id>`); drop any with an assignee; first in map order wins.
- **Claim**: `bd update <id> --claim`, the session's first write.
- **Resolve**: `bd comments add <id> "<answer>"`, then `bd close <id> --reason "..."`, then
  append a context pointer (gist + link) to the map's Decisions-so-far.
