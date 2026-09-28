# 11. Live answer shape: rounding in real Jev responses

Our own measurement, 2026-09-28, model `jev-1.13.0`. First live calls from this project.

## Why

Until now every test used scripted mock answers. The first live smoke run passed for Noul
and Choice but rejected a Score answer as `answer_invalid`. The validator required the
reported score to equal the probability-weighted mean within `1e-6`. Live answers are
rounded, so that check rejected valid answers at random.

## Method

- Direct calls through the kit's live transport, with synthetic, non-personal state
  (five short sentences such as "User says the page loads slowly on mobile.").
- 30 calls: a three-level Score, a five-level Score and a four-option Choice, ten each. Then
  12 more Choice calls with the raw answer recorded, and one Score call through the full
  engine with the raw body recorded. 43 calls in all, each run under a hard cap.
- For each answer: the probability sum, the number of decimals, the gap between the reported
  score and the weighted mean of the reported probabilities, and whether the chosen option
  had the highest reported probability.

## Results

| Check | Observed |
| --- | --- |
| Probabilities sum to 1 | 42 of 42 answers sum to exactly 1.0 |
| Decimals reported | Two (one answer had a float artifact, 0.41000000000000003) |
| Score minus weighted mean, 3 levels | Up to 0.01 in either direction; 6 of 10 nonzero |
| Score minus weighted mean, 5 levels | Up to 0.02; 6 of 10 nonzero |
| Chosen option is the top reported probability | 21 of 22 Choice answers |
| Choice `confidence` equals the top probability | 0 of 22 (for example top 0.59, confidence 0.46) |

With the old tolerance, 12 of 20 Score answers were rejected. The one Choice answer whose
chosen option was not the top reported probability is a different matter: rounding
preserves order, so it cannot turn the true top option into a trailing one. That answer
was inconsistent as returned, and the validator rejects it by design.

## What we changed

- The Score check allows the gap that rounding to two decimals can produce: half a step on
  the score plus the worst case for the mean. Probability errors are at most half a step
  each and sum to zero, so the mean error is at most half a step times `floor(n^2 / 4)` for
  `n` levels. The bound is `0.005 * (1 + floor(n^2 / 4))`: 0.015 for three levels and 0.035
  for five, above the 0.01 and 0.02 observed. (An earlier draft used `0.005 * n`, which is
  too tight: an honest five-level answer can differ by 0.03.)
- The Choice argmax check is unchanged and exact (ties allowed). A non-top choice is
  rejected; accepting it could let routing act on a label the distribution does not favor.
- The probability-sum check is unchanged: live sums were exact.
- Choice `confidence` is a separate value from the top probability. The validator already
  treats it only as a number in [0, 1]; nothing depends on it matching.

Contradictions beyond these bounds are still rejected, and tests cover both sides.

## Limits

One model version, one day, synthetic sentences, 43 calls. If TypeSafe changes the rounding,
these bounds need to change with it; the tests pin the recorded answers.
