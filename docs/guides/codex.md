# Codex

Adds a `PreToolUse` hook that asks Jev about each shell command before Codex runs it. It
starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

Follow [Try it in a minute](../../README.md#try-it-in-a-minute) in the README. Use the same Python that
Codex will find on your `PATH`.

## 2. Register the hook

Registering the hook changes how your agent runs tool calls, even in shadow mode. Do it only
with approval from whoever owns that environment. The offline check in the "Try it" step needs
no registration.

Codex reads hooks from `~/.codex/hooks.json`, from `[hooks]` in `~/.codex/config.toml`, or
from the same files under a repository's `.codex/` folder. Pick one of the two formats for a
given folder: if both define hooks, Codex merges them and warns at startup.

`hooks.json`, with the path replaced by the absolute path to your clone:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^(shell|Bash)$",
        "hooks": [
          {
            "type": "command",
            "command": "python -m jev_kit.hooks.codex --mode shadow --question-set-path \"/ABSOLUTE/PATH/TO/jev-agent-kit/skills/jev-runtime/questions/tool-call-gate.json\"",
            "statusMessage": "jev-kit: checking tool call",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The same snippet is in [`examples/hosts/codex/hooks.json`](../../examples/hosts/codex/hooks.json).
Or the `config.toml` equivalent:

```toml
[[hooks.PreToolUse]]
matcher = "^(shell|Bash)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = 'python -m jev_kit.hooks.codex --mode shadow --question-set-path "/ABSOLUTE/PATH/TO/jev-agent-kit/skills/jev-runtime/questions/tool-call-gate.json"'
statusMessage = "jev-kit: checking tool call"
timeout = 30
```

Keep the quotes around the path so a folder name with spaces stays one argument, and always
use an absolute path. Without `--question-set-path`, the hook looks for the question set
relative to the directory Codex is working in.

On Windows, Codex can run a different command through `commandWindows`. Point it at the
same module with your Windows Python.

## 3. Trust the hook

Codex skips a new or changed hook until you review and trust it. Start Codex, run `/hooks`,
review the jev-kit hook, and trust it. Codex records trust against the hook's exact
definition, so editing the command means trusting it again.

A hook in a repository's `.codex/` folder also needs that project to be trusted. Trust the
hook yourself; do not bypass the review.

## 4. Try it without Codex

```sh
python -m jev_kit.hooks.codex --mode shadow \
  --question-set-path "$PWD/skills/jev-runtime/questions/tool-call-gate.json" \
  < examples/events/codex-pretooluse.json
```

Shadow mode prints `{}` and exits 0. With `--mode enforce` and no API key it prints a
`deny`, writes the reason to stderr, and exits 2:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "config"}}
```

## What Codex sees

| Situation | Hook output | Exit code | Effect |
| --- | --- | --- | --- |
| Shadow mode, any result | `{}` | 0 | No decision. Codex continues as usual. |
| Enforce, every gate cleared | `{}` | 0 | No decision. The hook never approves on its own. |
| Enforce, a gate not cleared, or any failure | `permissionDecision: "deny"` | 2 | Codex blocks the call and shows the reason. |

Codex has no "ask" decision for hooks, so a gate that needs a human becomes `deny`. You can
then run the command yourself or approve it another way.

Codex skips a hook that exceeds its timeout and carries on, so a slow hook fails open. The hook
waits at most about 8 seconds for the engine's answer: a worker timeout, backed by a watchdog
that kills the engine's child process. If that budget runs out, the hook answers fail-closed
without waiting further, but a worker still finishing can delay the hook process exiting.
Python startup and reading the event also come on top of the budget. Keep the hook `timeout`
well above 8 seconds; 30 is a reasonable start.

## 5. Record real evidence (optional)

Live calls send data to TypeSafe, so only do this with authorization to send it. Read
[What leaves your machine](../../README.md#what-leaves-your-machine) first, then set the
variables in [Live calls](live-calls.md) in the
environment Codex starts from. Stay in shadow mode: enforce needs thresholds measured on
labeled, held-out data and approval from whoever owns the environment.

## Troubleshooting

- **The hook never runs.** Check `/hooks` first: an untrusted or changed hook is skipped. For
  a repository-level hook, check that the project is trusted. Then check the `matcher`: Codex
  tool names differ by version, so widen it and look at the tool name in the event.
- **Nothing seems to happen.** In shadow mode that is expected. Check the receipts folder or
  run step 4.
- **Every shell command is denied in enforce.** Expected with the uncalibrated question sets.
  Switch back to shadow.
