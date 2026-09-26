"""Command-line entry point: one JSON request in, one JSON response out.

This is the seam a scheduler, CI job or hook uses to call the engine without writing
Python (spec story 9). The contract:

- Read one JSON request object from standard input, or from ``--input PATH`` if given.
- Call ``jev_kit.engine.run_json`` and write its response, compact and UTF-8, to
  standard output, followed by a newline.
- Exit 0 whenever a response was written, whether that response is an "ok" result or
  the engine's own "error" envelope (spec story 10; both are valid decisions a host
  must act on, and hosts are told to treat an error envelope as "ask" -- spec story 26).
- Exit 2 only when the invocation itself is unusable: the input is not valid JSON, the
  input is not a JSON object, the input file cannot be read, or the command line has
  unknown arguments. In every exit-2 case a config error envelope is still written to
  standard output so a host that only reads stdout never sees an empty response, and a
  short message goes to standard error. The offending input text itself is never echoed
  into either stream.

See ``jev_kit.engine`` for the exact request and response shapes (spec story 82).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from jev_kit import SCHEMA_VERSION

_ERROR_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "status": "error",
    "reason": "config",
    "records": [],
}

_ERROR_INTERNAL: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "status": "error",
    "reason": "internal",
    "records": [],
}


class _ArgumentError(Exception):
    """Raised in place of argparse's default SystemExit(2).

    This lets ``main`` respond the same way as any other unusable invocation: a config
    error envelope on stdout, a short message on stderr, and a return of 2, rather than
    argparse exiting the process directly with nothing written to stdout.
    """


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentError(message)


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="jev-kit",
        description="Run one Jev decision request from JSON and print the JSON response.",
    )
    parser.add_argument(
        "--input",
        metavar="PATH",
        default=None,
        help="read the JSON request from PATH instead of standard input",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"jev-kit {SCHEMA_VERSION}",
    )
    return parser


def _write_response(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")


def _fail_invocation(message: str) -> int:
    """Handle an unusable invocation: short message to stderr, config envelope to stdout."""
    sys.stderr.write(f"{message}\n")
    _write_response(_ERROR_CONFIG)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentError as exc:
        return _fail_invocation(str(exc))

    if args.input is not None:
        try:
            text = Path(args.input).read_text(encoding="utf-8")
        except OSError as exc:
            return _fail_invocation(f"could not read input file: {exc}")
    else:
        text = sys.stdin.read()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _fail_invocation(f"invalid JSON on input: {exc}")

    if not isinstance(parsed, dict):
        return _fail_invocation("request must be a JSON object")

    # Lazy import: the engine is a heavier, later-slice module, and this keeps the CLI's
    # own argument and JSON handling importable and testable independent of it.
    from jev_kit.engine import run_json

    try:
        response = run_json(parsed)
    except Exception:  # defensive fallback only: run_json is specified to never raise
        # for runtime conditions, but an unexpected engine bug is not an unusable
        # invocation -- it still gets a response (the internal error envelope) and an
        # exit of 0, so a host never mistakes "the engine misbehaved" for "the CLI
        # itself could not run".
        _write_response(_ERROR_INTERNAL)
        return 0

    _write_response(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
