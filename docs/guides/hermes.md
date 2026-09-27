# Hermes Agent

Adds a Hermes plugin that registers a `pre_tool_call` hook and asks Jev about each tool
call. It starts in shadow mode: evidence is recorded and nothing about your session changes.

## 1. Install the package

Follow [Get the code](../../README.md#get-the-code) in the README. Install into the same
Python environment Hermes runs in, so the plugin can import `jev_kit`.

## 2. Add the plugin

Installing the plugin changes how your agent runs tool calls, even in shadow mode. Do it only
with approval from whoever owns that environment. The offline check in the "Try it" step needs
no registration.

Copy the example plugin into your Hermes plugins folder:

```sh
cp -r examples/hosts/hermes/jev-gate ~/.hermes/plugins/jev-gate
```

A Hermes directory plugin is a `plugin.yaml` manifest plus an `__init__.py` that defines
`register(ctx)`. `plugin.yaml`:

```yaml
name: jev-gate
description: Ask Jev whether a tool call looks destructive, exfiltrating, or permission-widening
version: 0.1.0
```

`__init__.py`:

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

This loads the plugin folder as a package, the way Hermes does, and calls the hook once:

```sh
JEV_KIT_HOOK_QUESTION_SET_PATH="$PWD/skills/jev-runtime/questions/tool-call-gate.json" \
python - <<'EOF'
import importlib.util
from pathlib import Path

folder = Path("examples/hosts/hermes/jev-gate")
spec = importlib.util.spec_from_file_location(
    "jev_gate", folder / "__init__.py", submodule_search_locations=[str(folder)]
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

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
{'action': 'approve', 'message': 'jev-kit gate: send to human approval. (reason: config)'}
```

## What Hermes sees

| Situation | Callback returns | Effect |
| --- | --- | --- |
| Shadow mode, any result | `None` | The call proceeds. |
| Enforce, every gate cleared | `None` | The call proceeds under your usual Hermes rules. |
| Enforce, a gate not cleared, or any failure | `{"action": "approve", ...}` | Hermes asks you. With no one to ask (a non-interactive run), a timeout or a denial, it blocks the call. |

Hermes treats a hook that exceeds its 30-second timeout as a block. The hook waits at most
about 8 seconds for the engine's answer: a worker timeout, backed by a watchdog that kills the
engine's child process. If that budget runs out, the hook answers fail-closed without waiting
further, and a worker still finishing keeps running in the background. Loading the engine also
comes on top of the budget. That still leaves a wide margin under 30 seconds.

The plugin sends no `rule_key`, so each call is approved on its own. If another plugin
returns `block` for the same call, Hermes blocks it.

## 5. Record real evidence (optional)

Live calls send data to TypeSafe, so only do this with authorization to send it. Read
[What leaves your machine](../../README.md#what-leaves-your-machine) first, then set the
variables in [Recording real evidence](../../README.md#recording-real-evidence-optional) in the
environment Hermes starts from. Stay in shadow mode: enforce needs thresholds measured on
labeled, held-out data and approval from whoever owns the environment.

## Troubleshooting

- **The plugin does not load.** Check that the folder contains `plugin.yaml` and
  `__init__.py`, and that `jev-gate` is listed under `plugins.enabled`.
- **`ModuleNotFoundError: jev_kit`.** Hermes runs a different Python. Install the package
  into that environment.
- **Everything is blocked in enforce.** Expected with the uncalibrated question sets. Unset
  `JEV_KIT_HOOK_MODE` to return to shadow.
