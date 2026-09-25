# 01. Field guide principles (paraphrased)

Summary of the independent 12-page guide, in our own words. These are the design rules the
guide argues for; the non-negotiables in `CLAUDE.md` are derived from them.

## Division of labor

- The LLM generates and proposes. Jev judges narrow, typed questions. Code enforces. The
  harness decides what happens next. No component is both judge and executor.
- Jev is a sensor, not a supervisor. The guide explicitly argues against using Jev as a
  manager that orchestrates several LLMs; the program owns the workflow.

## Designing decisions

- Surface hidden decisions. Every implicit "should I..." inside an agent loop is a
  candidate typed question.
- Build a compact state packet: current goal, relevant facts, evidence, constraints,
  available actions. Do not dump the transcript.
- Treat questions as contracts: enumerate outcomes, define what each means, include an
  escape option (for example "needs clarification"), and version the contract like code.
- Build the option menu from live state: only tools, files, workers and actions that exist
  and are allowed right now. Deterministic policy removes impossible or forbidden options
  before any semantic routing.
- Ask independent questions together against one immutable snapshot of state.

## Acting on answers

- Route by confidence and by consequence: automate confident, low-consequence internal
  work; gather more evidence in the middle band; send dangerous actions to a human.
- A missing, failed or malformed decision is not approval. Never substitute an invented
  default that looks like a model answer.
- Retrieval should be decision-aware: deterministic filters first, Jev keeps what matters,
  the LLM gets the smallest useful context.

## Operating it

- Record a receipt for every decision: state, contract version, full distribution,
  threshold, selected route, resulting action.
- Deploy in shadow mode first, compare against the existing agent, measure calibration,
  then automate the safest branch and widen gradually.
