# Codex

Adds a `PreToolUse` hook that asks Jev about each shell command before Codex runs it. It
starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

```sh
git clone https://github.com/nanoDBA/jev_agent_kit.git
cd jev_agent_kit
python -m pip install -e .
```

Use the same Python that Codex will find on your `PATH`.

## 2. Register the hook

Codex reads hooks from `~/.codex/hooks.json`, from `[hooks]` in `~/.codex/config.toml`, or
from the same files under a repository's `.codex/` folder. In `hooks.json`, replacing the
path with the absolute path to your clone:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^(shell|Bash)$",
        "hooks": [
          {
            "type": "command",
            "command": "python -m jev_kit.hooks.codex --mode shadow --question-set-path /ABSOLUTE/PATH/TO/jev_agent_kit/skills/jev-runtime/questions/tool-call-gate.json",
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
The `config.toml` equivalent:

```toml
[[hooks.PreToolUse]]
matcher = "^(shell|Bash)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = 'python -m jev_kit.hooks.codex --mode shadow --question-set-path /ABSOLUTE/PATH/TO/jev_agent_kit/skills/jev-runtime/questions/tool-call-gate.json'
statusMessage = "jev-kit: checking tool call"
timeout = 30
```

Always pass `--question-set-path` as an absolute path. Without it, the hook looks for the
question set relative to the directory Codex is working in.

On Windows, Codex can run a different command through `commandWindows`. Point it at the
same module with your Windows Python.

## 3. Try it without Codex

```sh
python -m jev_kit.hooks.codex --mode shadow \
  --question-set-path "$PWD/skills/jev-runtime/questions/tool-call-gate.json" \
  < examples/events/codex-pretooluse.json
```

Shadow mode prints `{}` and exits 0. With `--mode enforce` and no API key it prints a
`deny` and exits 2:

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

Codex skips a hook that exceeds its timeout and carries on, which would fail open. The hook
does not rely on that: it gives itself an 8-second deadline and always answers first.

## 4. Record real evidence

Set the variables in [Recording real evidence](../../README.md#recording-real-evidence-shadow)
in the environment Codex starts from, and stay in shadow mode until you have measured
thresholds on the receipts.

## Troubleshooting

- **Nothing seems to happen.** That is shadow mode. Check the receipts folder or run step 3.
- **Every shell command is denied in enforce.** Expected with the uncalibrated question sets.
  Switch back to shadow.
- **The hook never fires.** Codex tool names differ by version. Widen or remove the
  `matcher` and check which tool name appears in the event.
