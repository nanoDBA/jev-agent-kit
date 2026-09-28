# 11. Live answer shape: rounding in real Jev responses

Our own measurement, 2026-09-28, model `jev-1.13.0`. First live calls from this project.

## Why

Until now every test used scripted mock answers. The first live smoke run passed for Noul
and Choice but rejected a Score answer as `answer_invalid`. The validator required the
reported score to equal the probability-weighted mean within `1e-6`, and the chosen Choice
option to be the top probability within `1e-9`. Live answers are rounded, so those checks
reject valid answers at random.

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

With the old tolerances, 12 of 20 Score answers and 1 of 22 Choice answers would have been
rejected.

## What we changed

- The Score check allows the gap that rounding to two decimals can produce: half a step on
  the score plus half a step per level index, `0.005 * levels`. That is 0.015 for three
  levels and 0.025 for five, above the 0.01 and 0.02 observed.
- The Choice check allows the chosen option to trail the top reported probability by at
  most one step (0.01). Two options within half a step of each other before rounding can
  end up one step apart after it.
- The probability-sum check is unchanged: live sums were exact.
- Choice `confidence` is a separate value from the top probability. The validator already
  treats it only as a number in [0, 1]; nothing depends on it matching.

Contradictions beyond these bounds are still rejected, and tests cover both sides.

## Limits

One model version, one day, synthetic sentences, 43 calls. If TypeSafe changes the rounding,
these bounds need to change with it; the tests pin the recorded answers.
