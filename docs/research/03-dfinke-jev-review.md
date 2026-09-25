# 03. Review of dfinke/Jev (PowerShell module)

Reviewed at commit 67cedc97 (v0.2.0). Doug Finke's module is the only PowerShell-native Jev
client found and the intended foundation for this repo's PowerShell side.

## Strengths

- PowerShell 7 native `Invoke-RestMethod`; no Python or bash dependency.
- HTTP failures throw. `-MockOnMissingKey` only mocks when the key is absent; network and
  API errors still fail. Closed by default for scripts.
- Exponential backoff on 408/425/429/5xx, capped. Question validation, duplicate-name
  checks, Pester tests.
- Pipeline design: answers promoted onto the input object, full answers retained.
- Companion repo `dfinke/jev-experiments` (Jev Lens) shows good hygiene: stale responses
  discarded by query revision, at most two requests in flight, raw file contents and
  absolute paths never sent.

## Findings (candidate upstream PRs)

| # | Issue | Proposed fix |
| --- | --- | --- |
| 1 | Mock output is indistinguishable from a real answer: returns `model = 'jev-latest'` with confident probabilities from keyword regexes. Only tell is zero token usage. | Set `model = 'mock'`, add `IsMock`, require explicit `-Mock` to enable. |
| 2 | All failures rethrown as one string; 401, 429 and timeouts are indistinguishable to callers. | Typed exceptions or an `ErrorRecord` with a category and status code. |
| 3 | `Get-JevErrorBody` calls `GetResponseStream()`, a Windows PowerShell 5.1 API. On PowerShell 7 the response is `HttpResponseMessage`, so the API error body is silently lost. The module requires 7.0, so that path never works. | Read `$_.ErrorDetails.Message`. |
| 4 | Promoted properties are bare labels (`$d.route`); confidence buried in `.answers`. Invites ungated branching. | Promote confidence and probability alongside the label, or a gate helper. |
| 5 | Defaults to `jev-latest`; no pinning. Retries ignore `Retry-After` and have no jitter. | `-Model` pin with a versioned default; honor `Retry-After`; add jitter. |
| 6 | `.Jev()` ScriptMethod on `System.Array`: one serial HTTP call per record, and it is attached to every array in the session. | Batch or parallel with a cap; scope the type extension or make it opt-in. |
| 7 | No state size budget or redaction; `Get-WinEvent \| Invoke-Jev` serializes whole event records at `-Depth 30`. | Size budget, property projection, redaction hook. |
| 8 | Validation accepts a 1-option Choice and 1-level Score; other clients enforce 2..255 options and 2..10 levels. | Enforce documented limits (verify against current API docs first). |

Operational note: `#requires -Version 7.0` means SQL Agent PowerShell job steps cannot
host it. Use a CmdExec step that invokes `pwsh.exe`.

Decision (2026-09-25, ADR 0001): no dependency and no planned upstream PRs. These findings are
requirements for our own client instead.
