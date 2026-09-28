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
quoting refusals), `metric` (numbers only: a finite int or float, or a numeric string matching
`^[+-]?[0-9]{1,32}(\.[0-9]{1,32})?$`, with no unit suffix), and `enum` (one of the declared
values). The first four reuse the existing egress transforms through the public
`transform_state` function, so they behave exactly like a state field of that kind. A template
`metric` slot is deliberately stricter than a plain `metric` state field, which keeps its
existing bounded token behavior: a free-text metric slot would let a template author pass
unmasked text (a name, a hex id) past kind-based masking inside an otherwise trusted message.
Non-numeric values with a known set belong in an `enum` slot. `level` is matched
case-insensitively against a fixed keyword set and always sent lowercased (`ERROR` becomes
`error`); anything outside the set is refused.

Rendering is a single left-to-right pass over the parsed template: literal pieces are copied and
each slot is replaced by its transformed value. Values are never re-parsed, so a param that
contains `{host}` or other template-looking text cannot expand or forge a slot.

### Why template literals are safe to send

- They are authored in the question set, at the same trust level as question instructions,
  which already leave the machine verbatim. **Caller precondition:** template definitions
  (literal text, slot kinds and enum values) must come from trusted, reviewed question-set
  content. This applies equally to a question set passed inline to `decide` (the
  `question_set` request field), not only to files in a repository. A caller must never build
  template literals or enum declarations from runtime data (tool output, log lines, user or web
  content); doing so turns a template into an unmasked channel. The kit cannot verify where an
  inline definition came from, so this is the caller's responsibility. At runtime the host
  supplies only a `template_id` and params.
- At load, template text is restricted to a narrow charset (letters, digits, space and
  `. , : ; ( ) [ ] / _ + # % = ' -`), capped at 240 characters, must name each declared slot
  exactly once and no other, and is refused if any Tier 1 detector matches it. Every slot must be
  delimited by a space or the template edge on both sides. `elapsed {n}ms`, `code_{a}`,
  `{a}{b}`, `x-{n}`, `{a}.{b}`, `{a}:{b}` and `({n})` are all refused. A word character
  (including underscore) or a joiner such as `-`, `.`, `:` or `/` can glue a value to template
  text, or two slots into one token, in a form a boundary-anchored detector does not bridge
  (`code_4111 1111 1111 1111` and `4111.1111.1111.1111` both evade the card-number rule).
  Space is the separator that rule bridges, so a card split across adjacent slots stays
  visible to the final scan. Template ids and enum values must be safe identifiers and also
  pass the detectors. Every validator matches the whole string (`fullmatch`), so a terminal
  newline cannot slip past an anchored pattern.
- Template text uses single ASCII spaces only: no runs of spaces, no leading or trailing space,
  and no tab, non-breaking space or other whitespace (the charset refuses them).
- **Digit-stream card check.** After a record's slots are transformed, the digits of every
  `metric` and `enum` slot value are concatenated in template order, ignoring the literal
  text, spaces, signs and decimal points between them, and every 13 to 19 digit window is
  Luhn-checked. Any hit refuses the record. This does not depend on how the Tier 1 card rule
  bridges separators, so spellings such as `4111  1111 1111 1111` (a double space),
  `4111 -1111 -1111 -1111` (signed values), `4111 1111 1111 1111 0` (a trailing slot that
  pushes the run past a Luhn-valid length) or a card split by literal words are all caught.
  Signs are kept on metric values because the stream ignores them.
- Each rendered slot value, of every kind, is checked on its own before concatenation: it must
  contain no newline, control character or brace, and the same Tier 1 detectors the final scan
  uses (`egress.scan_text`) must not match it.
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
  non-metric slot, string over 256 characters, enum value not declared, a metric string that is
  not strictly numeric (names, words, hex, units, exponents), or a value its slot transform
  refuses.

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
  slot value is a token (HMAC, placeholder, command basename, enum value or number), none of
  which can contain braces or newlines. This can be revisited if calibration shows the model
  prefers structure.
- **Reusing the plain metric token (up to 64 characters of `[A-Za-z0-9 ._:+/-]`) for template
  metric slots.** It would let arbitrary words and identifiers ride through a template
  unmasked. Rejected in favor of numbers only.
- **Per-field (rather than whole-request) blocking.** Existing egress blocks the whole request on
  any field failure; a partial request would silently change what a calibrated question sees.

## Residual risks

- A question-set author can write a sensitive literal into a template. Review of the question
  set is the control, backed by the load-time and final Tier 1 scans, which cannot catch every
  class (for example a customer name).
- A numeric metric slot still sends the number verbatim. A number can itself be sensitive (an
  account or PIN-length value); authors should declare metric slots only for counts, sizes,
  durations and codes. Card-number-shaped values are still caught by the final scan.
- The digit-stream check fails closed and has false positives: about one random 13 to 19 digit
  window in ten is Luhn-valid, so a record whose numeric slots together carry 13 or more
  digits is often refused. Authors should keep numeric slots few and small (counts, codes,
  durations), and use `enum` for anything else.
- Digits inside `identifier` (HMAC tokens), `command`, `contact` and `path` slots are not part
  of the stream: those values are tokens or placeholders produced locally, not host digits.
  A card number cannot reach them unmasked except through a public-names allowlist entry or a
  command basename, both of which still pass the per-slot and final scans.
- Card numbers are the only Tier 1 class that is reassembled across slots. Other classes split
  across several numeric slots (for example an SSN as `123 45 6789`) are caught only if the
  final scan's pattern bridges the rendered form.
- Plain `metric` state fields keep the existing 64-character token behavior; this ADR does not
  change them.
- An `identifier` token is linkable and still personal data (ADR 0002). A `log` field is a
  personal kind, so the live DPA attestation rules apply unchanged.
- The template approach only helps hosts that can emit structured records. Hosts with only raw
  log lines keep the H17 reduction.
