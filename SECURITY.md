# Security policy

jev-agent-kit sits between a coding agent and its tool calls, and it decides what leaves your
machine. A bug here can leak data or weaken a safety check.

## Reporting a vulnerability

Please do not describe a security problem in a public issue or pull request.

- **If the repository's Security tab offers "Report a vulnerability"**, use it. That is
  GitHub's private vulnerability reporting, which is available once the repository is public
  and the maintainer has enabled it.
- **Otherwise**, open a public issue titled "Security contact request" that contains no
  details of the problem. The maintainer will reply with a private way to send the report.

Include what you can:

- the affected version or commit;
- the smallest input that shows the problem (a request, event, or question set);
- what happened and what you expected;
- whether the problem needs a live API call to reproduce, or shows up with the mock transport.

This is a small project maintained in spare time, with no guaranteed response time. We aim to
acknowledge a report within a week and to agree a disclosure date with you before anything is
published.

## Supported versions

The project is pre-1.0. Only the latest commit on the default branch receives fixes.

## What we consider a vulnerability

Anything that breaks one of the kit's safety rules, for example:

- **Egress bypass:** data that should be transformed or blocked reaches the outgoing request.
  This includes a credential that evades the scan through encoding, escaping, nesting, or
  formatting, and undeclared fields that get sent.
- **Secret leakage:** an API key, credential, or input-derived text appears in a receipt, a
  returned record, a log line, or a CLI error message.
- **Authority widening:** a Jev answer, a mock, or a failure leads a host hook to approve a
  tool call, or turns a gate that should ask into a silent allow.
- **Fail-open:** a timeout, crash, or malformed response lets a gate proceed when it should
  ask.
- **Calibration reuse:** a change in behavior that leaves the question fingerprint unchanged,
  so a threshold measured for one behavior is applied to another.

Out of scope: the TypeSafe service itself, the host agents (Claude Code, Codex, Hermes
Agent), and findings that need an attacker who can already change your local configuration or
files. Report those to their owners.

## Testing safely

- Use the mock transport. The examples and tests run offline, and so can a reproduction.
- Do not send other people's data, or data you are not authorized to share, to the live Jev
  API while testing.
- Do not test against infrastructure you do not own.

## How the kit is designed to fail

When a gate question fails or is not cleared, the engine routes it to `ask`, and each host
applies that its own way: Claude Code asks you to confirm, Codex denies the call, and Hermes
blocks it. Advisory questions fail open to "no advice". Shadow mode, the default, never
changes what the agent does. The egress policy is in
[ADR 0002](docs/adr/0002-egress-policy.md), and what leaves the machine is summarized in the
[README](README.md#what-leaves-your-machine).
