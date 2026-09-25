# ADR 0002: Egress policy for state sent to Jev

- Status: accepted
- Date: 2026-09-25
- Decided by: owner, who directed that the policy follow accepted security standards and
  practice (answers open question 3 in `docs/plan.md`)
- Evidence: every standard, control ID and vendor term below was checked against its primary
  source on 2026-09-25. URLs are in the References section.

## Context

The client sends small state packets to TypeSafe's hosted API. The kit is general purpose:
state can hold code or query text, names of internal systems and people, error and log
lines, shell and tool commands, file paths and URLs, free text under judgment (tickets,
messages, documents, web content) and agent transcript excerpts. The policy is therefore
defined over content kinds, not over any one domain. Domain-specific rules live in
optional profiles (see below).

What TypeSafe's own terms say (checked directly):

- No training on customer input (Privacy Policy; Master Customer Agreement).
- Zero data retention is offered **for enterprise customers only**. Without it, input is
  retained "as long as reasonably necessary", hosted in the United States.
- The Master Customer Agreement (updated 2026-09-23) defines Telemetry to include "hashes,
  summary statistics and classifications" and says TypeSafe "may Process Telemetry without
  restriction". Anything we send can therefore surface in unrestricted derived data.
- There is no business associate agreement (HIPAA) and no clause accepting cardholder or
  special-category data. A DPA with EU SCCs exists. SOC 2 Type II is listed; no ISO 27001.

## Decision

### Principles

1. **Allowlist, deny by default.** A field leaves the machine only if the question set's
   field allowlist names it, and only after its required transform. Everything else is
   dropped. (NIST SP 800-53 SC-7(5); OWASP Input Validation Cheat Sheet.)
2. **Detect, then block, even after transforms.** Tier 1 detectors run over the final
   serialized request. Any hit fails closed: nothing is sent, gates resolve to "ask",
   advisory questions to "no advice". (AC-4(15), AC-4(25), SC-7(10); OWASP LLM02:2025.)
3. **Log the rule, never the value.** Receipts record detector rule ids, transform names and
   counts, never matched text. (AC-4(26); OWASP Logging Cheat Sheet.)
4. **Minimize.** Send the smallest state that answers the question. (SI-12(1); NIST SP 800-122
   section 4.2.1; OWASP LLM02.)
5. **Keyed hashes only.** Where an identifier must be replaced by a stable token, use
   HMAC-SHA256 with a local 256-bit key that never leaves the machine. Unkeyed hashes of
   identifiers are forbidden: they can be brute-forced, and the vendor may keep hashes as
   unrestricted Telemetry. (NIST SP 800-188 section 4.3.1; ENISA pseudonymisation guidance.)
   Keyed-hash tokens are still linkable and remain personal data if the input was personal
   (GDPR Art. 4(5)).

### Tier 1: always blocked

A detector hit anywhere in the request blocks the whole request.

| Class | Examples | Basis |
| --- | --- | --- |
| Credentials and secrets | Private key PEM blocks; JWTs; cloud access keys (AWS, Azure, GitHub and similar); Azure SAS tokens and storage keys; HTTP `Authorization` headers; passwords; high-entropy strings near credential keywords | OWASP LLM02:2025 and Logging Cheat Sheet; AC-4(15); CIS 3.13; gitleaks, GitHub secret scanning and Microsoft Purview pattern sets |
| Connection strings carrying credentials | SQL Server and Azure SQL strings with `Password=` or `Pwd=`; MongoDB, MySQL, PostgreSQL URIs with credentials | OWASP Logging ("Database connection strings"); Purview "SQL Server connection string" |
| Payment card data | Luhn-valid PANs; track data, card verification codes, PINs | PCI DSS v4.0.1 3.3.1, 3.4.2, 3.5.1, 12.8.1 |
| Government identifiers | US SSN and other national ids | NIST SP 800-122 section 2.1; SP 800-53 PT-7 |
| Health information | Any of the 18 HIPAA Safe Harbor identifiers in a health context | 45 CFR 164.514(b)(2); no BAA with the vendor |
| GDPR special categories | Health, biometric, genetic, beliefs, orientation and the other Art. 9 classes | GDPR Art. 9, Art. 28 |
| Raw data rows | Result sets, row values, table contents | Minimization (SI-12(1)); PCI and HIPAA exposure |

### Tier 2: blocked unless allowlisted and transformed

