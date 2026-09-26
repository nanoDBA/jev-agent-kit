# Independent adversarial review: Phases 3 and 4

Date: 2026-09-26. Reviewer: independent (read-only). Range: `git diff phase-0...phase-3-impl`.
Verdict: **REVISE** (two majors; no blockers). Owner-gated boundary: **respected**.

## Gates observed (all pass)

- `pip install -e . --no-deps -q`: ok
- `python -m pytest -q`: **all pass, exit 0** (~354 tests)
- `python -m ruff check src tests scripts tools`: **All checks passed!**
- `python -m mypy`: **Success: no issues found in 52 source files**

## Boundary discipline (charter) — respected

- No threshold promoted. `calibration/promotion.py` returns a `PromotionResult` dataclass only;
  it imports nothing but `metrics` and never touches `registry.py`/`engine.py` (grep confirms no
  registry/engine import in `src/jev_kit/calibration/*`). It only recommends, and confirms on a
  frozen split.
- Enforce not enabled. Every shim defaults to `Mode.SHADOW`; an unrecognized `JEV_KIT_HOOK_MODE`
  falls back to shadow (`claude.py:65-70`, `codex.py:79-84`, `hermes.py:50-56`).
- No host armed, no live API call. Tests inject fake runners / use `python -c` children
  (`test_hooks_watchdog.py:18-25`); no `LiveTransport` or API key appears in the diff's tests.
- DuckDB stays optional and out-of-process: imported lazily inside `to_duckdb`
  (`tools/receipts_report.py:214-220`); importing the module never needs it.
- No `type: ignore`, `noqa`, or `cast` anywhere in the new source.

## Findings

### MAJOR 1 — Enforce fails OPEN when records contain no dicts (`src/jev_kit/hooks/core.py:129`)

`routes = [r.get("route") for r in records if isinstance(r, dict)]`; then
`if all(route == "accept" for route in routes)` returns ALLOW. `all([])` is `True`, so a
non-empty `records` list containing only non-dict entries yields **ALLOW in enforce**.
Empirically confirmed: a runner returning `{"status":"ok","records":[42,"nope"]}` gives
`outcome=allow route=accept`. This contradicts the module's own guarantee ("Enforce fails closed
to ASK on any failure") and the review's "unparseable-event path fails closed" requirement.
Reachability: low via the shipped engine (it always emits dict records, `engine.py:585-589`) and
the watchdog only checks that the top-level response is a dict, not record shape — so this is a
latent, defense-in-depth fail-open in the worst direction.
Fix: `if routes and all(route == "accept" for route in routes):` (empty routes must fall through
to ASK).

### MAJOR 2 — Core does not independently own its deadline; blocks until the runner returns (`src/jev_kit/hooks/core.py:95-102`)

On `future.result(timeout=self_deadline_s)` timing out, the `return` exits the
`with ThreadPoolExecutor(...) as pool:` block, whose `__exit__` calls `shutdown(wait=True)` and
**joins the still-running worker**. Empirically: a runner sleeping 2.0s with
`self_deadline_s=0.2` returns the correct ASK but only after `elapsed=2.03s` — the self-deadline
is not enforced. The real wall-clock bound comes solely from the default `run_child` subprocess
kill (`watchdog.py:51-54`), which caps at ~`self_deadline_s`; so the shipped path is safe and the
adapter never relies on a *host* timeout (review point (c) holds via the subprocess). But the
stated amendment-1 mechanism ("the core owns its own deadline ... abandons a result that misses
the deadline") is false, and `test_hooks_core.py:65-74` asserts the outcome but never the elapsed
time, masking it. Risk: an injected runner (the public `runner=` arg) or a future default that
does not self-bound would hang the hook past the deadline; in shadow on Hermes that is the
amendment-2 "shadow blocks at 30s" hazard.
Fix: don't join on exit — create the executor without the `with`, and on timeout call
`pool.shutdown(wait=False, cancel_futures=True)` before returning (and add an elapsed-time
assertion to the test).

## Verified safe (no finding)

- **Shadow never blocks:** `_fail_closed` returns ALLOW in shadow on every failure/timeout/
  exception path (`core.py:61-66, 100, 102`); confirmed for a hung runner (returns ALLOW).
  With the default `run_child` the wall-clock is ~`self_deadline_s` (default 8s) << Hermes 30s.
- **Enforce fail-closed on mock / uncalibrated:** defended at the engine — `resolve_route`
  routes mock and uncalibrated gates to a non-accept (ASK) route (`routing.py:213-214, 221-223`),
  so `core._map_response` never sees `route == "accept"` for them. (Note: the core itself does
  not re-check `is_mock`; it relies on the engine. Acceptable but worth a defense-in-depth guard.)
- **Codex actively denies** on ASK: JSON `permissionDecision: "deny"` plus exit code 2 and a
  stderr reason (`codex.py:189-218`), never relying on the 600s fail-open silence.
- **Watchdog hard-kills and cannot leak the key:** `proc.kill()` + `wait(1.0)` on timeout
  (`watchdog.py:52-54, 69-76`); `stderr=DEVNULL`, stdout used only as parsed JSON on
  `returncode == 0`, and every error path returns a fixed envelope (never child bytes)
  (`watchdog.py:49-66`). The API key rides only in the inherited child env (by design,
  owner-gated), never in argv/stdout/stderr.
- **PAV correct:** groups ties and averages labels, pools adjacent violators with a backward
  cascade, expands pooled means over breakpoints in order → non-decreasing `ys`
  (`pav.py:54-80`). Checked [1,0,1] -> [0.5,0.5,1]. No monotonicity/tie counterexample found.
- **Metrics correct:** Brier is mean squared error; ECE is bin-share-weighted |mean_prob-acc|
  over nonempty bins; coverage answers the confident tails and leaves the open middle band
  unanswered (`metrics.py:78-189`). Bool labels/probs rejected; prob==1.0 lands in the last bin.
- **Promotion** selects bounds maximizing coverage subject to a fit-split accuracy floor, then
  re-confirms accuracy on the disjoint frozen split; declines otherwise (`promotion.py:41-78`).
- **Receipts reader** only returns decisions from batches with a matching commit marker; drops
  truncated/mismatched batches (`receipts_report.py:96-114`).

## Nits

- `pav.py:68` uses a `1e-12` merge tolerance, so means may be non-monotone by up to 1e-12
  (harmless numerically).
- `core.py` shadow/enforce mapping trusts engine record shape; a `route`-key guard would harden
  it alongside MAJOR 1.
