# Hermes Agent

Adds a Hermes plugin that registers a `pre_tool_call` hook and asks Jev about each tool
call. It starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

```sh
git clone https://github.com/nanoDBA/jev_agent_kit.git
cd jev_agent_kit
python -m pip install -e .
```

Install into the same Python environment Hermes runs in, so the plugin can import
`jev_kit`.

## 2. Add the plugin

Copy the example plugin into your Hermes plugins folder:

```sh
cp -r examples/hosts/hermes/jev-gate ~/.hermes/plugins/jev-gate
```

It is two files. `plugin.yaml`:

```yaml
name: jev-gate
description: Ask Jev whether a tool call looks destructive, exfiltrating, or permission-widening
version: 0.1.0
```

and `plugin.py`:

```python
from jev_kit.hooks.hermes import register as _register_gate


def register(ctx):
    _register_gate(ctx)
```

Enable it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - jev-gate
```

## 3. Configure it

The plugin reads its settings from the environment when Hermes loads it:

| Variable | Value |
| --- | --- |
| `JEV_KIT_HOOK_MODE` | `shadow` (default) or `enforce` |
| `JEV_KIT_HOOK_QUESTION_SET_PATH` | Absolute path to `skills/jev-runtime/questions/tool-call-gate.json` |

Set the question-set path. Without it the plugin looks for the file relative to Hermes'
working directory.

This variable name is not the one the Claude Code and Codex hooks use: they read
`JEV_KIT_QUESTION_SET_PATH` or take `--question-set-path`.

## 4. Try it without Hermes

```sh
JEV_KIT_HOOK_QUESTION_SET_PATH="$PWD/skills/jev-runtime/questions/tool-call-gate.json" \
python - <<'EOF'
import sys
sys.path.insert(0, "examples/hosts/hermes/jev-gate")
import plugin

hooks = {}
class Ctx:
    def register_hook(self, name, fn):
        hooks[name] = fn

plugin.register(Ctx())
print(hooks["pre_tool_call"](tool_name="terminal", args={"command": "rm -rf ./build"}, task_id="t1"))
EOF
```

Shadow mode prints `None`, which lets the call proceed. With `JEV_KIT_HOOK_MODE=enforce`
and no API key it prints:

```text
{'action': 'block', 'message': 'jev-kit gate: send to human approval. (reason: config)'}
```

## What Hermes sees

| Situation | Callback returns | Effect |
| --- | --- | --- |
| Shadow mode, any result | `None` | The call proceeds. |
| Enforce, every gate cleared | `None` | The call proceeds under your usual Hermes rules. |
| Enforce, a gate not cleared, or any failure | `{"action": "block", ...}` | Hermes blocks the call. |

The plugin answers within its own 8-second deadline, well inside Hermes' 30-second hook
timeout. That matters because Hermes treats a timeout as a block.

Known limitation: in enforce mode the plugin returns Hermes' `block` action, so the call is
stopped rather than sent for approval, even though the message says "send to human approval".
Hermes also has an `approve` action that asks for approval. Switching to it is tracked as a
follow-up.

## 5. Record real evidence

Set the variables in [Recording real evidence](../../README.md#recording-real-evidence-shadow)
in the environment Hermes starts from, and stay in shadow mode until you have measured
thresholds on the receipts.

## Troubleshooting

- **`ModuleNotFoundError: jev_kit`.** Hermes runs a different Python. Install the package
  into that environment.
- **Everything is blocked in enforce.** Expected with the uncalibrated question sets. Unset
  `JEV_KIT_HOOK_MODE` to return to shadow.
