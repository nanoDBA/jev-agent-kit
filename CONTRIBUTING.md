# Contributing

Thanks for helping. This project is small and cautious: it sits in front of an agent's tool
calls, so correctness and safety come before features.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- For a security problem, do not describe it in a public issue. Follow [SECURITY.md](SECURITY.md).
- Read the rules in [CLAUDE.md](CLAUDE.md). They apply to human contributors as much as to
  coding agents. The short version: code owns authority, gates fail closed, nothing enforces on
  an uncalibrated threshold, and tests never call the live API.

## Setting up

Get the code as described in [the README](README.md#try-it-in-a-minute), then install the
development tools:

```sh
python -m pip install pytest ruff mypy
```

Every change must pass:

```sh
python -m pytest
python -m ruff check src tests scripts tools
python -m mypy
```

## What we look for in a change

- **Standard library only at runtime.** A new runtime dependency needs a written case in an ADR
  under `docs/adr/`. Development tools are fine.
- **Tests for behavior, not helpers.** A fix for a bypass or leak needs a regression test built
  from the actual counterexample, run through a public path such as `decide()`, a hook shim, or
  the CLI. Add a clean control too, so the fix is not simply blocking everything.
- **No new suppressions.** Avoid `type: ignore` and `noqa`. If one is unavoidable, explain why
  on the same line.
- **Docs stay honest.** If you change behavior a guide or example shows, update it.
  `tests/test_docs_examples.py` runs the examples and fails when they drift.
- **Prose style:** plain language, no em dashes.

## Your contribution and the license

This project is licensed under the [MIT License](LICENSE). By submitting a contribution, you
agree that it is licensed under the same terms.

Only contribute work you have the right to submit. Do not paste code, documentation, or other
text copied from another project or vendor unless its license allows it, and say where it came
from so we can keep its notice. Short factual references and links are fine; copies of other
people's documentation are not.

## Pull requests

- Keep a pull request to one purpose.
- Describe what changed and why, and how you tested it.
- Changes to egress, receipts, host hooks, or calibration get an extra review pass. Expect
  questions, and a request for a reproduction.

## Trademarks

TypeSafe, Jev, Claude, Claude Code, Codex, and Hermes Agent are trademarks of their respective
owners. This project is independent and is not affiliated with or endorsed by them. Use those
names only to describe compatibility.
