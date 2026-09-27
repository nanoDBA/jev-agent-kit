# 08. Parallel usage: best practices

Researched 2026-09-25. Sources are official TypeSafe docs (listed in `raw/SOURCES.md`; page
names below are relative to https://docs.typesafe.ai/),
the official skill `typesafe-ai/skills` at the pinned commit
(`skills/typesafe-ai/SKILL.md`), and pinned prior art. Vendor numbers are the vendor's, not
our measurements.

"Parallel" means two different things with Jev, and the guidance differs:

- **A. Many questions about one state:** one request, evaluated in parallel on the server.
  This is the vendor's primary recommendation.
- **B. The same questions about many states** (items, pairs, candidates): the state differs, so
  either send several requests concurrently from the client, or pack the items into one state.

## A. Many questions about one state (one request)

1. **Send every question that uses the same state in one request.** Question types can be
   mixed. "Adding questions barely changes the response time and costs only the tokens for
   the extra questions." (`primitives`, "Ask multiple questions together")
2. **Ask speculative questions.** Include questions whose answers matter only for some inputs
   and let code ignore the unused ones ("Speculative fan-out", `patterns/fan-out`). The
   official skill adds: "State each speculative premise explicitly" and "Ignore uncertainty on
   unused branches."
3. **Questions cannot see each other.** "One primitive's result does not become hidden context
   that changes another primitive's result." (`concepts/how-to-build-with-system-one`). The
   vendor's 13-question test found "no batching effect" on answers across 5 runs; run-to-run
   noise is "a property of the question, not of how you batch" (`cookbooks/parallel_questions`).
4. **Make a second request only for a real dependency:** when code needs the first answer to
   fetch evidence, build new state, or choose the next question's options. "Two requests are the
   exception, not the rule." Cookbook examples of real dependencies: skill suggestion (rank
   182, then re-judge the top 3 with full text), structure recovery, hierarchical
   classification. (`primitives`, "When one question depends on another")
5. **Split complex judgments into atomic questions and combine them in code** with weights you
   own (Composite scoring). Splitting costs a few question tokens, not extra round trips.
   (`primitives`; `patterns/composite-scoring`). The official skill qualifies this: split
   "without destroying the relationship being judged"; atomic does not mean trivial.

### Cost and speed: what the numbers mean

- The saving comes from sending the **state once** instead of once per question. In the GDPR
  test (a roughly 54,000-character article), 13 questions in one call versus 13 separate calls
  gave identical answers, 12.2x lower cost and 10.0x lower latency.
- The latency figure **sums sequential single calls**. The page says: "Fire them concurrently
  and the gap shrinks, but the 13x token cost stays." So the dependable win is cost; the speed
  win is mostly against a serial client.
- The `primitives` page quotes the same test as "11.5x cheaper and 9.6x faster". The two pages
  disagree slightly; cite the cookbook, and neither as our own number.
- The official skill: "Extra questions still use tokens; measure actual request budgets,
  cost, and end-to-end latency."

### Limits that bound a request

- 64k tokens for state plus **all** questions; 32k tokens for state plus the **single longest**
  question (`models`). Many questions eat into the 64k budget.
- Rate limits: 1,200 requests per minute and 250,000 tokens per second, "adjusting dynamically"
  and liable to change without notice (`models`). Batching reduces the request count.
- No documented cap on questions per request (see `06`).

## B. The same questions about many states

Two approaches appear in the official cookbooks.

### B1. One request per item, run concurrently

- When each question is about one item or pair, the state is that item, so requests cannot be
  merged: "One request per passage, so cost scales with `k`. Nothing batches passages into one
  request, because each question is about one pair." (`cookbooks/classifying_rag_passages`)
- Run those requests concurrently with a **bounded** pool. Re-ranking: 1,200 calls through a
  thread pool with `max_workers=12` (`cookbooks/rerank_typesafe`). Skill suggestion:
  `WORKERS = 8`, "small pool: enough to keep a live run to minutes, gentle on rate limits"
  (`cookbooks/skill_suggestion`). These are the only concurrency figures the vendor gives; there
  is no documented recommended cap.
- Within each per-item request, still ask every question about that item together. The
  re-ranking page notes that it asks one question per pair "for clarity" and that "a real
  application would ask several questions about the same pair in one call".
- Prior art: dfinke/jev-experiments (Jev Lens) keeps at most two requests in flight, pauses
  90 ms to merge bursts of requests, and discards stale responses by query revision.
  Gilbert09/jev-cli's benchmark uses a configurable worker pool. dfinke/Jev's `.Jev()` makes one
  serial call per record (finding 6 in `03`), which is the pattern to avoid.
- SDK defaults relevant to a pool: 10 s per-call timeout, 2 retries, and a 30 s total retry
  budget (`06`). The Python SDK has an `AsyncTypeSafeClient` for asyncio concurrency.

### B2. Pack the items into one state and ask one question over them

- Semantic search puts 218 document lines in one state and asks a single Choice whose options
  are the **line ids**, getting a probability per line from one request
  (`cookbooks/semantic_find`). This is capped at 255 options per Choice.
- Hierarchical classification runs a beam search: each request evaluates `K` paths as parallel
  Choice questions, "so extra exploration adds little wall-clock latency"
  (`cookbooks/hierarchical_classification`).
- Risks of packing:
  - **Context rot.** The jaggedness page says accuracy falls as the state fills with detail
    that is irrelevant to a given question. Packed items are irrelevant to one another, so this
    applies directly.
  - **Positional indexing.** The pedramamini gist measured 86 of 320 answers wrong when
    `items[i]` was addressed by position over 150 items, and 0 of 320 wrong with keyed objects
    (`02`). Always use stable keyed ids, never array positions.
  - **The 32k budget** covers the state plus the longest question.

## Implications for this repo

- **P1-2 wrapper API:** the unit of work is one state snapshot plus a keyed question set, sent
  as one request. Thresholds stay per question fingerprint (non-negotiable 5), because answers
  are independent even when requested together. Emit one receipt per decision, each carrying
  the shared request id (`x-typesafe-request-id`) and state digest.
- **Fail asymmetry across a mixed request:** if a gate question and an advisory question share
  a request, one transport failure hits both. Apply the asymmetry per question: the advisory
  answer becomes "no advice" and the gate becomes "ask". Never retry only the advisory part and
  let the gate pass.
- **Concurrency (B1):** (ADR 0003: Python, so a bounded `ThreadPoolExecutor`; originally
  written for PowerShell `ForEach-Object -Parallel`) use a small worker limit (start at 4; the vendor cookbooks use 8 to 12), plus a shared budget for
  requests per minute and tokens, and the per-run call cap from `CLAUDE.md`. Each hook runs as
  its own process, so a per-process cap is not a global cap. Record this as a known limit.
- **Packing (B2):** allowed only with keyed ids, inside the state budget. Prefer B1 when the
  items are long or unrelated to each other.
- **P1-4 smoke script:** measure, on our own states, the latency as the question count grows
  (1, 5, 13, 25), the answer stability batched versus single, and the time for concurrent
  single calls versus one batched call. This replaces vendor numbers with ours.
- **P2-1 skill text:** state rules A1 to A4 as rules. Coding agents in particular tend to make
  one call per question; the `primitives` page says so and the official skill guards against it.
