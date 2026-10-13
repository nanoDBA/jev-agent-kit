# Use answers with your own policy

Your application decides how to use Jev. It can act on a selected label, use a provisional
cutoff, combine scores, ask another model, or involve a person. Calibration is one way to
evaluate those choices, not a requirement for consuming an answer.

## An answer and a route are different

`decide()` and the JSON CLI return records with typed answer fields:

| Question | Answer fields |
| --- | --- |
| Choice | `label`, `distribution`, `confidence` |
| Score | `value`, `distribution`, `confidence` |
| Noul | `noul`, the probability of yes |

The separate `route` field is the result of the kit's built-in threshold policy. That policy
uses calibrated registry entries. A successful shadow call still returns the answer, with
`route: "no_advice"`. Your application can use that answer immediately. Check `status`,
`fail_reason` and whether the answer exists to distinguish this from a failed call.
An uncalibrated gate in enforce mode can retain an answer while reporting
`fail_reason: "uncalibrated"`. That is a routing-policy status, not a transport failure.

For example, the routing demo chooses the returned handler label without a registry:

```sh
python examples/route_request.py --offline
```

It prints `route:   no_advice` and `handled: human`. The selected handler comes from a labelled
recorded answer in this offline run. The script prints the selection; it does not call a
person or another model. Without `--offline` and with credentials configured, it uses the
same policy on a live answer. The demo's error and `unclear` fallback is `specialist_llm`.
Change that fallback, the question's criteria, or the policy to fit your application.

## Choose components independently

- Use returned answer fields for a custom policy. This works without thresholds and does
  not require enabling hook enforce mode.
- Use `route` when you want the built-in calibrated policy. The
  [calibration guide](calibration.md) describes how to configure it.
- Install the host hooks if you want their particular behavior: shadow records only;
  enforce asks or blocks when checks do not pass and otherwise leaves host permissions
  unchanged. These hooks do not implement arbitrary approval policies.
- Use a custom integration for other approval, escalation or automation behavior. The
  skill supports helping you build it, including decisions delegated to another model.

The bundled client still applies its schema validation, egress rules, model pin, deadlines,
receipt handling and [live-call configuration](live-calls.md). Direct answer consumption
does not disable those mechanisms. If they do not fit, adapting the kit or using the
[TypeSafe API](https://docs.typesafe.ai/api.md) directly are alternatives.

Be clear about what you measured, what is a model judgment, and what is replayed or simulated.
The level of evaluation, acceptable uncertainty and resulting action are your choices.
