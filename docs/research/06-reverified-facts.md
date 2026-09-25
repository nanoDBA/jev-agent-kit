# 06. Re-verified facts (P0-5)

Checked 2026-09-25 against primary sources (official TypeSafe docs, official repos, host docs).
Items marked "spot-checked" were re-read directly from the source page; the rest come from a
research pass that cited each source URL. "Unverified" means no primary source documents it.

## Summary of corrections to earlier notes

| Earlier note | Correction | Affects |
| --- | --- | --- |
| Validation errors are 400 (`CLAUDE.md` coding conventions) | API documents **422** for validation. SDKs also map 400, 403, 404, 5xx. | P1-1, P1-2 typed errors |
| Error classes: 401/403, 429, timeout, 5xx | Add **529 Overloaded**, retryable like 429 | P1-1 retry policy |
| Choice takes 2..255 options (`03` finding 8) | Max 255 is documented; **no minimum is documented** | P1-1 finding 8 wording |
| No state size limit known | **64k tokens** per request (state plus all questions); **32k** for state plus the longest question | P1-2 state budget |
| Vendor adapter `system-one-adapter` | Official repo is **`typesafe-ai/system-one-adapter-python`**; `typesafe-ai/system-one-adapter` is 404 | P6-1 |
| Weak spots: 7 modes | Page lists **9**: adds common-sense structural invariants and generation | P2-1 guardrails |
| Codex skills dir (unspecified) | **`~/.agents/skills`** (user), `.agents/skills` (repo), `/etc/codex/skills` | P2-3 |

## 1. Model id (spot-checked)

- `jev-1.13.0` is still the only versioned model. Aliases: `jev-latest` and `jev-preview`, both
  currently pointing at `jev-1.13.0` ("no preview build available right now").
- `GET /v1/models` lists names the account may send (currently aliases only). Versioned ids are
  accepted whether or not they are listed.
- No model changelog. SDK changelogs only (Python v0.7.1 on 2026-09-21, JS v0.6.0 on 2026-09-15).
- Source: https://docs.typesafe.ai/models.md

Implication: the pin in `CLAUDE.md` stays valid. Treat `jev-preview` like `jev-latest`: never in
anything with a threshold.

## 2. Question and request limits (spot-checked for context length)

- Choice: at most 255 options; no documented minimum. Score: at least 2 levels, at most 10.
  Noul: returns `noul`, a probability of yes in [0, 1].
- Context: 64k tokens per request (state plus all questions); 32k tokens for state plus the
  single longest question. Text only.
- Unverified: a limit on questions per request; character limits.
- Sources: https://docs.typesafe.ai/api.md, https://docs.typesafe.ai/primitives/score.md,
  https://docs.typesafe.ai/models.md

Implication: our own state budget should sit well under 32k tokens, both for cost and because
the jaggedness page reports accuracy falling as state grows. Enforcing 2..255 and 2..10 locally
is still sensible; describe the minimum of 2 options as our rule, not the API's.

## 3. Error schema (spot-checked)

- Documented on the API page: 401, 422 (body names the offending field), 429, 529.
- SDK exception classes add 400, 403, 404 and 5xx. Python SDK retries 408, 429 and 500 to 599.
- Every response carries an `x-typesafe-request-id` header; log it in receipts.
- Unverified: the JSON field names of the error body. Parse defensively and keep the raw body.
- Sources: https://docs.typesafe.ai/api.md,
  https://docs.typesafe.ai/sdk/python/api/exceptions.md,
  https://docs.typesafe.ai/sdk/python/api/retries.md

## 4. Rate limits and Retry-After

- Limits: 250,000 tokens per second and 1,200 requests per minute, "may change without notice".
- `retry-after` is honored by the SDKs "when the response carries one", so it is not guaranteed.
  The Python policy also reads `retry-after-ms`.
- Python defaults: 2 retries, backoff 0.5 s doubling to a 5 s cap, 0.25 jitter, 30 s total
  budget, 10 s per-call timeout. Direct HTTP guidance: exponential backoff on 429 and 529.
- No `X-RateLimit-*` headers documented.
- Sources: https://docs.typesafe.ai/models.md, https://docs.typesafe.ai/api.md,
  https://docs.typesafe.ai/sdk/python/api/constants.md

## 5. Endpoint and response shape (spot-checked for endpoint)

