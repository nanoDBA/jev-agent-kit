# ADR 0004: Declared log templates for structured log egress

- Status: proposed (owner to accept)
- Date: 2026-09-28
- Issue: jak-lck
- Extends: ADR 0002 Tier 2, "Error and log lines" (keep codes, levels and message templates;
  mask values and identifiers)

## Context

A `log` field today is free-form text. Without a declared template the kit cannot tell a
template word ("connection", "failed") from data (a hostname `db01`, a user `JaneDoe`, a numeric
password), so finding H17 reduced free-form log egress, fail closed, to level keywords and
placeholders: every other token becomes `<v>`. That is safe but low-signal. "ERROR connection to
db01 failed after 3 retries" leaves the machine as "ERROR <v>".

ADR 0002 always intended to keep message templates. The missing piece was a trustworthy source
for what counts as template text.

## Decision

A question set may declare `log_templates`. A `log` field opts in with
`"params": {"format": "template"}` and then accepts only structured records built from those
templates. Free-form `log` fields (no `format` param) are unchanged.

Question set:

```json
"log_templates": {
  "conn_failed": {
    "text": "connection to {host} failed after {attempts} retries: {reason}",
    "params": {
      "host": {"kind": "identifier"},
      "attempts": {"kind": "metric"},
      "reason": {"kind": "enum", "values": ["timeout", "refused"]}
    }
  }
},
"state_schema": {"events": {"kind": "log", "params": {"format": "template"}}}
```

Host state: one record, or a list of at most 20 records:

```json
{"events": [{"level": "error", "template_id": "conn_failed",
             "params": {"host": "db01", "attempts": 3, "reason": "timeout"}}]}
```

What leaves the machine for each record:

```json
{"level": "error", "template_id": "conn_failed",
 "message": "connection to id_1a2b3c4d5e6f7a8b failed after 3 retries: timeout"}
```

Slot kinds: `identifier` (HMAC token unless on the public-names allowlist), `contact`
(`<contact>`), `path` (`<path>`), `command` (basename only, with the existing compound and
quoting refusals), `metric` (a finite number, or the existing bounded metric token), and `enum`
(one of the declared values). The first five reuse the existing egress transforms through the
public `transform_state` function, so they behave exactly like a state field of that kind. Only
`level` values from a fixed keyword set are accepted.

Rendering is a single left-to-right pass over the parsed template: literal pieces are copied and
each slot is replaced by its transformed value. Values are never re-parsed, so a param that
contains `{host}` or other template-looking text cannot expand or forge a slot.

### Why template literals are safe to send

- They are authored in the question set, which is a reviewed, repo-controlled file, the same
  trust level as question instructions that already leave the machine verbatim. They are never
  runtime data: the host supplies only a `template_id` and params.
- At load, template text is restricted to a narrow charset (letters, digits, space and
  `. , : ; ( ) [ ] / _ + # % = ' -`), capped at 240 characters, must name each declared slot
  exactly once and no other, and is refused if any Tier 1 detector matches it. Template ids and
  enum values must be safe identifiers and also pass the detectors.
- The final whole-request Tier 1 scan (`scan_request`) still runs over the exact outgoing bytes,
  including every rendered message. A secret in a param that survives its transform (for
  example a credential-shaped metric token) blocks the whole request.

### Refusals (fail closed)

Any of these blocks the whole request with `egress_blocked` (gates ask, advisory returns no
advice). Nothing is passed through raw, and no partial field is sent.

- A templated field whose value is a string (free text) or anything other than a record or a
  list of records; a list longer than 20.
- A record with missing or extra keys, a level outside the keyword set, or an unknown
  `template_id`.
- Params that are not an object, or that are missing a slot or carry an extra key.
- A param value that is a bool, None, nested container, non-finite number, number in a
  non-metric slot, string over 256 characters, enum value not declared, or a value its slot
  transform refuses (for example a metric string outside the metric token charset, which also
  refuses newlines and braces).

At load (a `config` failure): malformed templates, a templated field when no templates are
declared, an unknown `format` value, unknown keys anywhere in a template definition.

### Calibration identity

Template definitions change what the model sees, so they are bound into the fingerprint:

- `egress_contract` adds a `log_templates` entry (every template's text and slot declarations,
  plus a digest of the `log_templates` module source) **only when the set declares templates**.
  A digest of the module source is required for templates to be usable at all: if the source
  cannot be read (a bytecode-only install) a set with templates fails to load its contract,
  closed.
- The `format` param appears in the schema part of the contract automatically.
- The question-set digest covers the whole file, so it changes automatically.

`src/jev_kit/egress.py` is **not modified**. The new code lives in `src/jev_kit/log_templates.py`
so `EGRESS_TRANSFORM_DIGEST` is unchanged, and every question set without templates keeps its
existing fingerprint. A test pins a pre-existing fingerprint to prove this.

## Rejected alternatives

- **Heuristic template inference from free-form lines** (drain-style log clustering). It guesses
  which tokens are data, which is exactly what H17 refused to do.
- **A new `ContentKind` inside `egress.py`.** Workable, but any edit to `egress.py` changes
  `EGRESS_TRANSFORM_DIGEST` and so every fingerprint in every question set. A separate module
  keeps existing calibration identities stable and binds its own digest only where used.
- **Sending params as a structured object next to the template text** instead of a rendered
  message. Unambiguous, but less natural for the model. Rendering is safe here because every
  slot value is either a token (HMAC, placeholder, enum, number) or a metric string whose
  charset excludes braces and newlines. This can be revisited if calibration shows the model
  prefers structure.
- **Per-field (rather than whole-request) blocking.** Existing egress blocks the whole request on
  any field failure; a partial request would silently change what a calibrated question sees.

## Residual risks

- A question-set author can write a sensitive literal into a template. Review of the question
  set is the control, backed by the load-time and final Tier 1 scans, which cannot catch every
  class (for example a customer name).
- A metric slot passes up to 64 characters of `[A-Za-z0-9 ._:+/-]` verbatim, as a metric state
  field already does. An author should use `enum` wherever the value set is known.
- An `identifier` token is linkable and still personal data (ADR 0002). A `log` field is a
  personal kind, so the live DPA attestation rules apply unchanged.
- The template approach only helps hosts that can emit structured records. Hosts with only raw
  log lines keep the H17 reduction.
