# 00. Context and provenance

Research conducted 2026-09-25 in a Claude chat session, then moved into this repo.

## What Jev is

TypeSafe's "System One" model, launched 2026-09-15. Input: a state (JSON or string) plus
typed questions. Output: typed answers with probabilities. Three question types:

| Type | Returns |
| --- | --- |
| Choice | One of a supplied option set, with a probability per option |
| Score | A level on an ordered rubric (2 to 10 levels), with a probability per level |
| Noul | Probability of "yes" (Bernoulli) |

Multiple independent questions in one request are evaluated together against the same
state. Official docs: docs.typesafe.ai. Official GitHub org: `typesafe-ai`.

## What was digested

| Source | Status |
| --- | --- |
| 12-page independent field guide PDF ("how to use Jev with LLMs") | Read in full. Summary in `01-pdf-principles.md`. It is an independent guide, not published by TypeSafe, despite X posts crediting the founder. |
| X posts by @0xwhrrari (status 2102020016539324501, 2103157617715491278) | Not read directly (X blocks fetching). A sibling thread (2102382523879678023) was recovered via search; its 10 steps map almost 1:1 onto the PDF. The account mostly posts engagement bait; treat as PDF summaries. |
| YouTube D-Z5HnLW_ho, "Every Jev Concept Explained (use with Claude)" | Not transcribed yet. Channel promotes skool.com/scrapes. |
| YouTube L2K__oshGds, "Jev + Claude Will Change How You Work Forever (Real Use Cases)" | Not transcribed yet. Ben AI channel. |
| ~30 GitHub repos | 17 cloned and source-read, the rest README-level. See `02-prior-art.md` and `sources.lock.json`. |

To finish the gaps: run `scripts/Get-VideoTranscripts.ps1` and `scripts/Get-XThread.ps1`,
drop outputs in `raw/`, and write the delta (only what is new) into `07-videos-and-x.md`.
