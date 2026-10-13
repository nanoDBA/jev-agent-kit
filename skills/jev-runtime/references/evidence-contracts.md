# Evidence contracts

This reference describes the shipped question sets and what their inputs can tell the
model. Adapt the sets and choose the resulting actions for your application. All four sets
ship without calibrated thresholds; that does not prevent consuming their typed answers.
The JSON files define the current wording, types and transforms.

## Task index

| Task | Question set | State fields | Question IDs and types | Consequence |
| --- | --- | --- | --- | --- |
| [Choose a handler](#preflight-route) | [preflight-route.json](../questions/preflight-route.json) | `request` | `route` (choice) | advisory |
| [Review an output](#postflight-verify) | [postflight-verify.json](../questions/postflight-verify.json) | `request`, `output` | `addresses_request` (noul), `contains_citation` (noul), `within_scope` (noul) | advisory |
| [Assess a proposed tool call](#tool-call-gate) | [tool-call-gate.json](../questions/tool-call-gate.json) | `command`, `cmd_parse_confident`, `cmd_recursive_delete`, `cmd_force_flag`, `cmd_targets_root_or_system_path`, `cmd_targets_home_directory`, `cmd_uses_elevation`, `cmd_network_download`, `cmd_pipes_to_shell`, `cmd_modifies_permissions`, `cmd_git_history_rewrite`, `cmd_package_install`, `target`, `context` | `destructive` (noul), `exfiltrates` (noul), `widens_permission` (noul) | gate |
| [Decide whether to finish](#stop-or-continue) | [stop-or-continue.json](../questions/stop-or-continue.json) | `goal`, `evidence_summary` | `enough_to_finish` (noul), `another_step_would_help` (noul) | advisory |

## preflight-route

Useful inputs: the current request and any context needed to choose among handlers. This
set declares only `request`, with source type `agent_request`.

Known limitations: the menu is fixed, and its `human` criteria encode this example's policy
for high-consequence requests. Replace the criteria or write another set when your policy
differs. A returned label does not establish that a handler exists; your code maps labels
to implementations. `unclear` is available for ambiguity, with the fallback chosen by you.
The routing demo consumes the returned label without a calibrated threshold.

## postflight-verify

Useful inputs: the request and the output to assess. The set asks about relevance, scope
and whether a citation is present. It can help select, revise or rank outputs.

Known limitations: the schema has no source excerpts or test-result field.
`contains_citation` detects the presence of a citation, not its accuracy. A relevance
judgment does not demonstrate that tests passed. If your task needs those distinctions,
provide appropriate evidence in a new question set or combine the answers with local
checks. The amount of verification and the action taken are application choices.

## tool-call-gate

Useful inputs: the proposed command, target, description and locally computed properties.
All three questions describe hazards, so a high yes probability indicates more of the
named hazard. Your integration decides how to use that information.

Known limitations: command egress keeps the basename and removes subcommands, flags and
arguments. Hooks add heuristic `cmd_*` booleans; `null` means unknown. Targets become
identifier tokens; `context` is free text from an allowlisted source. These transforms can
remove details relevant to the judgment.

The optional shipped hooks use calibrated routes and allowed labels in enforce mode,
and ask or block when the command parse is unconfident. When checks pass they return no
host decision. A custom application can instead consume the hazard probabilities, apply
its own cutoffs, ask another model, or implement a different action/approval policy.

## stop-or-continue

Useful inputs: the goal and an `evidence_summary` of progress. The answers can drive another
agent turn, select a follow-up, or end a workflow.

Known limitations: a summary can omit work or become stale. A confident `enough_to_finish`
answer is a judgment on the supplied summary, not an independent record of completion.
Choose whether to use it directly, combine it with artifact checks, or gather more evidence.
