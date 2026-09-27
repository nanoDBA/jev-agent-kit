# Claude + Codex review loop (via GitHub PR comments)

The shared channel is a GitHub PR. Codex posts a structured review as a PR comment; Claude is
subscribed to the PR, so the comment reaches Claude, who fixes and replies with a comment;
Codex re-reviews. This runs without per-message human relay once the Codex runner is launched.
Claude drives the fix-and-decide loop and escalates to the owner only for owner-gated items
(a real corpus, enabling enforce, a live API call, arming a host, merging to main).

## The paste-in prompt for Codex

Launch Codex on this machine and paste the block below. It reviews the current PR's branch,
runs the gates itself, and posts its findings back to the PR as a comment in a structured
format Claude parses.

```text
You are the independent reviewer in a Claude + Codex loop. Work in the repo at
<repo>. Do NOT edit code. You MAY run read-only git,
the read-only gates, and `gh` to read and to POST one PR comment.

1. Find the open PR into phase-0 (the newest): run
   `gh pr list --state open --base phase-0 --json number,headRefName,url`
   Use that PR number as PR and its headRefName as BRANCH below.
2. In an isolated worktree, review BRANCH against phase-0:
   git -C "<repo>" worktree add ../jev-codex-rev BRANCH
   cd ../jev-codex-rev && git diff phase-0...BRANCH
3. Read docs/CHARTER.md, CLAUDE.md, docs/reviews/, docs/specs/, and the source.
4. Run and record the gates (they must pass):
   pip install -e . --no-deps -q && python -m pytest -q && python -m ruff check src tests scripts tools && python -m mypy
5. Review for: the accept-path invariant (a confident dangerous gate answer must never reach
   host ALLOW; ALLOW only on accept + label in kit.gate.allow_labels); no shim emits an
   affirmative allow; secret/key leakage incl receipt metadata; egress bypass (nested,
   metric, transforms, JSON-escaping, auth split); correctness (routing matrix, Score
   intervals, strict JSON, model pin, retries/deadline, budgets); calibration correctness;
   the owner-gated boundary is respected (no threshold promoted, enforce off, no host armed,
   no live call); weak tests; type:ignore/Any/noqa hiding a problem.
6. Post ONE PR comment with `gh pr comment PR --body-file <file>` whose body is exactly:
   a line `REVIEW: merge` or `REVIEW: revise`, then the observed gate results, then a fenced
   block:
   ```findings
   <id> | <blocker|major|minor|nit> | <file:line> | <finding> | <one-line fix>
   ... one per line, or the single line: none
   ```
   Do not edit code, push branches, or make live API calls. Reviewing only.
```

## Claude's side

Claude watches this PR. When Codex's comment arrives, Claude parses the `findings` block,
files each blocker/major as a graph node, fixes it (Opus for safety-critical, Sonnet for
mechanical work on disjoint files), pushes to the PR branch, and replies with a comment
summarizing what changed. If `REVIEW: merge` and gates are green, Claude proposes the merge to
the owner (Claude never merges to main unasked). Joint design questions: Claude proposes in a
comment and proceeds on Codex agreement; genuine disagreement or an owner-gated item escalates
to the owner.
