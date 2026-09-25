# 09. LLM plus Jev: learnings from source reads

Source reads done 2026-09-25 at the commits pinned in `sources.lock.json`. File and line
references are to those commits. None of the code was run. Repo content was treated as data.

| Repo | Commit | What it is |
| --- | --- | --- |
| browser-use/jev-ultrafast | 1231850 | Browser agent: Jev picks the operation and the element, a small LLM writes text only for `TYPE_TEXT` |
| tamaratran/fast-jev-compaction | e3f262a | Claude Code plugin: Jev decides which tool calls and results survive compaction |
| DoGMaTiiC/hermes-jev | 5dddbea | Hermes pre-tool gate v2 plus a live calibration write-up |

## The shared shape: Jev decides, the LLM generates, code checks both

jev-ultrafast is the cleanest public example of the loop from `07`:

1. Code observes the world and builds a **menu from live state**. It offers an operation
   only if at least one valid target exists, and never offers disabled, hidden or password
   fields (`model.py:87-89`, `snapshot.js:9,58`).
2. **One Jev request per step** asks the operation question plus one speculative target
   question per available operation. Only the head matching the chosen operation is used
   (`model.py:90-129`). This is `08`'s speculative fan-out inside an agent loop.
3. The LLM runs **only** when Jev picks `TYPE_TEXT`. It sees a small, fixed context and must
   return exactly `{"text": str}`, capped at 2,000 characters; `null` stops the run rather than
   guessing (`model.py:151-193`).
4. Code checks progress on its own: three non-wait actions that do not change the page stop the
   run as blocked, whatever Jev says (`agent.py:153-158`). `DONE` from Jev is explicitly not
   treated as proof of success. Outcomes are verified in code (`AGENTS.md:10`,
   `measure_flights.py:55-57`).

Recorded run: 17 Jev requests for 11 actions, about 5.3k input tokens per request. Matched
comparison is 3 pairs on one task (sign test p = 0.25); the authors say it is not a benchmark.

## Patterns to adopt

### Request and response hygiene
- **Strict response validator** (jev-ultrafast `model.py:30-45`). The chosen option must be in
  the offered set. Probability keys must equal the offered set exactly. Values must be finite
  and in [0, 1], sum to within 0.02 of 1, and the choice must be the argmax. Any failure means
  "no action". Port this to P1-2 as-is, and add our served-model check.
- **Reject booleans where a probability is expected.** `float(False)` reads as 0.0, meaning
  "no danger" (hermes-jev `jev.py:186-187`, test `test_bool_never_zero`). PowerShell's
  `[double]$false` has the same trap.
- **A partial answer set fails the whole decision**, never just the missing question
  (fast-jev-compaction `compact.ts:128`).
- **Fail reasons as a closed vocabulary** (transport, timeout, parse, answer_missing, and so on)
  so receipts are queryable (hermes-jev).

### Identity and staleness
- **Show Jev short ids; act on code-owned stable ids.** jev-ultrafast shows positional
  element numbers, maps each to a stable node id held by code, and re-checks a guard tuple and
  the page fingerprint just before acting (`agent.py:88-91`, `browser.py:88-98`). This also
  answers the positional-index accuracy risk in `02`: whatever Jev sees, code re-validates
  identity at execution time.
- **Consume the decision before acting** so a retry cannot act twice (`agent.py:90-91`).
- **Log before observing** so a later failure cannot erase the record of what was done
  (`agent.py:120-148`). Applies directly to our receipts.

### State
- **Pin in code, not by model.** The first message and the newest N messages are never
  candidates for dropping, whatever Jev answers (fast-jev-compaction `compact.ts:107`).
- **Staged fitting to a token budget** with per-entry accounting, recording which stage was
  needed (`state.ts:195-303`). Good template for our state budget. Their estimator is
  uncalibrated; ours should read `usage.input_tokens` from every response and track estimate
  error in receipts.
- **Compute numbers in code and state them as facts** (fast-jev-compaction passes
  `resultChars`; `compact.ts:64`). Matches non-negotiable 6.
- **"Not worth it" floor:** skip the action when the measured benefit is below a threshold
  (fast-jev-compaction skips compaction under a 25% reduction; `fast-jev.ts:271`).

### Decisions
- **Two narrow Nouls, three outcomes in code.** "Keep the call?" and "keep the result
  verbatim?" give keep, truncate or drop (`compact.ts:101-115`). The action mapping lives in
  code, where it can change without re-calibrating the questions.

