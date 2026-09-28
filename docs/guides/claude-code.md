# Claude Code

Adds a `PreToolUse` hook that asks Jev about each shell command before Claude Code runs it.
It starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

Follow [Quick start](../../README.md#quick-start) in the README. Use the same Python that
Claude Code will find on your `PATH`, then check that the hook module loads:

```sh
python -c "import jev_kit.hooks.claude; print('ok')"
```

## 2. Register the hook

Registering the hook changes how your agent runs tool calls, even in shadow mode. Do it only
with approval from whoever owns that environment. The offline check in the "Try it" step needs
no registration.

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
            "command": "python -m jev_kit.hooks.claude --mode shadow --question-set-path \"/ABSOLUTE/PATH/TO/jev-agent-kit/skills/jev-runtime/questions/tool-call-gate.json\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The same snippet is in [`examples/hosts/claude-code/settings.json`](../../examples/hosts/claude-code/settings.json).

Keep the escaped quotes (`\"`) around the path so a folder name with spaces stays one
argument. If your Python path has spaces, quote it the same way.

Pass `--question-set-path` as an absolute path. Without it, the hook uses `JEV_KIT_HOOK_QUESTION_SET_PATH` if it is set (the older name
`JEV_KIT_QUESTION_SET_PATH` still works, but the new name wins when both are set). With
neither, it uses the `skills/jev-runtime/questions/tool-call-gate.json` of the repository
the `jev_kit` code was loaded from, found from the module's own location and never from the
working directory. That default only exists when `jev_kit` runs from a checkout of this
repository (for example `pip install -e .`); a plain wheel install does not ship the skill
folder, so the gate then has no question set and fails the normal way: no decision in
shadow, and ask in enforce.

The question-set path must be absolute, whether it comes from `--question-set-path` or an
environment variable. A relative path is rejected, never resolved against the working
directory: the gate treats it like a missing question set (no decision in shadow, fail
closed in enforce).

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
PowerShell calls are both covered. It always exits 0.

The hook waits at most about 8 seconds for the engine's answer: a worker timeout, backed by a
watchdog that kills the engine's child process. If that budget runs out, the hook answers
fail-closed without waiting further, but a worker still finishing can delay the hook process
exiting. Python startup and reading the event also come on top of the budget. Keep the
`timeout` in your settings well above 8 seconds.

## 4. Record real evidence (optional)

Live calls send data to TypeSafe, so only do this with authorization to send it. Read
[What leaves your machine](../../README.md#what-leaves-your-machine) first, then set the
variables in [Live calls](live-calls.md) in the
environment Claude Code starts from. Receipts accumulate in the receipts folder. Stay in
shadow mode: enforce needs thresholds measured on labeled, held-out data and approval from
whoever owns the environment.

## Troubleshooting

- **Nothing seems to happen.** That is shadow mode working. Check the receipts folder, or
  pipe the sample event through the hook as in step 3.
- **Every command prompts in enforce.** Expected with the shipped, uncalibrated question sets.
  Switch back to `--mode shadow`.
- **`No module named jev_kit`.** Claude Code is running a different Python. Put the full,
  quoted path to the interpreter you installed into at the start of the `command` string.
