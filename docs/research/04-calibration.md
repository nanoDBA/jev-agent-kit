# 04. Calibration findings

Jev probabilities are useful for ranking and often wrong as absolute confidence. Every
threshold in this repo must be measured, fingerprinted and labeled.

## Evidence gathered (community measurements, not ours)

- Same question asked as Noul vs Choice returned 0.22 vs 0.01; a question and its negation
  can sum to 1.19. (Colonizer-dev/harness issue #239)
- A NousResearch eval found the default 0.5 threshold dropped all 851 keep-candidates while
  ranking stayed fine; absolute calibration off by roughly 2-3x. (cited in the same issue)
- Raw probabilities rank well but are miscalibrated with ties everywhere; fix with
  per-question isotonic or Platt calibration bound to a fingerprint of wording plus model,
  report from a held-out split. (keduseworku/Jev-Calibration)
- Choice "confidence" appears to be a fixed function of the top probability, so it carries
  no extra information. (awesome-jev-robustness)
- Calibration held on support routing and collapsed on random 3-SAT. (123k-request
  independent study, awesome-jev-robustness)
- An independent 8-way routing test: keeping only answers at confidence >= 0.90 raised
  accuracy from 83.8% to 95.5% while answering 70% of items. (AY Automate blog)
- Vendor's own four-workflow eval lands around 68%, close to mid-tier LLMs.

## Rules for this repo

1. Threshold key = sha256(question wording + type + option set + model version).
2. Change any part of the key and the threshold is void until re-measured.
3. Prefer relative use (rank, top-k, margin between top two) until a question is calibrated.
4. Calibrate on a labeled corpus from our own traffic (T-SQL destructiveness, log triage,
   tool-call gating), split fit/held-out by group, report ECE, Brier and coverage.
5. Useful building blocks: keduseworku/Jev-Calibration (jev_eval), sureshbujji/jev-eval-lab
   (ECE, Brier, reliability data), chayan-bit/jev-harness (held-out threshold selection that
   never activates a policy), ismaelsoilet/jev-harness (160-case corpus with replay).

## Known weak spots (vendor jaggedness page, v1.13)

Literal reading, math and counting, dates as text, indirection, context rot (accuracy falls
as state grows), adversarial content in state, contradictory instructions.
