<!-- Fetched 2026-09-25 from https://arxiv.org/abs/2609.29769 -->

Title:JEV vs. LLMs as Rubric Judges: Cheaper, Faster, and Wrong in the Same Places

Authors:Delip Rao, Chris Callison-Burch

Abstract:We ask whether Jev, a typed classifier that returns probabilities over permitted answers without generating text, can replace an LLM rubric judge. We compare it with three flash-tier LLM judges on nine panels drawn from seven benchmarks, giving every judge identical criterion texts. Jev's accuracy differs significantly from an LLM judge's in only 8 of 27 paired comparisons, ahead mostly on binary criteria and behind only on graded ones, and most of the other comparisons are inconclusive. Summed over the nine panels, the LLM judges, called once per criterion, cost 29 to 325 times as much as Jev and took 30 to 220 times as long. On graded criteria all four judges agree more with one another than with the labels and mostly assign lower levels than the raters. One of several observational accounts is that raters followed scale conventions our criterion texts omit. Jev's confidence ranks its own errors on most panels, which should make a cheap classifier the ideal first stage of a cascade that defers its uncertain verdicts to an LLM judge. Correlated errors undo that advantage. The LLM judges repeat nearly all of Jev's most confident errors, so a cascade replayed on the recorded verdicts lowers cost but gains at most 1.5 points over the best single judge with cross-fitted thresholds, and at most 2.0 even with oracle thresholds.

Submission history From: Delip Rao [view email] [v1] Thu, 24 Sep 2026 13:16:21 UTC (206 KB)
