# jev_agent_kit

Give your coding agent a second opinion on risky tool calls, from a model that answers in
probabilities instead of prose.

jev_agent_kit connects Claude Code, Codex, and Hermes Agent to
[TypeSafe's Jev](https://typesafe.ai), a typed-decision model. Before your agent runs a shell
command, the kit asks Jev three yes/no questions (is it destructive, does it send data out,
does it widen permissions) and records the answers. Your code and your agent's normal
permission rules still make the final call; a Jev answer never grants permission.

> **Status: early and unarmed.** The hooks run in shadow mode by default, where they record
> evidence and change nothing. The shipped question sets are not calibrated yet, so no Jev
> answer can approve anything on its own. Use it to collect and measure evidence, and do not
> rely on it as a guard yet.

## What Jev is

Most models answer questions in text. Jev answers typed questions with numbers:

| Question type | You ask | Jev returns |
| --- | --- | --- |
| Noul | a yes/no question | the probability that the answer is yes |
| Choice | pick one of a fixed set of options | a probability for each option |
| Score | rate something on a defined scale | a distribution over the scale's levels |

You send a small state (for example `{"command": "rm -rf ./build"}`) and a set of questions.
Jev sends back distributions. To try Jev by hand, use the
[console playground](https://console.typesafe.ai/playground); the API is documented at
[docs.typesafe.ai](https://docs.typesafe.ai/api.md).

The kit turns each answer into one of three routes:

| Route | Meaning |
| --- | --- |
| `accept` | The evidence cleared a threshold that was measured on real data. |
| `ask` | A gate question was not cleared, or anything failed. A human or a deterministic check decides. |
| `no_advice` | An advisory question produced nothing usable. Carry on without it. |

Even `accept` only means the evidence cleared a bar. The agent's own permission flow still
applies, and a hook built on this kit never approves a tool call by itself.

## Quick start (offline, no API key)

You need Python 3.11 or later. The runtime uses only the standard library.

```sh
git clone https://github.com/nanoDBA/jev_agent_kit.git
cd jev_agent_kit
python -m pip install -e .
python examples/first_decision.py
```

The example runs the shipped tool-call gate against a scripted mock model:

```text
status: ok
  destructive        route=ask  p(yes)=0.97  threshold=uncalibrated  mock=True
  exfiltrates        route=ask  p(yes)=0.02  threshold=uncalibrated  mock=True
  widens_permission  route=ask  p(yes)=0.01  threshold=uncalibrated  mock=True
receipt: .../jev-receipts-xxxx/20260926T215655Z-34616.jsonl
```

Every route is `ask`, even where the mock answered a confident "no": a mock answer is
labelled `mock=True` and can never approve anything, and no threshold has been measured yet. The receipt file is a JSONL record of each decision, and it
is what you would later use to calibrate.

## Add it to your agent

Each guide starts in shadow mode:

- [Claude Code](docs/guides/claude-code.md): a `PreToolUse` hook in `settings.json`.
- [Codex](docs/guides/codex.md): a `PreToolUse` hook in `hooks.json` or `config.toml`.
- [Hermes Agent](docs/guides/hermes.md): a small plugin that registers `pre_tool_call`.

The installer copies the `jev-runtime` skill into each host's skill folder. It shows what it
would do unless you pass `--apply`:

```sh
jev-kit install --scope user            # dry run
jev-kit install --scope user --apply    # ~/.claude/skills, ~/.agents/skills, ~/.hermes/skills
```

## Shadow and enforce

| Mode | What the hook does | When to use it |
| --- | --- | --- |
| `shadow` (default) | Records evidence and returns no decision. Your agent behaves exactly as before. | Always, until you have calibrated thresholds. |
| `enforce` | A gate that is not cleared becomes a human-approval step (Claude Code `ask`, Codex `deny`, Hermes `block`). | Only after measuring thresholds on your own traffic. |

With the uncalibrated sets that ship today, `enforce` asks about every matched tool call. That
is intended: nothing is trusted until it has been measured.

## Recording real evidence (shadow)

Without an API key the hooks still run, fail closed internally, and stay silent in shadow. To
make real Jev calls and collect receipts, set:

| Variable | Purpose |
| --- | --- |
| `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND` | Your Jev API key, or a command (a JSON argument list) that prints it, for example from a secret manager. |
| `JEV_KIT_HMAC_KEY` | 32 random bytes, base64url. Identifiers such as file paths are sent as keyed hashes, never in plain text. |
| `JEV_KIT_SOURCE_ALLOWLIST` | Set to `agent_context` so the gate may send a short tool-call context string. Free text is blocked unless its source is listed here. |
| `JEV_KIT_ATTESTATION` | Path to a JSON file confirming TypeSafe is in your data-flow inventory, for example `{"inventory": true, "inventory_date": "2026-09-26", "dpa": true}`. Live calls are refused without it. |
| `JEV_KIT_RECEIPTS_DIR` | Optional absolute path for receipts. The default is a per-user data folder. |

Generate an HMAC key with:

```sh
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

## What leaves your machine

Before any request is sent, the kit reduces it to declared fields only and scans the exact
bytes for secrets:

- Commands are cut down to the program name (`rm -rf ./build` is sent as `rm`).
- File paths and other identifiers become keyed hashes.
- Anything that looks like a credential (keys, tokens, passwords, connection strings, card
  numbers) blocks the whole request.

The receipts never contain your API key or the raw state. The full policy is in
[ADR 0002](docs/adr/0002-egress-policy.md).

## Rules the kit follows

1. Code owns authority. A Jev answer never widens a permission.
2. Gates fail closed to `ask`. Advisory questions fail open to `no_advice`.
3. A mock or fallback never looks like a model answer.
4. The model is pinned to a version (`jev-1.13.0`), and a mismatched response is rejected.
5. A threshold belongs to one question, one wording, and one model version.
6. Nothing enforces on an uncalibrated threshold.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/jev_kit/` | The engine, egress checks, receipts, and host hook shims |
| `skills/jev-runtime/` | The agent skill and its question sets |
| `examples/` | Runnable offline example, sample hook events, host config snippets |
| `docs/guides/` | Per-host setup guides |
| `docs/adr/`, `docs/specs/` | Design decisions and specifications |
| `CLAUDE.md` | Rules for agents working on this repository |

## Development

```sh
python -m pytest
python -m ruff check src tests scripts tools
python -m mypy
```

Tests never call the live API.
