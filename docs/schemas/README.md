# Published JSON Schemas (spec story 82, finding C21)

Versioned JSON Schemas for the kit's external contracts. `schema_version` is 1 across the set.

| File | Contract |
| --- | --- |
| `question-set.schema.json` | the reviewed question-set file (kit shape, criteria per type) |
| `registry.schema.json` | the threshold registry (calibrated entries require a date) |
| `attestation.schema.json` | the live-use inventory/DPA attestation |
| `cli-request.schema.json` | the CLI/`run_json` request, with the `op` dispatch field |
| `cli-response.schema.json` | the CLI/`run_json` response and error envelope |
| `decision-record.schema.json` | one decision record returned to the caller |
| `receipt.schema.json` | one JSONL receipt line |
| `outcome.schema.json` | an outcome line appended by `record_outcome` |

`tests/test_schemas.py` checks each file is a valid JSON Schema document and that worked
examples load through the real loaders (`load_question_set`, `loads_registry`,
`check_attestation`), so the schemas stay tied to the code rather than drifting. Full
schema-validation of arbitrary payloads is left to consumers, since the runtime has no
JSON Schema dependency (ADR 0003).
