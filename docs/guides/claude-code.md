# Claude Code

Adds a `PreToolUse` hook that asks Jev about each shell command before Claude Code runs it.
It starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

```sh
git clone https://github.com/nanoDBA/jev_agent_kit.git
cd jev_agent_kit
python -m pip install -e .
```

Use the same Python that Claude Code will find on your `PATH`. Check that the hook module
loads:

```sh
python -c "import jev_kit.hooks.claude; print('ok')"
```

## 2. Register the hook

Add this to `~/.claude/settings.json` (all projects) or `.claude/settings.json` (one
project). Replace the path with the absolute path to your clone:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "python -m jev_kit.hooks.claude --mode shadow --question-set-path /ABSOLUTE/PATH/TO/jev_agent_kit/skills/jev-runtime/questions/tool-call-gate.json",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The same snippet is in [`examples/hosts/claude-code/settings.json`](../../examples/hosts/claude-code/settings.json).

Always pass `--question-set-path` as an absolute path. Without it, the hook looks for
`skills/jev-runtime/questions/tool-call-gate.json` relative to the directory Claude Code is
working in, which only exists inside this repository.

## 3. Try it without Claude Code

Pipe the sample event into the hook:

```sh
python -m jev_kit.hooks.claude --mode shadow \
  --question-set-path "$PWD/skills/jev-runtime/questions/tool-call-gate.json" \
  < examples/events/claude-pretooluse.json
```

In shadow mode it prints `{}`, which tells Claude Code "no decision", so the normal
permission flow continues. Run it again with `--mode enforce` and no API key set:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "config"}}
```

The engine could not make a real call, so the gate failed closed, and Claude Code would
prompt you before running the command.

## What Claude Code sees

| Situation | Hook output | Effect |
| --- | --- | --- |
| Shadow mode, any result | `{}` | No decision. Your usual permissions apply. |
| Enforce, every gate cleared | `{}` | No decision. The hook never approves on its own. |
| Enforce, a gate not cleared, or any failure | `permissionDecision: "ask"` | Claude Code asks you to confirm. |

The hook reads the command from `tool_input.command`, `script`, or `code`, so Bash and
PowerShell calls are both covered. It always exits 0 and answers within its own 8-second
deadline.

## 4. Record real evidence

Set the variables in [Recording real evidence](../../README.md#recording-real-evidence-shadow)
in the environment Claude Code starts from, then keep using Claude Code as normal. Receipts
accumulate in the receipts folder. Stay in shadow mode until you have measured thresholds on
those receipts.

## Troubleshooting

- **Nothing seems to happen.** That is shadow mode working. Check the receipts folder, or
  pipe the sample event through the hook as in step 3.
- **Every command prompts in enforce.** Expected with the shipped, uncalibrated question sets.
  Switch back to `--mode shadow`.
- **`No module named jev_kit`.** Claude Code is running a different Python. Put the full path
  to the interpreter you installed into at the start of the `command` string.