### Calibration method (hermes-jev)
- **Record once, sweep offline through production code.** Run the live corpus once with
  caching off, save raw signals, then replay threshold sweeps through the same decision
  function the hook uses (`run_live.py:38-43`, `120-134`). No re-implementation to drift.
- **Pre-register the promotion criterion** in the document before the run, and publish
  "unfavourable findings" and "not measured" sections. Their v2 did not flip because it
  failed its own pre-registered rule.
- **Corpus provenance:** hash the corpus, record per-class counts and label sources, drop
  ambiguous rows instead of relabelling them.
- **Mirror check:** ask an inverted-rubric version of a Score question to confirm the model
  reads the scale the right way round (`gate.py:248-274`). Cheap probe for new questions.
- **Timeout arithmetic tied to the host:** they clamp per-attempt timeouts so the worst case
  (retries included) stays under Hermes' 30 s fail-closed hook timeout (`jev.py:40-44`). Our
  P3 hooks need the same budget per host (see `06` for Codex's opposite behavior).

## What their results teach

- **hermes-jev on 56 live commands** (50 safe, 6 destructive): v2 flagged 4 of 50 safe
  commands, caught 1 of 6 destructive ones, and let 5 through. `sends_outbound` was inverted
  on this data (up to 0.97 on safe commands, 0.13 on destructive). Three of six signals never
  fired. p50 latency 721 ms, max 2,072 ms. Labels came from Hermes' own regexes, so this is
  agreement with a regex, not accuracy. This is the most honest public data point on a Jev
  tool gate, and it is weak: **do not expect a Jev gate to catch destructive commands
  reliably without calibration on our own traffic.** It supports keeping deterministic
  policy first and Jev as extra evidence.
- **Their criterion was friction-only** (`FP(v1) >= FP(v2)`). Ours should require that
  detection does not get worse as well.
- **Six positives is not a calibration set.** P4-1 needs positives in every class the gate
  exists for, human labels, and a held-out split by group.

## Anti-patterns seen in all three

| Issue | ultrafast | compaction | hermes-jev |
| --- | --- | --- | --- |
| Defaults to `jev-latest`; served model never checked | yes | yes | yes |
| Thresholds unmeasured or absent | no thresholds (argmax always executed) | 0.5 for everything | hand-set, no held-out split |
| Retry-After ignored | fixed sleeps | no retries at all | honors it (from `02`) |
| Failure posture | stop on error | falls back to lossy LLM summary | **enforce fails open**, including on exceptions |
| No timeout | 25 s | **none** (hang stalls compaction) | clamped under host timeout |
| Receipts | partial | UI log only | JSONL, no fingerprint or served model |
| Redaction before egress | none | **none**: transcript text and tool inputs (Bash commands, Edit strings) leave the machine | n/a |

Other specific traps:
- fast-jev-compaction reads the 32k limit as a cap on state plus **all** questions. The
  official rule is 32k for state plus the **longest** question and 64k for everything (`06`).
  The result is about 8 times more requests than needed, each resending the full state. It
  also fires every request at once with no concurrency cap.
- fast-jev-compaction assumes deleted tool results can always be re-created by re-running the
  tool. That is false for non-deterministic tools (Bash output, web fetches).
- hermes-jev ships a contract JSON and a release manifest that no test checks against the
  code. Both have already drifted from HEAD. If we write a contract file, a test must load it
  and compare it to the live question set.
- sha256 manifests fail on Windows checkouts with `autocrlf=true`. Hash git blobs, or add a
  `.gitattributes` for hashed files.

## Changes to our plan

- **P1-2 wrapper:** add the strict response validator, bool rejection, whole-decision failure
  on partial answers, closed fail-reason vocabulary, `usage`-based token accounting, and
  consume-before-act ids.
- **P1-3 tests:** port the test patterns: recording stub that asserts one request with the
  exact question set, "policy never absolves", "bool never zero", "invented choice rejected".
  Mocks must still carry `IsMock` (the reviewed repos' mocks return one-hot answers with
  confidence 1.0 and no label).
- **P2-2 question sets:** use two-Noul, three-outcome designs where an action has a middle
  path. Add a mirror check for every Score.
- **P3 hooks:** per-host timeout budgets; enforce mode fails closed to ask, unlike hermes-jev.
- **P4 calibration:** record-once, sweep-offline through production code; pre-registered
  criterion covering friction and detection; corpus provenance file.
- **Contract files:** only with a test that binds them to code.
- **New candidate (not in plan):** Claude Code context compaction with Jev, done with
  redaction, a stable-id scheme, a correct reading of token limits, and a transcript backup
  before anything is dropped. Owner to decide whether it is in scope.

## Independent evidence: Rao and Callison-Burch (arXiv:2609.29769)

"JEV vs. LLMs as Rubric Judges: Cheaper, Faster, and Wrong in the Same Places", Delip Rao and
Chris Callison-Burch, submitted 2026-09-24 (https://arxiv.org/abs/2609.29769). The title,
authors, date and abstract were checked on arXiv directly. Details beyond the abstract come
from a research pass over the HTML version and are marked where not confirmed in the raw text.
This is the first independent, statistically careful comparison of Jev with LLMs.

- **Setup:** Jev (Choice as the main framing, plus Noul and Score) versus three flash-tier LLM
  judges (GPT-5.6 Luna, Gemini 3.8 Flash, DeepSeek V4.1 Flash), nine panels from seven
  benchmarks, 5,003 items, identical criterion texts, options shuffled with logged seeds.
  Bootstrap tests with Holm correction.
- **Accuracy:** significant differences in only 8 of 27 paired comparisons. Jev is ahead
  mostly on binary criteria and behind only on graded ones; most comparisons are
  inconclusive.
- **Cost and time:** summed over the nine panels, the LLM judges cost 29 to 325 times as much
  and took 30 to 220 times as long. The time figure is total wall clock across all panels,
  not per-call latency. Per the research pass (not confirmed in raw text), Jev ran 8 requests
  at once versus 16 to 32 for the LLMs.
- **Graded criteria:** all four judges agree more with each other than with the human labels
  and mostly score lower than the raters. A constant one-level shift lifts Jev's ELLIPSE exact
  accuracy from 13.4% to 50.4%. The authors suggest the raters followed scale conventions
  that the criterion texts leave out.
- **Confidence ranks Jev's own errors** on most panels (AUROC about 0.57 to 0.70 on six of
  seven graded panels), but not on ELLIPSE.
- **Correlated errors defeat the cascade.** On Jev's most confident errors, 96.0% of LLM
  verdicts repeat the same wrong answer (242 of 252), against about half if errors were
  independent. A Jev-first cascade that defers uncertain items to an LLM, replayed on the
  recorded verdicts, cuts cost (10.3% to 99.9%) but gains at most 1.5 points over the best
  single judge with cross-fitted thresholds, and at most 2.0 with oracle thresholds.
- **Limitations stated by the authors:** observational; one snapshot per judge with default
  settings, run once; 10 to 20% stratified subsets; zero-shot; rubric wording differs from the
  raters' original instructions.
- Companion paper: see the next section.

### What it changes for us

1. **Escalating to an LLM does not catch Jev's confident mistakes.** The LLM tends to be wrong
   on the same items. For gates, the escalation target for uncertainty must be a human or a
   deterministic check, not another model. This backs non-negotiable 1 and the fail-closed to
   "ask" rule with data.
2. **Model diversity is not error diversity.** This agrees with Self-MoA in `05`: mixing
   judges adds less than expected. For P5, measure error correlation between Jev and each
   proposer or judge before assuming a second opinion adds information.
3. **Use Jev as the cheap judge where criteria are binary.** Parity or better on binary
   criteria at a small fraction of the cost supports Noul-heavy question sets (P2-2).
4. **Graded scores need per-fingerprint calibration against our own labels.** A systematic
   one-level offset is exactly what isotonic calibration per fingerprint (P4-2) corrects. It
   also argues for writing scale conventions into Score level descriptions explicitly.
5. **Confidence is useful for ranking, weak as a gate on its own.** AUROC of 0.57 to 0.70 is
   real but modest; do not route high-consequence actions on confidence alone.

## Independent evidence: Li, Miao, Krishnan and Padman (arXiv:2609.26550)

"JEV-as-a-Judge: Accept When Confident, Escalate When Unsure", submitted 2026-09-22
(https://arxiv.org/abs/2609.26550; affiliation Carnegie Mellon per the HTML version). Title,
authors, date and abstract checked on arXiv directly (`raw/arxiv-2609.26550-abstract.md`).
Numbers below come from a research pass over the HTML through a summarizing fetch, so tables
were not seen directly; treat exact figures as provisional until re-read.

- **Setup:** `jev-1.13.0` against 16 generative and reward-model judges on RewardBench (400
  pairs), JudgeBench (350), HaluEval (240) plus controls, with blinded human adjudication. A
  642-item pilot was frozen before inference; a 670-item extension was frozen before pilot
  accuracy was inspected.
- **Standalone:** within about 3 points of the strongest LLM judge on ordinary preference and
  evidence-grounded factuality, at 0.36% of its fee; median latency about 0.15 s versus
  1.9 s. Much worse where a derivation must be checked (JudgeBench reasoning about 68% versus
  96%, coding about 76% versus 98%) or where the wrong answer is more elaborately written
  (a 9.2-point drop on style-adversarial pairs).
- **Confidently misled cases exist:** on reference-free prose, accuracy was near chance while
  mean confidence was 0.90 to 0.96 (error-detection AUROC about 0.52).
- **Cascade:** thresholds chosen on a 96-item selection set (maximize coverage while staying
  within two points of the fallback), frozen, then tested on the disjoint extension. At
  threshold 0.9 with the strongest LLM as fallback it accepted about 54% of pairs, lost 0.6
  points, and cost about 57% of the fallback alone. Thresholds did not transfer to every
  fallback model; one lost 2.35 points at its chosen threshold.
- **Order effects:** swapping the order of a pair flipped about 11% of JudgeBench decisions;
  they average probabilities over both orders.
- **Temperature scaling did not transfer** across benchmarks.

### How it fits with Rao and Callison-Burch

The two papers agree. On items Jev accepts confidently, the LLM is nearly interchangeable
with it (95.8% versus 96.5% here), which is the same fact Rao and Callison-Burch express as
correlated errors. The cascade gains in this paper come from routing Jev's **uncertain**
items to the LLM, not from catching Jev's **confident** errors. In the authors' words:
"Confidence routes well where the first stage is competent but uncertain, and badly where it
is confidently misled."

### What it changes for us

1. **Cost-saving cascades are fine for advisory work** (ranking, postflight scoring): accept
   confident Jev answers, send uncertain ones to an LLM.
2. **Confident-error protection must come from elsewhere:** deterministic checks and human
   spot audits of accepted items. This confirms the gate rule from the previous section.
3. **Some question kinds should never auto-accept:** correctness of code or reasoning, and
   comparisons where one candidate is more verbose or persuasive. The threshold registry
   needs a "never auto-accept" state for these.
4. **Pairwise questions run in both orders** and average (P2-2 question-set rule).
5. **A threshold belongs to its escalation target as well as its fingerprint.** It did not
   transfer across fallback models.
6. **Their selection rule is a usable template** for P4-4 promotion criteria (maximize
   coverage subject to accuracy within X points of the reference on a selection set, confirm
   on a disjoint frozen set), with a larger selection set than their 96 items.
7. **Count invalid outputs as errors** during calibration.

## Claims from an external AI summary, checked

The owner relayed a Grok answer on 2026-09-25. Checked against primary sources:

| Claim | Status | Source |
| --- | --- | --- |
| Diogo Almeida is CEO, InstructGPT co-author, ex-OpenAI, founded 2024 | Verified (the team page also says Google Brain) | https://typesafe.ai/team, arXiv:2203.02155, Business Wire release 2026-09-15 |
| Erik Gafni CTO and co-founder (Ravel, Invitae, Freenome) | Verified | https://typesafe.ai/team |
| Sasha Sheng COO and co-founder (Meta/FAIR, NeurIPS, ECCV) | Verified | https://typesafe.ai/team |
| "Jev" is a nod to Jevons Paradox | Verified | Business Wire release 2026-09-15 |
| Trained with RLCD (reinforcement learning for calibrated decisions) | Verified | docs.typesafe.ai/introduction/machine-learning-primer |
| Latency 70 to 500 ms | Not supported. Official figures: about 100 ms, under 100 ms, 150 ms. "70 ms" appears only on a third-party site | docs, press release |
| About 2 years in stealth | Inference only (founded 2024, launched 2026-09-15) | |
| Rao and Callison-Burch paper | Verified | arXiv:2609.29769 |
| Simon Willison wrote about hybrid LLM plus Jev stacks | Contradicted. His posts (2026-09-21, 2026-09-22) describe Jev and an `llm` plugin; his two-stage example pairs Jev with BM25, not an LLM. He stresses that evals matter even more than for LLM projects | simonwillison.net/2026/Sep/21/jev/ |