| Content kind | Default transform before egress | Basis |
| --- | --- | --- |
| Code and query text (any language: SQL, scripts, configuration, infrastructure as code) | Replace string and numeric literals with placeholder tokens and strip comments, using the language's profile when one exists; otherwise treat as free text. | SI-12(1); SI-19(4) |
| Internal identifiers (host, service, repository, project, customer, database object, account and user names) | Replace with HMAC-SHA256 tokens. Names on a reviewed allowlist of public or system names pass unchanged. | SP 800-188 section 4.3.1; OWASP Logging ("commercially-sensitive information") |
| Personal contact data (email addresses, phone numbers, postal addresses, IP addresses) | Mask, or replace with HMAC tokens when linkage matters. | NIST SP 800-122 section 2.1; SI-19(4); GDPR Art. 4(5) |
| Error and log lines | Keep codes, levels and message templates; mask quoted values, identifiers and personal contact data. | OWASP Logging; AC-4(25) |
| Shell and tool commands | Keep the command and flag names; redact argument values, environment values and URLs with query strings. | OWASP LLM02; AC-4(8) |
| File paths and URLs | Replace the user profile, hosts and query strings with tokens (`%USERPROFILE%`, `<host>`); HMAC the remaining segments unless allowlisted. | SI-19(4) |
| Free text under judgment (tickets, messages, documents, web content) | Allowed only when the question set declares the field as the content being judged. Personal contact data is masked by default; a question set may opt out per field with a recorded justification. Tier 1 detection always applies. | OWASP LLM02; SI-12(1); RA-8 |
| Agent transcripts | **Off by default.** When a question set enables them: a size-capped excerpt only, after all other transforms, then Tier 1 detection. | OWASP LLM02; NIST AI 600-1 section 2.4 |

### Tier 3: allowed as-is

Only fields a question set's allowlist names, of these kinds: tool name, action category,
the kit's own field names and decision codes, numeric metrics (durations, counts, sizes),
error codes, product and version strings, and HMAC tokens produced by Tier 2. (SC-7(5);
CIS 3.7, 3.8; SI-12(1).)

### Domain profiles

A profile adds detectors, transforms and allowlists for one domain. It may only tighten the
core policy: a profile can add blocked patterns and stricter transforms, never remove a Tier 1
class or relax a Tier 2 default without a recorded justification in the question set.
Profiles are versioned data like the core rules.

- **SQL profile** (an example profile; which profile ships first depends on the first real use
  case): literal stripping for SQL dialects, following the model of PostgreSQL
  `pg_stat_statements` and SQL Server `query_hash`, which identify statements that differ only
  by literals; object-name tokenization with a system-object
  allowlist (for example `sys.*`, `INFORMATION_SCHEMA.*`); SQL Server error lines reduced to
  error number, severity and state; SQL Server and Azure SQL connection-string detectors.
- Other profiles (for example cloud and infrastructure as code, web content, source control)
  are added when a question set needs them.

### Preconditions for any live egress

- Jev is recorded as an external service in the owner's data-flow inventory (CIS 3.8; CA-3;
  SA-9; NIST AI 600-1 GV-6.1-007).
- Any personal data requires the TypeSafe DPA to be in place (GDPR Art. 28).
- Enterprise zero data retention is preferred (SI-12; SA-9(5)); until then, Tier 2 transforms
  are mandatory, not optional.

## Amendment 1 (2026-09-25): free text, undetectable classes, live-use attestation

Decided by the owner after the independent Codex review of the Phase 1 spec
(`docs/reviews/2026-09-25-codex-spec-review.md`, finding R09).

### Free text is allowed only from named source types

- Free text under judgment (and code in a language without a profile, which is treated as free
  text) may leave the machine only when its field declares a `source_type` that appears in the
  owner's local source allowlist. The allowlist is local configuration, not part of the repo,
  and **ships empty**: until the owner names source types, all free text is blocked.
- Examples of source types the owner may name: public web pages, the owner's own issue
  tracker, the owner's own agent tool output. Each named type is an explicit acceptance of the
  residual risk below for that source.
- Free text still gets the default contact-data masking and the Tier 1 detectors.

### Accounting for every Tier 1 class

| Class | Control |
| --- | --- |
| Credentials and secrets; credentialed connection strings | Pattern detectors on the final request (block) |
| Payment card numbers | Luhn-valid PAN detector (block) |
| Card track data | Track-format detector (block) |
| Card verification codes and PINs | Not detectable in isolation; controlled by allowlist and source policy (never an allowed content kind) |
| National identifiers | Named formats only (Phase 1: US SSN); further formats added by profile |
| Health information; GDPR special categories | Not reliably detectable; controlled by source policy (named source types only) and by question sets declaring they do not carry these classes |
| Raw data rows | Never an allowed content kind |

