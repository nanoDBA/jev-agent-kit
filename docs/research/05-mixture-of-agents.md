# 05. Mixture of agents: where Jev fits

## Findings

- Mixture-of-Agents (Wang et al., ICLR 2025): layered proposers plus an aggregator beat
  single strong models on AlpacaEval 2.0 (65.1% with open models).
- Self-MoA (arXiv 2502.00674): mixing different LLMs often lowers average quality;
  aggregating several samples from the single best model beat standard MoA by 6.6% on
  AlpacaEval 2.0 and 3.8% on average across benchmarks.
- danielgshea/jev-as-a-judge: Jev matched human pass/fail labels on all 500 decisions and
  showed far lower variance than LLM judges. Only 5 frozen agent runs sit behind those
  decisions, so low variance is the finding; accuracy is anecdotal.
- The field guide rejects Jev as a manager of LLMs.
- kerpopule/hermes-jev-skills does per-turn model routing, the only multi-model use seen.

## Design direction

- Jev is the cheap, low-variance judge between propose and aggregate: rank, filter,
  verify. The LLM still synthesizes.
- Apply one versioned postflight question set to every proposer's output so scores are
  comparable across proposers.
- Baseline to beat: N samples from one strong model plus Jev selection (Self-MoA shape).
  Only add model diversity if it wins against that baseline on our tasks.
- Route classes, not providers: Jev picks from an allowed menu of route classes; code maps a
  class to a concrete provider and model.

## Update 2026-09-25 (see `09`)

- Rao and Callison-Burch (arXiv:2609.29769): LLM judges repeat 96% of Jev's most confident
  errors, so a Jev-first cascade that defers to an LLM saves cost but gains at most 1.5 points
  (2.0 with oracle thresholds). Model diversity did not give error diversity.
- Consequence for P5: measure error correlation between Jev and each proposer or judge before
  counting a second opinion as independent evidence. Escalate uncertain gate decisions to a
  human or deterministic check, not to another model.
- browser-use/jev-ultrafast is a working "Jev decides, LLM generates" loop (`09`).
