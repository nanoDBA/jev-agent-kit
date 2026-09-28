# Live calls and recording real evidence

Live calls send data to TypeSafe and may cost money. Get authorization for that before adding
credentials. The README demos need none of this.

Read [What leaves your machine](../../README.md#what-leaves-your-machine) first.

## Configuration

| Variable | Purpose |
| --- | --- |
| `TYPESAFE_API_KEY` or `TYPESAFE_API_KEY_COMMAND` | API key, or a JSON argument list for a command that prints it from your secret store. |
| `JEV_KIT_HMAC_KEY` | 32 random bytes, base64url-encoded, for keyed hashes such as `target`. Keep it secret. |
| `JEV_KIT_SOURCE_ALLOWLIST` | Approved source types, comma-separated. `agent_context` permits the gate context; `agent_request` permits the routing example's text. |
| `JEV_KIT_ATTESTATION` | Path to a JSON file recording inventory inclusion, its date and DPA status. Live calls are refused without a valid attestation. |
| `JEV_KIT_RECEIPTS_DIR` | Optional absolute receipt folder; defaults to a per-user data folder. |

An attestation has fields such as `{"inventory": true, "inventory_date": "2026-09-26",
"dpa": true}`. Record your actual status and date; do not copy these values.

## Measuring thresholds

The model is pinned to `jev-1.13.0`, and an answer from any other version is rejected.
Measure thresholds from authorized real answers, with independent labels and held-out data.
A threshold belongs to one exact question and set of data rules, so changing either retires
it; see [Receipts](../receipts.md) for how that identity is recorded. Collecting evidence
does not by itself authorize turning on enforce mode.
