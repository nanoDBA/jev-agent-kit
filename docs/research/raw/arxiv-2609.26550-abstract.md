<!-- Fetched 2026-09-25 from https://arxiv.org/abs/2609.26550 -->

Title:JEV-as-a-Judge: Accept When Confident, Escalate When Unsure

Authors:Yubo Li, Yidi Miao, Ramayya Krishnan, Rema Padman

Abstract:LLM-as-a-judge enables evaluation across diverse tasks, but inference cost and confidence reliability become critical at scale. We study whether a decision-only judge can provide an economical first pass and identify when stronger evaluation is needed. Comparing jev-as-a-judge with sixteen generative and reward-model judges, with blinded human adjudication, we find it within three percentage points of a state-of-the-art LLM judge, our strongest comparator, on ordinary preference and evidence-grounded factuality at 0.36% of the comparator's fee. Larger gaps arise when judgments require checking a derivation or resisting an elaborately written wrong answer. On several benchmarks, JEV's gap to this comparator is concentrated in low-confidence decisions. A frozen cascade that accepts confident verdicts and escalates uncertain ones retains 99% of the comparator's accuracy at lower cost.

Submission history From: Yubo Li [view email] [v1] Tue, 22 Sep 2026 15:05:56 UTC (2,824 KB)
