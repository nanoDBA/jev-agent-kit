# Evidence contracts

Use the task index, then read only the selected section and its linked question set. These
are caller-side evidence requirements, not new API fields or verified engine capabilities.
The files are the source of truth for wording, types, field kinds, source types, and model
pins. All four sets ship uncalibrated. Structural tests check this index against those files;
they do not establish evidence quality, calibration, or host enforcement.

## Task index

| Task | Question set | State fields | Question IDs and types | Consequence |
| --- | --- | --- | --- | --- |
| [Choose a handler](#preflight-route) | [preflight-route.json](../questions/preflight-route.json) | `request` | `route` (choice) | advisory |
| [Review an output](#postflight-verify) | [postflight-verify.json](../questions/postflight-verify.json) | `request`, `output` | `addresses_request` (noul), `contains_citation` (noul), `within_scope` (noul) | advisory |
| [Assess a proposed tool call](#tool-call-gate) | [tool-call-gate.json](../questions/tool-call-gate.json) | `command`, `cmd_parse_confident`, `cmd_recursive_delete`, `cmd_force_flag`, `cmd_targets_root_or_system_path`, `cmd_targets_home_directory`, `cmd_uses_elevation`, `cmd_network_download`, `cmd_pipes_to_shell`, `cmd_modifies_permissions`, `cmd_git_history_rewrite`, `cmd_package_install`, `target`, `context` | `destructive` (noul), `exfiltrates` (noul), `widens_permission` (noul) | gate |
| [Decide whether to finish](#stop-or-continue) | [stop-or-continue.json](../questions/stop-or-continue.json) | `goal`, `evidence_summary` | `enough_to_finish` (noul), `another_step_would_help` (noul) | advisory |

## preflight-route

Required observations: the actual current request, its constraints and intended outcome, plus
local confirmation that the candidate handler exists and is authorized for this task. Resolve
fixed lookups and policy exclusions deterministically before considering semantic routing.

Known limitations: only `request` is a declared state field. The fixed handler classes do not
prove a concrete tool, provider, or person is available; code owns that mapping and permission
check. The source type for `request` must be allowlisted. If ambiguity prevents choosing an
authorized handler, retain `no_advice` and clarify; do not route from guessed intent. The
`unclear` option exists for that case: a high `unclear` means ask the requester, not pick a
handler.

## postflight-verify

Required observations: the exact request and output under review, the requirements being
checked, and actual check results tied to this output revision. For each factual citation,
the reviewer needs accessible source content and a supporting passage, not merely a URL.
Run relevant code tests, literal comparisons, and derivation checks outside Jev.

Known limitations: the set has only `request` and `output`, with no dedicated source-content,
test-result, or citation-provenance field. The citation question only detects whether a
citation is present; it never judges accuracy, because the state holds no source content. A
high `contains_citation` means code or a person must check each cited source; a low one means
there is nothing to check, not that the output is accurate. Do not add a `sources` field or
rewrite the request to imply authorization. A persuasive answer can still be wrong; a passing relevance judgment cannot
replace the missing evidence or deterministic correctness checks.

## tool-call-gate

Required observations: the exact proposed executable/script and arguments, the resolved
target and current resource state, the relevant trust boundary and data destinations, and
the host's authorization scope. Code checks paths, permissions, destructive effects, and
egress deterministically; evidence must describe the same action that would execute.

Known limitations: the command transform keeps only the executable's basename and drops
every subcommand, flag, and argument (`git -C repo push --force` is sent as `git`). The hooks
add `cmd_*` booleans computed locally from the full line (ADR 0005), such as
`cmd_git_history_rewrite`; they are heuristic, `null` means unknown, and a command the parser
cannot read (`cmd_parse_confident` false) never reaches allow in enforce. Identifier
transforms obscure target identity, and `context` requires an allowlisted source type. Those
transforms can
remove precisely what distinguishes a safe command from a destructive or outbound one. Keep
raw checks local; if the permitted packet loses a necessary distinction, `ask` rather than
infer safety from redaction. A missing target or boundary is unknown, not harmless.

All three questions describe hazards: a confident yes is adverse evidence. A favorable no
still requires the explicit allowed-label policy and host checks in the core skill. The
shipped JSON alone does not establish that policy or verify an installed shim. Satisfying
checks preserves the host's normal permission flow; it supplies no affirmative allow.

## stop-or-continue

Required observations: the current goal and completion criteria, achieved results, remaining
obligations, and fresh evidence with local source/revision traceability. Build the summary
from observed outcomes, including failed checks and evidence gaps. Count completed items,
check required artifacts, and evaluate time/call budgets deterministically.

Known limitations: `evidence_summary` is a lossy account, not an independent witness or the
underlying sources. Compaction, redaction, and stale observations can erase unfinished work.
An empty summary is missing evidence, not proof that no work remains. Neither a confident
`enough_to_finish` nor repeated agreement proves completion. If required outcomes cannot be
checked, retain `no_advice` and report the gap; further gathering stays within authorization
and budget. Exhausted budget means report a limitation, not fabricate completion.
