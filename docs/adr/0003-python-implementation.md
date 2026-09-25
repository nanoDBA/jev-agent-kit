# ADR 0003: Python is the implementation language

- Status: accepted
- Date: 2026-09-25
- Decided by: owner ("Make it Python"). The specific choices below are defaults proposed with
  that decision; change them by amending this ADR.
- Supersedes: the PowerShell platform and coding conventions in `CLAUDE.md` and the
  PowerShell wording in `docs/plan.md` and the Phase 1 spec.

## Context

The kit is meant to be a general-purpose skill for Claude Code, Codex and Hermes. PowerShell
was chosen from the owner's background, the same way database examples had crept into the
core. Hermes plugins must be Python. Claude Code and Codex hooks run any command. Python runs
the same on Windows, Linux and macOS and is the language of the official TypeSafe SDK and
most prior art.

## Decision

- **Language:** Python 3.11 or later (Hermes compatibility; `tomllib` in the standard
  library). Windows, Linux and macOS are equal targets.
- **Runtime dependencies: none.** The package uses only the standard library (HTTP through
  `urllib.request`, `hmac`, `hashlib`, `json`, `concurrent.futures`). Reasons: it installs into
  any host (hook command, Hermes plugin, CI runner) without a virtual environment, and hooks
  start fast because they run on every tool call. A runtime dependency needs a written
  justification in an ADR.
- **Development tools:** `uv` for environments, `pytest` for tests, `ruff` for linting and
  formatting, `mypy --strict` for types. These never ship as runtime dependencies.
- **The official `typesafe-sdk-python` is a reference, not a dependency** (same reasoning as
  ADR 0001: its defaults use `jev-latest`, and our layer must own validation, deadlines and
  failure semantics). It is MIT licensed; adapted code keeps its notice. An optional transport
  built on it may be added later behind the same transport seam.
- **Secrets:** the API key comes from `TYPESAFE_API_KEY`, or from `TYPESAFE_API_KEY_COMMAND`,
  a command that prints the key. The command form works with any secret store (Windows
  Credential Manager or PowerShell SecretManagement, macOS Keychain, `pass`, 1Password CLI)
  without a dependency; prior art: anasbekheit/typesafe-jev-mcp. The HMAC key for egress
  tokens uses the same two forms under its own names. Keys never appear in receipts, logs,
  errors or exception messages.
- **Shape:** one package with one public decision function, a command-line entry point that
  hooks call, and a Python installer command that links the canonical skill into each host's
  skill directory (replacing the planned `install.ps1` and `install.sh`).
- **Concurrency:** a bounded `ThreadPoolExecutor` with a shared per-run call cap and rate
  budget.
- **Mocks:** every mock result carries `is_mock=True` and `model="mock"`.
- **Existing PowerShell helper scripts** under `scripts/` (prior-art sync, transcript and X
  pulls) stay as research tooling. They are not part of the product and may be ported later.

## Consequences

- The dfinke/Jev review (`docs/research/03`) is still useful as a list of client defects to
  avoid; its PowerShell-specific items (error body location, array type extension) no longer
  apply directly.
- Windows users need Python installed; the installer must detect it and say so clearly.
- Stdlib HTTP means we implement retries, `Retry-After` handling and deadlines ourselves,
  which we wanted to own anyway.
