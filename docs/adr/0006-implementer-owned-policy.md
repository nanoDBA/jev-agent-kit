# ADR 0006: Application policy belongs to the implementer

- Status: accepted
- Date: 2026-10-13
- Decision: the owner asked for permissive guidance that leaves use of the tool to its users.

## Context

The skill told agents to ignore raw probabilities, abstain without calibration and escalate
gates only to humans or deterministic checks. Applications treated these instructions as
reasons to refuse otherwise valid uses. Development safeguards had become product policy.

## Decision

Users decide how Jev results affect their applications. Direct automation, custom cutoffs,
unthresholded selection, model-to-model escalation and custom authorization policies are
supported uses. Evaluation and calibration can inform those choices; this repository does
not require them before an application consumes an answer.

Separate the answer from the built-in route. The existing engine already returns typed
answers in shadow mode without a calibration registry. Consumers may read them directly.
The routing example demonstrates one such policy: use the returned handler label, with a
fallback for errors or an unclear answer. Its offline run simulates the same policy with
labelled recorded data.

The skill explains current interface constraints and limitations without converting them
into requirements for every application. Question sets are examples, including their choices
about which tasks go to a person. Implementers can replace them.

## Compatibility

This change does not alter `src/jev_kit`, fingerprints, existing hook settings, or registered
thresholds. The bundled threshold policy still requires calibration for `accept`; the
shipped hooks still default to shadow and do not emit affirmative approval. Those are
properties of these components, not universal rules for consuming model answers.

Schema validation, egress transforms, credential scans, receipt handling and live-client
configuration remain implemented as before. They are not silently disabled by permissive
prose. An application needing a different interface or data policy can adapt the kit or
use TypeSafe's API/SDK directly. Such an integration should document its actual behavior.

This decision supersedes application-wide prohibitions in earlier skill instructions,
charter language, and review guidance. Earlier ADRs and specs still describe the bundled
components where their behavior has not changed. It does not authorize a repository agent
to spend money, activate a host hook, or merge a PR without the user's task authorizing it.

## Consequences

Agents help implement the requested policy rather than demanding our preferred one. Docs
still identify mock answers, failures, unmeasured accuracy and component limitations. Tests
cover direct answer consumption without changing the existing routing and hook contracts.
