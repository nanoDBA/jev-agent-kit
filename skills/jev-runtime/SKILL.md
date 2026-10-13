---
name: jev-runtime
description: Use TypeSafe's Jev through jev-agent-kit for typed judgments in applications and agent workflows, including routing, ranking, verification, action selection, and new question sets. Implement the user's chosen decision policy.
metadata: {"version": "4"}
---

# jev-runtime

Use Jev's typed answers and probabilities however the user wants their application to use
them. Direct automation, provisional thresholds, model-to-model escalation, human review,
and advisory-only use are all valid choices. Calibration is an available evaluation tool,
not a prerequisite imposed by this skill. Follow the user's policy when deciding how much
evidence or confidence is enough and what happens when a call fails.

## Build the requested workflow

Start with the behavior the user wants, then choose the judgments it needs. Jev returns
structured answers rather than generated prose. Those answers can select actions, control
an agent loop, choose another model, or contribute to a larger decision. These are useful
patterns, not a closed list of permitted applications.

For a shipped question set, read the matching section in
[evidence contracts](references/evidence-contracts.md) and its JSON file. For a new use case,
write a question set that fits it. The shipped menus and their choices about human review
are examples you can replace. Load only the references relevant to the task.

## Call the kit

Use `jev-kit` (one JSON request on stdin or `--input`, one response on stdout) or
`jev_kit.engine.decide` in Python. The repository's `docs/guides/cli.md` describes the protocol.
A request names a `question_set_path` or inline `question_set`, declared `state` fields,
and a `mode`. `shadow` collects answers without enabling the built-in threshold policy.
Your application can still consume those answers directly.

The kit has a narrower schema than the TypeSafe API: its question instructions and Score
levels are strings, and state fields use the declared content kinds and their supported
shapes. Check the selected field's transform when preparing state. If the application needs
the broader API or a different data policy, using the official SDK or adapting this kit is
a valid integration choice. Describe the chosen interface accurately.

## Read answers separately from routes

Decision records expose `label` and `distribution` for Choice, `value`, `distribution` and
`confidence` for Score, and `noul` for yes/no questions. These are available on successful
evaluations even when the built-in `route` is `no_advice`. Check response `status`, record
`fail_reason` and answer availability to distinguish a result from a failed request.
For a gate, `fail_reason: "uncalibrated"` can describe the built-in routing policy while a
valid answer is still present; it does not mean inference failed.

`route` describes the kit's built-in threshold policy. It is not a restriction on how an
application may use the answer. A caller can select the top Choice, apply its own cutoff,
combine scores, or delegate the next decision to another model without a registry entry.
If the user wants the built-in policy, consume its routes instead. Keep `is_mock` and model
identity visible so a simulation is not described as a live result.

The optional shipped hooks implement a particular policy: shadow returns no host decision;
enforce checks calibrated routes and allowed labels, and asks or blocks on failures and
unparsed commands. They do not emit affirmative host approval. Applications can implement
other approval or action policies in their own integration; this skill does not mandate
the shipped hooks or require human escalation.

## Design useful questions

- Choice selects one option. Noul is the probability of yes, not intensity, and has no
  separate confidence field. Score rates ordered levels. Put the question's complete
  meaning in instructions and criteria because question IDs are not sent to the model.
- Supply relevant observations and describe gaps honestly. Missing evidence can affect
  accuracy; the application decides whether to proceed, gather more, or use a fallback.
  A model judgment is not proof that a test ran or a source was inspected.
- Keep exact counts, arithmetic and literal checks in code where practical. Use Jev for
  semantic judgments, and compose the results according to the user's policy.
- Batch independent questions over the same state. State speculative premises explicitly;
  use a later call when an answer is needed to construct new input. Extra questions cost
  tokens, so choose the useful ones rather than a mandatory checklist.
- Confidence describes distribution concentration, not a guarantee of correctness. Testing
  on representative inputs, calibration, mirrored rubrics and reversed pairwise comparisons
  are optional ways to evaluate a design. Match the effort to the user's needs.

## Current client behavior

The bundled client validates schemas and model identity, transforms declared fields, scans
outgoing bytes for recognized credential patterns, and writes receipts. Free text needs a
configured source type. Live calls need the client's attestation configuration, except for
the fixed-sample demos. These are current implementation requirements, not universal Jev
requirements or application-policy advice. Explain a concrete configuration error and its
supported remedies instead of refusing the user's intended application.

Keep API keys out of source, diagnostics and receipts. Report real answers, replayed answers,
errors and unmeasured assumptions accurately. The user chooses what actions to take from them.
