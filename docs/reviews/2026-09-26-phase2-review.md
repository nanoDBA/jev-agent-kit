# Phase 2 adversarial review (2026-09-26)

Independent, read-only review of `phase-0...phase-2-impl`. Scope: SKILL.md, four question
sets, installer, audit, CLI subcommands. No code edited, nothing committed, no network calls.

## Gate results (all green)

- `pip install -e . --no-deps -q`: ok
- `python -m pytest -q`: 282 passed in ~7s
- `python -m ruff check src tests scripts`: All checks passed
- `python -m mypy`: Success, no issues in 35 source files

## Verdict: REVISE

One MAJOR. No blockers. The safety contract (SKILL.md, question sets, audit) is faithful and
adds no authority; the defect is a functional break in the installer's default path.

## MAJOR

### MAJOR-1: default `install --apply` writes a broken symlink

`src/jev_kit/install.py:164` `os.symlink(source, target, target_is_directory=True)` writes
the `source` string verbatim into the link. `src/jev_kit/cli.py:108`
`source = Path(args.source)` keeps the CLI default `"skills/jev-runtime"` relative and never
resolves it. On any symlink-capable platform (Linux, macOS, and Windows with Developer Mode,
which is enabled on the owner's own machine) `jev-kit install --apply` from the repo root
creates `~/.claude/skills/jev-runtime -> skills/jev-runtime`, resolved against the link's own
directory `~/.claude/skills/`, so the target is unreachable and the skill is not installed.

Reproduced: link points to `skills\jev-runtime`, `(target/'SKILL.md').is_file()` is `False`.
The copy fallback (Windows without Developer Mode) works because `copytree` reads relative to
cwd, which is why no test caught it: every `test_install.py` case passes an absolute
`tmp_path` source, and the CLI test uses dry-run only. A stale rerun then also misreports the
broken link as `conflict` because `_tree_hash` of the dangling target reads no files.

One-line fix: symlink an absolute source, e.g. `src/jev_kit/install.py:164`
`os.symlink(source.resolve(), target, target_is_directory=True)` (protects the library API and
all callers).

## Minors / nits

- MINOR: `src/jev_kit/audit.py:148-151` `dangerous_command.sudo` (`(?<![\w-])sudo\s`) and
  `:79-81` `injection.role_takeover` (`\byou\s+are\s+now\b`) flag ordinary prose ("sudo is
  dangerous", "you are now ready"). Both are medium severity so they do not fail CI; acceptable
  for a lint but expect noise. Fix: require a command-line-ish context or downgrade to low.
- NIT: `src/jev_kit/audit.py:15-17` docstring says regexes run "against individual lines of
  bounded length", but only files are byte-bounded (`MAX_FILE_BYTES`); a newline-free file
  yields one ~512 KiB line. Harmless because every pattern is linear (O(n)); reword the claim.
- NIT: `src/jev_kit/cli.py:9-17` module docstring documents only exit 0 and 2 for the JSON
  path; the `install`/`audit` subcommands add exit 1 (conflict / high-severity). Note it.
- NIT: audit calls `egress.scan_text` per line only, not the structural `scan_request`
  auth-pair pass, so a credential split across a JSON key and value is not flagged. Reasonable
  for a text lint; mention the limit.

## Confirmed correct

- SKILL.md faithfully states the engine contract: routes `accept`/`ask`/`no_advice`
  (`routing.py:216-228`); `accept` explicitly not authority, "map to an action in your code"
  (SKILL.md:32-34); fail asymmetry gate=ask / advisory=no_advice matches `_fail_route`
  (`routing.py:175-176`); "gate escalates to a human or deterministic check, never a model"
  (SKILL.md:44-46); nine weak spots and keep-math-in-code (SKILL.md:64-69); batch-one-request
  (SKILL.md:53-56); egress/state and the free_text named-source rule (SKILL.md:72-86). No
  instruction anywhere tells a host to auto-act on a gate or treat Jev output as authority.
- Question sets: all four load via `load_question_set_file`; each pins `jev-1.13.0`;
  `escalation_target` is `human-review` / `deterministic-budget-check` (non-model); only
  `tool-call-gate.json` uses `consequence: gate`; `state_schema` kinds are coherent with egress
  (`free_text` blocked until the operator allowlists its `source_type`, `command` reduced to
  names, `identifier` HMAC-tokenized), so no set can leak by construction. The free_text /
  source_type requirement is documented in SKILL.md:76-78. D2 SQL pack correctly absent.
- Installer: dry-run writes nothing; apply idempotent (`skip_same` by tree hash); symlink with
  copy fallback on `OSError`; conflict-unless-force; targets are fixed computed paths (no path
  escape); no network. Host paths match docs/research/06 section 9.
- Audit: patterns are data-only, never executed (proved by
  `test_audit.py:168` patching `subprocess.run`/`Popen`); regexes linear (ReDoS-bounded);
  secret findings delegate to `egress.scan_text`; high-severity -> nonzero exit is sane.
- CLI: subcommand dispatch (`cli.py:159-162`) cannot shadow the JSON-request default (default
  path takes no positional first arg); exit codes coherent; offending input never echoed.
