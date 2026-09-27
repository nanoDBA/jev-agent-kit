# 10. How others demonstrate Jev, and what has been measured

Researched 2026-09-27. Extends [02-prior-art](02-prior-art.md) (which catalogued community
repos for safety patterns) with a different question: which examples make a Jev tool worth
trying, and which numbers about it hold up. Sources were read as data; ideas are summarized
in our own words, with links. None of the numbers below are our measurements.

Provenance labels: **VENDOR** (TypeSafe), **INDEPENDENT** (a third party, method stated,
not replicated by us), **ANECDOTAL** (engagement counts, secondary reports).

## How the strongest projects show value

1. **A real run with one number in the caption, linked to a script you can rerun.**
   [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) pairs a recording
   of a browser agent with its median task time and an "evidence and limits" section.
   [shitianfang/jev-use](https://github.com/shitianfang/jev-use) shows four short recordings,
   each captioned with one measured number, and offers a keyless mock mode.
2. **Accuracy against human labels, plus repeat variance.**
   [danielgshea/jev-as-a-judge](https://github.com/danielgshea/jev-as-a-judge) grades agent
   runs against a human oracle and repeats each judgment 100 times.
3. **Publishing the negative results.** [Gilbert09/jev-cli](https://github.com/Gilbert09/jev-cli)
   ships two of its four hooks disabled because its A/B test could not tell them apart from
   no hook. That makes the enabled ones more believable.
4. **A caveats section next to every number** (jev-ultrafast, RahulBalakavi/claude-code-jev,
   TypeSafeAI/jev-harness, which labels its mock values as mock).
5. **Natural-language request, then the typed request, then the distribution, then the
   code's branch** ([FrancoisChastel/jev-code](https://github.com/FrancoisChastel/jev-code),
   [anasbekheit/typesafe-jev-mcp](https://github.com/anasbekheit/typesafe-jev-mcp)). Our
   README's agent-conversation section already follows this shape.
6. **Contrasting inputs that swing the probability** ([dfinke/Jev](https://github.com/dfinke/Jev):
   an outage versus a healthy system).
7. **A reliability diagram with confidence bands**
   ([keduseworku/Jev-Calibration](https://github.com/keduseworku/Jev-Calibration)), which maps
   directly to our calibrate-before-enforce rule.

Engagement (ANECDOTAL): a community list,
[awesome-jev-use-cases](https://github.com/walidboulanouar/awesome-jev-use-cases), ranks demos by
likes; the top one is a Claude Code plugin that scores tool calls. Demos that spread had one
visible decision inside a loop the viewer already knows. TypeSafe's own launch leaned on a
real-time game demo for the same reason.

Few demos show a **decision receipt**.
[TypeSafeAI/jev-harness](https://github.com/TypeSafeAI/jev-harness/blob/main/docs/hardening/05-receipt-binding.md)
documents bound receipts and replay; this kit's demo prints one real receipt with each field
explained.

## Official use cases (VENDOR)

From the [docs index](https://docs.typesafe.ai/llms.txt) and the
[launch post](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (2026-09-15):

| Scenario | Question shape | Vendor-reported result |
| --- | --- | --- |
| [Skill suggestion](https://docs.typesafe.ai/cookbooks/skill_suggestion.md) | A Choice over the skill list, then a rerank and Noul checks | Fewer wrong and needless skill loads |
| [LLM guardrails](https://docs.typesafe.ai/cookbooks/llm_guardrails.md) | Several Nouls plus a severity Score | Pass, review, block or support bands |
| [SDE cascade](https://docs.typesafe.ai/cookbooks/sde_cascade.md) | A small model extracts, Jev verifies, a reasoning model handles the rest | Similar quality at lower cost |
| [Rerank](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md) | A Noul per query and candidate pair | Better top-k on a legal corpus |
| [Confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md) | One Choice; confidence decides act, confirm or hand off | Three confidence bands |

The launch post claims large speed and cost multiples. We found no independent reproduction;
write-ups such as
[pasqualepillitteri](https://pasqualepillitteri.it/en/news/16460/typesafe-jev-chatless-ai-beats-claude)
treat them as awaiting outside verification. Smaller gains on other tasks are not a test of
those figures. Treat vendor multiples as claims.

## Independent measurements, checked at the source

| Source | Method | Result | Caveats (the author's own) |
| --- | --- | --- | --- |
| [danielgshea/jev-as-a-judge](https://github.com/danielgshea/jev-as-a-judge) | 5 frozen agent runs, each judged 100 times, against human pass/fail labels | Jev matched every human label; the compared LLM judges' repeat variance was 92x to 913x higher; $0.00035 and 0.44 s per call | Small corpus; observational, not causal |
| [PrimeLine](https://primeline.cc/blog/typesafe-jev-pre-registered-test) (2026-09-18) | Pre-registered, about 9,750 calls across the study | Beat Haiku on commit classification (65.8% vs 54.6%), lost on knowledge categories (90.7% vs 97.8%). Test 1, a separate pool of 2,600 public-dataset items: about 92% accurate on the 73% answered at confidence 0.9 or above | Single-developer corpora; "not a portable benchmark" |
| [anisselbd/jev-phishing-bench](https://github.com/anisselbd/jev-phishing-bench/blob/main/results/report.md), summarized by [Rajesh Beri](https://www.beri.net/article/typesafe-jev-typed-decision-model-calibration-decomposition-shadow-eval) (2026-09-17 to 20) | 2,000 phishing emails against Haiku. Headline: one question on all 2,000. Control 2: five questions per model, combined by a logistic model fitted on 1,000 and tested on the other 1,000 | One question: 62.6% vs Haiku 81.3% (all 2,000). Five questions combined: 95.0% vs Haiku 93.2% on the 1,000 held out, not significant (p = 0.063) | Synthetic bodies; labels from URL-reputation feeds; both combiners are trained |
| [AnthusAI/Jev-Calibration](https://github.com/AnthusAI/Jev-Calibration) (a fork is at keduseworku/Jev-Calibration) | Sentiment on jev-1.13.0: 8,801 items for raw confidence bands, 3,521 held out for calibration | Raw confidence, meaning the probability of the predicted label, was weak below 0.95. Isotonic calibration cut the ECE of Noul P(positive) from 0.117 to 0.008 on the 3,521 held out | One task; the constructed dataset gives neutral items arbitrary labels |

A separate finding, from
[scienthoon/jev-ood-calibration](https://github.com/scienthoon/jev-ood-calibration): Jev
answered confidently on questions the state could not answer.

What these agree on, and what it means for us:

- **Consistency is the clearest advantage.** Repeat variance was far lower than LLM judges.
- **Accuracy depends on the task and on decomposition.** One vague question can lose to a
  small LLM; several narrow questions, combined with weights fitted on labels, can match it.
  This supports our "atomic questions, keep math in code" rules.
- **Confidence needs calibration per question type**, measured on labeled, held-out data. This
  supports our rule that nothing enforces on an uncalibrated threshold.
- **Jev answers confidently even when the state cannot answer the question.** This supports
  our evidence-sufficiency check before inference.

## Example ideas for our README, ranked

Offline ideas can ship now; the others need a live key under the repository's call cap and
our own labels.

1. **Show one decision receipt, annotated** (digest, fingerprint, pinned model, full
   distribution, `uncalibrated`, `applied`). Offline.
2. **"Tests pass" without a test run**: code records whether a test command ran and its exit
   code, puts the facts in state, and the postflight-verify question judges the agent's claim.
   A relatable failure, and it shows keeping facts in code. Offline with a mock answer.
3. **A short recording of the agent-conversation examples**, captioned with what is real.
   Offline.
4. **An attributed "what others measured" section** linking the table above, with every
   caveat. It gives the token and cost story substance without claiming it as ours.
   Needs owner sign-off.
5. **Batched questions timing** (three gate questions in one request against three
   requests). Needs a live key.
6. **A shadow-mode report with our own calibration** on labeled fixtures. Needs a key and
   labels; this is the evidence that would eventually justify enforce.

## Leads not yet read

rotecodefraktion, ayautomate, pravvich (dev.to, with a linked repo), the LangChain harness
blog, [AbdelStark/awesome-typesafe-jev](https://github.com/AbdelStark/awesome-typesafe-jev)
(links independent evaluations such as JevBench and Jevals), and new projects
[y0usaf/pi-jev](https://github.com/y0usaf/pi-jev) and
[evoke-build/evoke](https://github.com/evoke-build/evoke).