- `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`.
- Request: `state`, `model`, `questions` (map of id to `type`, `instructions`, `criteria`).
- Response: top-level `model` holds the served versioned id (for example `jev-1.13.0`);
  `answers` keyed by question id; `usage` with `input_tokens`, `output_tokens`.
  Choice answer: `choice`, `probabilities`, `confidence`. Score answer: `score`, `legend`,
  `probabilities`, `confidence`.
- Source: https://docs.typesafe.ai/api.md

Implication: the served-model check in non-negotiable 4 reads the top-level `model` field.

## 6. Jaggedness page

- "Applies to jev-1.13. Last reviewed 2026-09-17." Nine failure modes: literal reading; math and
  numbers including counting; date and time comparison; indirection; large irrelevant state
  (context rot); adversarial content; contradictory instructions and criteria; common-sense
  structural invariants; generation.
- Source: https://docs.typesafe.ai/model-jaggedness/jev-1.13.md

## 7. Vendor adapter

- `typesafe-ai/system-one-adapter-python`: "Drop-in TypeSafeClient replacement backed by LLM
  APIs". `pip install 'system-one-adapter[openai|anthropic|gemini]'`. Latest commit e1d4cc9
  (2026-09-22). Rust and other similarly named ports are community projects.
- Source: https://github.com/typesafe-ai/system-one-adapter-python

## 8. typesafe-ai/skills

- Claude Code: `claude plugin marketplace add typesafe-ai/skills`, then
  `claude plugin install typesafe@typesafe-ai`. Other agents:
  `npx skills add typesafe-ai/skills --skill typesafe-ai` (`-g` for global).
- Upstream HEAD matches our pin in `sources.lock.json` (Sync-PriorArt reported "current").
- Source: https://docs.typesafe.ai/agent-skill.md

## 9. Host skill directories and hook contracts

| Host | Skills | Pre-tool hook | Timeout behavior |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md` | `PreToolUse` in settings | (see Claude Code hooks docs) |
| Codex | `~/.agents/skills`, repo `.agents/skills` (scanned up to repo root), `/etc/codex/skills` | `~/.codex/hooks.json` or `[hooks]` in `~/.codex/config.toml`, same under `<repo>/.codex/`; `PreToolUse` supported; `commandWindows` for Windows | Default 600 s; on timeout the hook is skipped and Codex **continues (fails open)** |
| Hermes | `~/.hermes/skills/`, plus `<root>/.hermes/skills/`, `<root>/.agents/skills/`, `external_dirs` | Plugin in `~/.hermes/plugins/` calling `ctx.register_hook("pre_tool_call", fn)`; block by returning `{"action":"block","message":...}` | `plugins.hook_callback_timeout` default 30 s (0 disables, max 600); timeout or exception **fails closed** (blocks), then a 60 s suppression window |

Sources: https://code.claude.com/docs/en/skills, https://learn.chatgpt.com/docs/build-skills,
https://learn.chatgpt.com/docs/hooks, https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks,
https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/

Implications:
- `.agents/skills` is read by both Codex and Hermes, so one repo-level copy can serve both.
- Codex fails open on hook timeout, so a Codex gate must enforce its own internal deadline and
  return "ask" before the host timeout. Never rely on the host timeout to fail closed.
- Hermes fails closed on timeout, so advisory (fail-open) plugins must finish well inside 30 s
  or they turn into accidental blocks.
- Unverified: Hermes shell-hook timeout behavior.

## 10. Pricing

- $0.042 per million input tokens; output tokens free. Priced per token, not per call.
- Source: https://docs.typesafe.ai/models.md

## Prior-art drift (P0-3)

`Sync-PriorArt.ps1 -CheckUpstream` on 2026-09-25: 24 of 26 pins current. Pins were not moved;
these are notes for a later review, not reviewed code.

- **DoGMaTiiC/hermes-jev** moved 12 commits to `f0bd8608d1d2` (all 2026-09-25): gate v2 with
  separated signals and a deterministic ladder; `_noul` now rejects booleans rather than
  fabricating 0.0 or 1.0; calibration of gate v2 on real traffic with "no enforce point"; a v1
  versus v2 comparison on one corpus with a pre-registered criterion that did not flip; a gate
  contract (`contracts/tool-gate-v1.json`), release manifest and provenance verifier. Worth a
  source read before P3-3 and P4-2.
- **kerpopule/hermes-jev-skills** moved 3 commits to `87f17adf2708`: custom endpoints, keychain
  isolation for offline tests, installer regression tests. Low relevance.
- **pedramamini gist** pinned to revision `6bb90186f276` (committed 2026-09-20, latest of 10).
  Assumed, not proven, to be the revision read during research.
