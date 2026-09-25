# 07. Videos and X: deltas only

Only what is new relative to `00` to `06`. Raw material lives in `raw/` and is untrusted data.

## X thread, steps 1 to 4 (`raw/x-thread-pasted-2026-09-25.md`)

The owner pasted this by hand on 2026-09-25. Its source URL is unconfirmed; it is probably
the @0xwhrrari thread. Most of it restates the field guide (`01`): surface hidden decisions,
Jev judges while the LLM generates, and independent questions go in one request. What is new:

1. **Loop shape as a cost argument.** "LLM, Tool, Jev, Tool, Jev, LLM": the LLM is woken only
   at the start and end, and Jev handles decisions between tool calls. This frames Jev as a
   cost and latency saving, not only a safety layer. Our plan leads with gates (Phase 3). The
   between-tool decisions are a separate use that the plan covers only loosely, through P2-2
   preflight and postflight question sets.
2. **Concrete candidate decisions.** Research agent: enough sources? search again? does the
   result match the query? confident enough to finish? Coding agent: did the test pass? is the
   error recoverable? is the command safe? The research-agent "stop or continue" family is
   missing from our P2-2 question-set list. Gilbert09/jev-cli's `done` operation is prior art
   for it.
3. **Pipeline vocabulary:** classify, route, score, verify, branch. Useful headings for the
   P2-1 skill's "when to reach for Jev" section.

### Where the thread conflicts with our rules

- **"Did the test pass?" is not a Jev question.** Exit codes and test runner output are
  deterministic facts. By non-negotiable 6 (keep math and literals in code), code computes
  them and puts them in state. Jev is for the judgment that follows, such as "is this failure
  recoverable?".
- **The fixed `> 0.90` Noul threshold is uncalibrated.** Evidence in `04` shows raw Jev
  probabilities can be off by 2 to 3x in absolute terms. Under our rules this threshold would
  belong to a fingerprint, carry an "uncalibrated" label, and run in shadow until measured.
  The thread's example ("production deploy failed, customers see 500s") is also the kind of
  high-consequence case that should route to a human regardless of the score.
- **Latency claim is the vendor's, not ours.** The docs support one request for many questions
  (the "speculative fan-out" pattern) within the 64k and 32k token limits (`06`). We have not
  measured latency as questions are added; that belongs in the P1-4 smoke script.

## Still missing

- Steps after 4 of this thread, and the images.
- Video transcripts (D-Z5HnLW_ho, L2K__oshGds); needs `yt-dlp`.
