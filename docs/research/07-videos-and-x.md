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

## Thread claims checked against the official docs

Source: docs.typesafe.ai, fetched 2026-09-25. Copies are in
`https://docs.typesafe.ai/` (the pages' embedded JavaScript widgets are left as they
are). Page names below are relative to https://docs.typesafe.ai/.

| Thread claim | Official position | Verdict |
| --- | --- | --- |
| Jev sits in the agent loop between tool calls | `concepts/how-to-build-with-system-one`: code owns the workflow; "avoid agent `while` loops when a software workflow can express the same behavior". Agents "work well when a person is monitoring", but "every loop introduces another opportunity to go off the rails". `introduction/coding-agents`: Jev is not a coding-agent model; use it inside an agent or app you build. `patterns/intent-routing`: classify first, then route to deterministic code, a specialist LLM, or a human. | Partly supported. The vendor prefers code-owned workflows to agent loops; Jev between tool calls fits only where code, not the LLM, drives the branching. |
| `urgency > 0.90` on a Noul | `primitives/noul`: a Noul "is not a scale of the thing you asked about. It is the probability that the answer is yes." Degree questions belong in a Score. Thresholds live in code and depend on the cost of a wrong answer; "values in the middle can go to a person". No `0.90` default appears anywhere. | Contradicted. "Urgency" is a degree, so use a Score. A single cutoff should be a band with a review middle. |
| Hundreds of micro-decisions per agent | `concepts/how-to-build-with-system-one`: "most queries complete in about 100 ms"; ask "explicit, narrow, specific, atomic" questions, called "probably the most important concept in this guide". | Supported for atomic questions. The ~100 ms figure is the vendor's. |
| Batching questions barely affects latency | `patterns/fan-out`: "additional questions usually have little effect on response time". `cookbooks/parallel_questions`: 13 questions in one call versus 13 calls, 5 runs each: identical answers, 12.2x cheaper, 10.0x faster. The page says the speed figure "assumes they run one after another. Fire them concurrently and the gap shrinks, but the 13x token cost stays." | Supported, with a caveat. The cost saving holds however the calls are made. The speed gain is measured against sequential calls. |
| Parallel questions do not influence each other | `concepts/how-to-build-with-system-one`: "One primitive's result does not become hidden context that changes another primitive's result." `cookbooks/parallel_questions`: "no question's answer depends on the 12 other questions sharing its request." | Supported by the vendor's own test. |

## What the docs add beyond the thread and our notes

- **Choice confidence is a function of the top probability.** The `confidence` page says
  confidence "is derived from the probabilities", and its interactive example computes Choice
  confidence as `(n * p_max - 1) / (n - 1)`. It also says the full `probabilities` are returned
  so callers can use a different measure. This backs `04`'s "appears to be a fixed function of
  the top probability" with a vendor source. The formula comes from the example's code, not the
  prose, so check it against live answers in P1-4.
- **Noul has no `confidence` field.** The `noul` value carries the certainty on its own.
- **Three bands, thresholds scale with risk.** From the `confidence` and
  `patterns/confidence-routing` pages: high means act, medium means confirm or gather more,
  low means do not act. The thresholds differ by action within one system (examples use a 0.5
  or 0.6 floor, and above 0.85 for a money transfer). "Start with conservative thresholds, test
  with your own data." This matches our per-fingerprint rule; the example numbers are
  illustrations, not calibrated values.
- **Calibration is claimed for groups, not individual answers.** `concepts/system-one`:
  "Calibration is measured across groups of predictions; it does not guarantee that an
  individual answer is correct." The community evidence in `04` (2 to 3x off in absolute
  terms on some tasks) still means we measure before enforcing.
- **Question-writing rules to carry into P2-1:** one condition per Noul (split "angry and
  asking for a refund" into two); phrase it so a high value means yes; use backticked
  dot-and-index paths such as `support.tickets[0].message` to point at state; put code-sourced
  data in named fields of structured `instructions`, not spliced into strings.
- **Self-consistency.** `cookbooks/consistency_noul_cookbook` uses 15 repeats per condition and
  a review band around 0.5 to absorb run-to-run movement.
- **Relevant cookbooks not yet in `02` or `05`:** `cookbooks/sde_cascade` (cheap model, then
  Jev verify, then a reasoning model only when needed) is close to our P5 postflight-verify
  shape. `cookbooks/llm_guardrails` screens LLM input and output with thresholded hazard
  probabilities (pass, review, block, route). `cookbooks/skill_suggestion` is the cookbook
  that shimo4228 reported does not transfer to Claude Code.

## Still missing

- Steps after 4 of this thread, and the images.
- Video transcripts (D-Z5HnLW_ho, L2K__oshGds); needs `yt-dlp`.
