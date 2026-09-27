# Claude + Codex review loop (via GitHub PR comments)

The shared channel is a GitHub PR. Codex posts a structured review as a PR comment, Claude
fixes and replies with a comment, and Codex re-reviews. Claude drives the fix-and-decide loop
and escalates to the owner only for owner-gated items: a real corpus, enabling enforce, a live
API call, arming a host, merging, and making anything public.

## Reviewer instructions

The reviewer's standing instructions live in [codex-reviewer.md](codex-reviewer.md). Paste a
short per-round request built from the template at the end of that file, for example:

```text
Read docs/handoffs/codex-reviewer.md and follow it.
Review PR #40 (phase-0-hardening -> phase-0) at head 45111c3.
Your last verdict: REVIEW: revise at 68448a8 (H3, H15).
This round claims to fix: H3 (language profiles rejected), H15 (string-root JSON).
Focus: none.
Out of scope: PR #42, PR #43.
```

## Claude's side

When Codex's comment arrives, Claude parses the `findings` block, reproduces each finding,
fixes it on the PR branch with a regression test built from the exact counterexample, and
replies with a comment that maps each finding to its fix and test. When the reviewer posts
`REVIEW: merge` with gates green, Claude brings the merge decision to the owner and never
merges unasked. Claude proposes joint design questions in a comment and proceeds on the
reviewer's agreement. A genuine disagreement, or an owner-gated item, goes to the owner.
