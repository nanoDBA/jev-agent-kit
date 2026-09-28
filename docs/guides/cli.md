# Command-line tool

`jev-kit` reads one JSON request and prints one JSON response. Run these commands from the
repository root so the relative paths work. If `jev-kit` is not on your PATH after installing,
use `python -m jev_kit.cli` instead.

## Send a JSON request

[`examples/requests/route.json`](../../examples/requests/route.json) contains this complete
request:

```json
{
  "schema_version": 1,
  "op": "decide",
  "question_set_path": "skills/jev-runtime/questions/preflight-route.json",
  "state": {
    "request": "Customer says order 1182 was charged twice and asks for a refund."
  },
  "mode": "shadow"
}
```

With both `TYPESAFE_API_KEY` and `TYPESAFE_API_KEY_COMMAND` unset, run:

```sh
jev-kit --input examples/requests/route.json
```

Expected output:

```json
{"schema_version":1,"status":"error","reason":"config","records":[]}
```

Without an API key, the command-line tool cannot ask Jev, so it reports a configuration
error. It has no demo mode; the Python demos in the README replay recorded answers with
`--offline`. With
credentials configured, the same command calls the service, so read
[Live calls](live-calls.md) first.

The CLI also reads one JSON object from standard input. In Bash or zsh:

```sh
jev-kit < examples/requests/route.json
```

In PowerShell 7:

```powershell
Get-Content -Raw examples/requests/route.json | jev-kit
```

Windows PowerShell 5.1 adds a byte-order mark to piped text, which the CLI rejects as invalid
JSON. There, pass the file instead: `jev-kit --input examples/requests/route.json`.

Both forms return the same response. Exit code `0` means the CLI produced a response,
including an error response; it does not mean approval, so read `status` and each record's
`route`. Malformed JSON or an unusable invocation returns exit `2`. The
[CLI schemas](../schemas/README.md) describe the request and response formats, including
`decide_batch` and `record_outcome`.

## Preview a skill install

```sh
jev-kit install --scope repo
```

This prints a JSON plan with `"applied":false` for `.claude/skills`, `.agents/skills` and
`.hermes/skills`, and changes no files. Use `--scope user` to preview a user-wide install.
The installer only writes changes when you pass `--apply`. It links to `skills/jev-runtime`
where it can and otherwise copies the files; moving or deleting a linked checkout breaks the
install. Installing the skill and registering a tool hook are separate steps; see the guide
for your host ([Claude Code](claude-code.md), [Codex](codex.md), [Hermes Agent](hermes.md)).

## Audit a skill

```sh
jev-kit audit examples/suspicious-skill
```

Prints the findings as JSON and exits `1` when any finding is high severity. The audit only
reads files; it never runs them or calls a model.