**Residual risk accepted by the owner:** a named free-text source can still contain health
information, special-category data or postal addresses that no detector recognizes. Postal
address masking is deferred (owner, 2026-09-25): Phase 1 has no address transform, so a field
declared as an address is blocked, and addresses inside named-source free text are accepted. Masking and declarations
reduce but do not eliminate this. Naming a source type is the acceptance.

### Live-use attestation

The live transport refuses to send unless a local attestation file records, with dates: that
TypeSafe is in the owner's data-flow inventory (CIS 3.8, CA-3, SA-9), and whether a DPA is in
place (required before any personal data, GDPR Art. 28). `dpa: false` permits only requests
whose every field is a non-personal Tier 3 kind; HMAC tokens count as personal data. Missing or invalid attestation is a
failure (gates ask, advisory returns no advice). Mock transports do not require it.

## Implementation rules

- Detectors and transforms are **data**, versioned in the repo, with the source pattern set
  and commit they were adapted from recorded (gitleaks is MIT licensed; Purview and GitHub
  patterns are documentation, reimplemented, not copied code).
- The detector set must cover gaps in the default gitleaks rules: SQL Server connection
  strings and Azure SAS or storage keys are not in gitleaks by default.
- Detection runs on the **final serialized request**, after redaction and transforms, so a
  transform bug cannot leak a secret.
- Every question set declares its field allowlist and transforms; the fingerprint of the
  question set includes the egress policy version, so a policy change forces re-review.
- Tests must include known-secret fixtures (fake keys, fake PANs, fake SSNs, fake connection
  strings) that must be blocked, and near-miss fixtures (placeholders such as `Password=***`,
  `<password>`) that must not cause false alarms where Purview suppresses them.

## Consequences

- Some useful context (real names, literal values) is never visible to Jev. Questions must
  be designed to work on normalized text and tokens.
- A detector false positive costs an "ask" or "no advice", never a leak.
- The HMAC key is a new local secret, supplied like the API key (environment variable or key
  command, ADR 0003).

## Open items for the owner

1. Pursue TypeSafe's enterprise zero data retention (owner: yes, 2026-09-25). It is not a
   prerequisite and does not relax this policy, because derived Telemetry may fall outside it.
2. Which internal identifiers, per profile, are non-sensitive enough to allowlist as-is.
3. Confirm with TypeSafe whether zero data retention also covers Telemetry derived from
   input, whether "Telemetry" can include raw input, the actual retention
   period without zero retention, and the scope of the SOC 2 Type II report.

## References

- TypeSafe: https://docs.typesafe.ai/legal.md, https://typesafe.ai/legal/privacy-policy,
  https://typesafe.ai/legal/mca, https://typesafe.ai/legal/data-processing,
  https://trust.typesafe.ai/ and /subprocessors
- OWASP LLM02:2025 Sensitive Information Disclosure:
  https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/
- OWASP Logging Cheat Sheet and Input Validation Cheat Sheet:
  https://cheatsheetseries.owasp.org/
- NIST SP 800-53 Rev. 5 (controls AC-4 and enhancements 8, 15, 25, 26; AC-21; CA-3; SA-9, SA-9(5);
  SC-7, SC-7(5), SC-7(10); SI-12, SI-12(1); SI-19, SI-19(4); PT-2, PT-3, PT-7; RA-8):
  https://github.com/usnistgov/oscal-content
- NIST SP 800-122: https://csrc.nist.gov/pubs/sp/800/122/final
- NIST SP 800-188: https://doi.org/10.6028/NIST.SP.800-188
- NIST AI 600-1: https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- CIS Controls v8.1, Control 3: https://www.cisecurity.org/controls/data-protection
- PCI DSS v4.0.1: https://www.pcisecuritystandards.org/document_library/
- 45 CFR 164.514: https://www.ecfr.gov/current/title-45/section-164.514
- GDPR: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- ENISA, Pseudonymisation Techniques and Best Practices (2019):
  https://www.enisa.europa.eu/publications/pseudonymisation-techniques-and-best-practices
- gitleaks default rules: https://github.com/gitleaks/gitleaks/blob/master/config/gitleaks.toml
- GitHub secret scanning patterns:
  https://docs.github.com/en/code-security/secret-scanning/introduction/supported-secret-scanning-patterns
- Microsoft Purview sensitive information types:
  https://learn.microsoft.com/en-us/purview/sit-sensitive-information-type-entity-definitions
- SQL Server query_hash: https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql
- PostgreSQL pg_stat_statements: https://www.postgresql.org/docs/current/pgstatstatements.html
