# Phase-0 hardening — adversarial re-review (round 3 / gate 3)

Date: 2026-09-26
Branch under review: `phase-0-hardening` vs `phase-0`
Scope: read-only. No edit/commit/push/network. Verified the 14 claimed fixes (H1-H14) and
hunted for new regressions.

## Gates (observed, all pass)

- `pip install -e . --no-deps -q`: ok
- `python -m pytest -q`: all pass (368 tests, exit 0)
- `python -m ruff check src tests scripts tools`: All checks passed!
- `python -m mypy`: Success: no issues found in 52 source files

## Verdict

MERGE with two follow-ups (both MAJOR, low practical exploitability). No blockers. No gate
fail-open. Owner-gated boundary intact: default mode is shadow (`hooks/*.py` `_mode_from_env`),
registry empty by default so nothing clears to accept, live send still requires attestation
(`engine.py` `check_attestation`), no threshold promoted, no host armed, no live call.

## H1-H14 dispositions

- H1 resolved — core `_may_allow` (hooks/core.py:689) allows only route==accept AND
  label in allow_labels; gate load requires allow_labels (questionset.py `_gate_allow_labels`),
  missing list fails closed. Confident "yes" on a destructive gate -> record{route=accept,
  label=yes, allow_labels=[no]} -> ASK (unit test + code path confirmed). Could not reach ALLOW
  with a dangerous answer.
- H2 resolved — claude (`{}`), codex (`{}`), hermes (`None`) all decision-free on ALLOW; only
  ASK/deny/block emits a decision. Shadow and enforce-ALLOW both decision-free.
- H3 resolved — profile identity is (name, version) in `effective_contract` (engine.py:483);
  bumping a version changes the fingerprint (test_fingerprint_changes_with_profile_version).
- H4 resolved — `_reject_nested` (egress.py:421) blocks dict-in-list, list-in-list,
  dict-in-dict; flat scalar collections still pass (probed).
- H5 resolved — `transcripts.enabled` must be a real bool; "false"/"true"/1/0 all rejected
  (questionset.py:180, probed).
- H6 PARTIAL — `_SAFE_QID` (questionset.py:166) restricts charset/length but ACCEPTS common
  credential shapes (AWS `AKIA...`, GitHub `ghp_...`), which then land verbatim as
  `question_id` in the durable local receipt even when the wire send is egress-blocked. See
  MAJOR-1.
- H7 resolved — `_require_answer_type` (validation.py:87) now requires type == expected;
  missing/null rejected. Legitimate paths still pass (all live-transport/validation tests
  green after adding `type` to fixtures).
- H8 resolved — post-resolution deadline recheck (engine.py:539) downgrades any accept to
  ask (gate) / no_advice with fail_reason=timeout, mutating the same records list that is both
  written and returned.
- H9 resolved — overlap check is now strict `a.upper > b.lower` (routing.py:848, no tolerance
  slack); `_score_candidate` sorts intervals so evaluation is order-independent.
- H10 resolved — dangerous-command scan is linear token matching (`_find_dangerous_commands`);
  100 KB adversarial `rm` flag scans <1s (test), and all 10 rules still detect with clean
  negatives (battery probed). Over-detection only (safe direction).
- H11 PARTIAL — `_decision_committed_on_disk` (engine.py:781) accepts a cross-process committed
  decision and rejects unknown ids, but does NOT truly honor per-call commit markers: `pending`
  is never reset and the commit's `call_id` is never matched, so a decision whose own batch was
  never committed is reported committed if any later batch commits in the same file. See
  MAJOR-2.
- H12 resolved — `_config_from_env` (engine.py:125) reads `JEV_KIT_SOURCE_ALLOWLIST` /
  `JEV_KIT_PUBLIC_NAMES`; run_json now uses it (test_config_from_env_reads_source_allowlist).
- H13 resolved — claude shim extracts command/script/code (claude.py `_COMMAND_FIELDS`), so a
  PowerShell call carries its real script, not the bare tool name (probed + test).
- H14 resolved — promotion rejects NaN/inf/out-of-range/bool reference & tolerance
  (promotion.py:56, `invalid_criteria`); valid inputs still promote (probed).

## New blockers or majors

### MAJOR-1 (H6) — secret-shaped question id reaches the durable receipt
`src/jev_kit/questionset.py:166` — `_SAFE_QID = ^[A-Za-z0-9_.:-]{1,64}$` accepts compact
alphanumeric credentials (AWS `AKIAIOSFODNN7EXAMPLE`, GitHub `ghp_...`). Confirmed: a decision
receipt is written to disk with `question_id = "AKIAIOSFODNN7EXAMPLE"` even though the wire send
was egress-blocked. Receipts are local at-rest data (this tree is under OneDrive sync), so the
H6 goal ("cannot reach a receipt") is not met for these shapes.
Fix: at load, also reject when the secret detector fires —
`from jev_kit.egress import scan_text; if scan_text(qid) is not None: raise
ValidationError(FailReason.CONFIG, "question_id_secret_shaped")` (the detector already
recognizes AWS/GitHub shapes).

### MAJOR-2 (H11) — cross-batch false positive in the durable commit check
`src/jev_kit/engine.py:786-788` — the scan adds a decision id to `pending` on its decision line
and returns True on the next `kind=="commit"` line while it is pending, never resetting
`pending` and never comparing the commit's `call_id`. A decision whose own batch was truncated
(write_call returned False) is reported committed once any later batch commits in the same file
(confirmed with a hand-crafted receipt file). This defeats the stated guarantee ("a decision in
a truncated batch does not count"). Impact is audit-integrity only (record_outcome accepts an
outcome for a non-durable decision); no gate bypass.
Fix: match the marker to the batch — collect committed `call_id`s and a decision_id->call_id
map, and return True only when the decision's own `call_id` has a commit (decision lines already
carry `call_id`, engine.py:603; the commit marker carries it too, receipts.py:142).

## Notes / non-issues

- H10 token scan keeps `seen_curl_or_wget`/`seen_base64_d` set for the whole line, so a pipe
  not connected to the curl can still flag curl_pipe_shell — over-detection, safe direction.
- No hardcoded secret/key introduced in the diff; `_config_from_env` reads only allowlist env
  vars and defaults to empty (no egress widening by default).
