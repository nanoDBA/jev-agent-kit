# jev_agent_kit

Give your coding agent a second opinion on risky tool calls, from a model that answers in
probabilities instead of prose.

jev_agent_kit connects Claude Code, Codex, and Hermes Agent to
[TypeSafe's Jev](https://typesafe.ai), a model that answers typed questions (yes/no, pick one,
rate on a scale) with probability distributions. Before your agent runs a shell command, the
kit asks Jev whether it is destructive, whether it sends data out, and whether it widens
permissions. It records the answers. Your agent's own permission rules still decide; a Jev
answer never grants permission.

> **Status: early and unarmed.** Hooks run in shadow mode by default: they record evidence
> and change nothing. The shipped question sets are not calibrated, so no answer can approve
> anything yet. Use it to collect and measure evidence, not as a guard.

## See it work (offline)

After [getting the code](#get-the-code), run:

```sh
python examples/gate_walkthrough.py
```

Your agent wants to run `rm -rf ./build`. The hook turns the tool call into a small state:

```json
{
  "command": "rm -rf ./build",
  "target": "/home/alice/private-repo/build",
  "context": "tool=Bash; description=Clean the build folder; cwd=/home/alice/private-repo"
}
```

Before anything leaves your machine, the kit reduces it. This is what Jev receives:

```json
{
  "command": "rm",
  "context": "tool=Bash; description=Clean the build folder; cwd=/home/alice/private-repo",
  "target": "id_7dcb134b1ad03cd1"
}
```

The command is cut to the program name and the path becomes a keyed hash. The `context` line
is sent as written, including the folder name; see [What leaves your machine](#what-leaves-your-machine).

Jev answers each question with the probability that the answer is yes, and the kit turns each
answer into a route:

```text
destructive        p(yes)=0.97  -> ask
exfiltrates        p(yes)=0.02  -> ask
widens_permission  p(yes)=0.01  -> ask
```

The example uses a scripted mock instead of the real model, so every route is `ask`: a mock
answer can never approve anything, and no threshold has been calibrated yet. Once thresholds
are measured, a gate that clears would route to `accept`, and one that does not would stay at
`ask`.

| Route | Meaning |
| --- | --- |
| `accept` | The evidence cleared a threshold measured on real, labeled data. |
| `ask` | A gate was not cleared, or something failed. A human or a deterministic check decides. |
| `no_advice` | An advisory question produced nothing usable. Carry on without it. |

## What your agent sees

In shadow mode the hook returns no decision, so every host behaves exactly as before. In
enforce mode, a gate that is not cleared looks like this:

| Host | The hook returns | What happens |
| --- | --- | --- |
| Claude Code | `{"hookSpecificOutput": {"permissionDecision": "ask", ...}}` | Claude Code asks you to confirm the command. |
| Codex | `{"hookSpecificOutput": {"permissionDecision": "deny", ...}}`, exit code 2 | Codex blocks the call and shows the reason. |
| Hermes | `{"action": "block", "message": "..."}` | Hermes blocks the call. |

When every gate clears, the hook still returns no decision (`{}` or `None`). It never approves
a tool call by itself.

## Ask Jev your own question

The gate is one use. Any narrow, repeated decision in your agent loop can be a typed question.
[`examples/route_request.py`](examples/route_request.py) asks which handler should take a
request, using the shipped `preflight-route` set:

```json
"route": {
  "type": "choice",
  "instructions": "Which handler class should take `request`?",
  "criteria": {
    "deterministic": "a fixed rule or lookup can answer it",
    "specialist_llm": "needs open-ended reasoning or generation",
    "human": "high consequence or ambiguous, needs a person"
  },
  "kit": {"consequence": "advisory"}
}
```

Your code gets a probability for every option and decides what to do with it:

```python
rec = decide(request, transport=transport)["records"][0]
if rec["route"] == "accept":
    handler = rec["label"]        # evidence cleared a calibrated threshold
else:
    handler = "specialist_llm"    # your normal path
```

```text
request:       "What is the HTTP status code for 'Not Found'?"
distribution:  {'deterministic': 0.82, 'specialist_llm': 0.15, 'human': 0.03}
route:         no_advice  (mock=True)
handled by:    specialist_llm
```

Jev has three question types. A Noul is a yes/no question and Jev returns p(yes). A Choice
picks one of a fixed set and returns a probability per option. A Score rates on a defined
scale and returns a distribution over its levels. To try Jev by hand, use the
[console playground](https://console.typesafe.ai/playground); the API is documented at
[docs.typesafe.ai](https://docs.typesafe.ai/api.md).

## Get the code

Before the first release, the package and these docs live on the development branch, not on
`main`. The repository is currently private, so you need read access to clone it.

```sh
git clone --branch phase-0-hardening https://github.com/nanoDBA/jev_agent_kit.git
cd jev_agent_kit
python -m pip install -e .
```

You need Python 3.11 or later; the runtime uses only the standard library. Once the package
reaches `main`, drop `--branch phase-0-hardening`.

## Add it to your agent

Each guide starts in shadow mode:

- [Claude Code](docs/guides/claude-code.md): a `PreToolUse` hook in `settings.json`.
- [Codex](docs/guides/codex.md): a `PreToolUse` hook in `hooks.json` or `config.toml`.
- [Hermes Agent](docs/guides/hermes.md): a small plugin that registers `pre_tool_call`.

The installer puts the `jev-runtime` skill into each host's skill folder. It shows what it
would do unless you pass `--apply`:

```sh
jev-kit install --scope user            # dry run
jev-kit install --scope user --apply    # ~/.claude/skills, ~/.agents/skills, ~/.hermes/skills
```

Where the system allows it, the installer creates a symbolic link to `skills/jev-runtime` in
your checkout and falls back to copying. A linked install breaks if you move or delete the
checkout.

## Shadow and enforce

| Mode | What the hook does | When to use it |
| --- | --- | --- |
| `shadow` (default) | Records evidence and returns no decision. | The normal mode. |
| `enforce` | A gate that is not cleared stops the call: Claude Code asks you to confirm, Codex denies it, Hermes blocks it. | Optional. Only with thresholds measured on labeled, held-out data, and with approval from whoever owns the agent's environment. |

With the uncalibrated sets that ship today, `enforce` asks about or stops every matched tool
call. That is intended: nothing is trusted until it has been measured.

## Recording real evidence (optional)

Without an API key the hooks still run, fail closed internally, and stay silent in shadow.

Live calls send data to TypeSafe. Only set them up with authorization to send that data,
after reading [What leaves your machine](#what-leaves-your-machine).

| Variable | Purpose |
| --- | --- |
| `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND` | Your Jev API key, or a command (a JSON argument list) that prints it, for example from a secret manager. |
| `JEV_KIT_HMAC_KEY` | 32 random bytes, base64url. The `target` field (a file path or URL) is sent as a keyed hash. |
| `JEV_KIT_SOURCE_ALLOWLIST` | Set to `agent_context` to allow the gate's `context` field. This is an opt-in to sending plain text; see below. |
| `JEV_KIT_ATTESTATION` | Path to a JSON file confirming TypeSafe is in your data-flow inventory, for example `{"inventory": true, "inventory_date": "2026-09-26", "dpa": true}`. Live calls are refused without it. |
| `JEV_KIT_RECEIPTS_DIR` | Optional absolute path for receipts. The default is a per-user data folder. |

Generate an HMAC key with:

```sh
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Every decision is written to a JSONL receipt. Calibration needs real model outputs collected
with authorization, independent labels, and validation on held-out data; mock receipts are
for demonstration only.

## What leaves your machine

Only fields declared in the question set are sent, each transformed first. For the tool-call
gate:

| Field | What is sent |
| --- | --- |
| `command` | The program name only. `rm -rf ./build` is sent as `rm`. |
| `target` | A keyed hash of the file path or URL, never the path itself. |
| `context` | Plain text. Claude Code and Codex send the tool name and the agent's working directory (for example `tool=Bash; cwd=/home/alice/private-repo`), plus the tool's description (Claude Code) or turn id (Codex). Hermes sends its task id. Email addresses, IP addresses and phone numbers are masked; folder names are not. |

The `context` field is sent only if you add `agent_context` to `JEV_KIT_SOURCE_ALLOWLIST`.
The gate currently always includes it, so without that opt-in every live request is blocked
before sending and you collect no evidence. Decide whether your directory names may leave the
machine before you opt in.

Before sending, the kit scans the exact request bytes and blocks the whole request if they
match its credential detectors (API keys, tokens, passwords, connection strings, card
numbers). Detection is pattern-based and cannot catch every secret.

Receipts never contain your API key or the raw state. The full policy is in
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
| `examples/` | Runnable offline examples, sample hook events, host config snippets |
| `docs/guides/` | Per-host setup guides |
| `skills/jev-runtime/` | The agent skill and its question sets |
| `src/jev_kit/` | The engine, egress checks, receipts, and host hook shims |
| `docs/adr/`, `docs/specs/` | Design decisions and specifications |
| `CLAUDE.md` | Rules for agents working on this repository |

## Development

```sh
python -m pytest
python -m ruff check src tests scripts tools
python -m mypy
```

Tests never call the live API.
