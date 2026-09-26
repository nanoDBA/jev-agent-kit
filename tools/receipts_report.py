"""Read and summarize jev-agent-kit receipts JSONL files, for shadow-mode analysis.

Receipts are append-only JSONL (see ``jev_kit.receipts`` and ``jev_kit.engine._write_receipts``):
one ``kind: "decision"`` line per decision made in a call, followed by a single trailing
``kind: "commit"`` marker for that call (``{"call_id": ..., "lines": n}``), and, appended later
and independently of any call batch, ``kind: "outcome"`` lines recording what a caller did with
a previously committed decision.

The commit marker is what lets a reader tell a complete call batch from one truncated mid-write:
a crash or a copy made while a process was still writing can leave decision lines with no commit
marker (or a stale one), and those lines must never be treated as a durable, complete record.
This module honors that by only returning decisions from batches whose commit marker is present
and whose line count matches.

This is pure Python: nothing here is a hard import outside the standard library. DuckDB is an
optional, out-of-process analysis backend (spec E3); it is imported lazily inside
:func:`to_duckdb`, only when that function is actually called, so importing this module never
requires DuckDB to be installed.

Usage as a script::

    python tools/receipts_report.py receipts-1.jsonl receipts-2.jsonl
    python tools/receipts_report.py receipts-1.jsonl --duckdb out.duckdb
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["main", "read_receipts", "summarize", "to_duckdb"]

_VALID_KINDS = frozenset({"decision", "commit", "outcome", "route_correction"})

# A route_correction is only honored when well-formed and permitted (finding H8): the receipt
# schema version, the closed set of downgrade routes, and the closed set of reasons.
_CORRECTION_SCHEMA_VERSION = 1
_CORRECTION_ROUTES = frozenset({"ask", "no_advice"})
_CORRECTION_REASONS = frozenset({"timeout"})


def read_receipts(
    path: Path,
    *,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Parse one receipts JSONL file and return its committed decisions.

    Decision lines are buffered into the call batch they belong to (the run of decision lines
    since the previous commit marker, or since the start of the file). A batch is committed,
    and its decisions included in the result, only when it is immediately followed by a
    ``kind: "commit"`` marker whose ``call_id`` matches the batch's own call id and whose
    ``lines`` count equals the number of decision lines buffered for it. Anything else (a
    mismatched commit marker, or the file ending with no commit marker at all, e.g. a truncated
    copy or a crash mid-write) causes that batch to be dropped rather than trusted.

    ``kind: "outcome"`` lines are not part of any call batch. Each is matched by
    ``decision_id`` to a committed decision (if any) once the whole file has been read, and
    attached under that decision's ``"outcomes"`` key, a list that is always present (possibly
    empty) on every returned decision.

    A line that fails to parse as JSON, or that parses to something other than an object with a
    recognized ``kind``, is skipped rather than raising.

    When ``stats`` is given, it is updated in place (added to whatever it already holds, so a
    caller can accumulate across several files) with:

    - ``"malformed_lines"``: lines skipped because they were not parseable, recognized JSON
      objects.
    - ``"dropped_batches"``: call batches dropped for a missing or mismatched commit marker.
    - ``"committed_calls"``: call batches whose commit marker matched and were included.
    """
    local_stats = {
        "malformed_lines": 0, "dropped_batches": 0, "committed_calls": 0, "dropped_corrections": 0
    }

    committed: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    pending_call_id: Any = None

    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except ValueError:
            local_stats["malformed_lines"] += 1
            continue

        if not isinstance(payload, dict) or payload.get("kind") not in _VALID_KINDS:
            local_stats["malformed_lines"] += 1
            continue

        kind = payload["kind"]
        if kind == "decision":
            if not pending:
                pending_call_id = payload.get("call_id")
            pending.append(payload)
        elif kind == "outcome":
            outcomes.append(payload)
        elif kind == "route_correction":
            corrections.append(payload)
        else:  # kind == "commit"
            if payload.get("call_id") == pending_call_id and payload.get("lines") == len(pending):
                committed.extend(pending)
                local_stats["committed_calls"] += 1
            elif pending:
                local_stats["dropped_batches"] += 1
            pending = []
            pending_call_id = None

    if pending:
        # The file ended mid-batch: no commit marker ever followed these decision lines, so
        # the batch is truncated and cannot be trusted as a complete, durable record.
        local_stats["dropped_batches"] += 1

    by_decision_id: dict[str, dict[str, Any]] = {}
    for decision in committed:
        decision.setdefault("outcomes", [])
        decision_id = decision.get("decision_id")
        if isinstance(decision_id, str):
            by_decision_id[decision_id] = decision

    for outcome in outcomes:
        decision_id = outcome.get("decision_id")
        if isinstance(decision_id, str) and decision_id in by_decision_id:
            by_decision_id[decision_id]["outcomes"].append(outcome)

    # Apply route corrections to their committed decision (finding H8): a correction supersedes
    # the decision's original route (a late accept downgraded to ask/no_advice after the write
    # overran the deadline), so summaries and exports report the corrected route, not the stale
    # accept. The pre-correction route is preserved under "route_before_correction".
    #
    # A correction is applied only when it is well-formed and permitted (finding H8, minor): its
    # schema_version and call_id must match the committed decision, its route/reason must be from
    # the closed downgrade vocabulary, and it may only downgrade an accept, never reverse a
    # decision in some other direction. A malformed or disallowed correction is counted as a
    # dropped correction and does NOT change the recorded route.
    for correction in corrections:
        decision_id = correction.get("decision_id")
        if not isinstance(decision_id, str) or decision_id not in by_decision_id:
            local_stats["dropped_corrections"] = local_stats.get("dropped_corrections", 0) + 1
            continue
        decision = by_decision_id[decision_id]
        corrected_route = correction.get("route")
        fail_reason = correction.get("fail_reason")
        schema_version = correction.get("schema_version")
        # Validate exact types BEFORE any set-membership test: a list- or dict-valued route/reason
        # is unhashable and would raise TypeError from `in`, aborting the whole report; a float or
        # bool schema_version must not be accepted as 1 (finding H8, minor).
        if (
            type(schema_version) is int
            and schema_version == _CORRECTION_SCHEMA_VERSION
            and correction.get("call_id") == decision.get("call_id")
            and isinstance(corrected_route, str)
            and corrected_route in _CORRECTION_ROUTES
            and isinstance(fail_reason, str)
            and fail_reason in _CORRECTION_REASONS
            and decision.get("route") == "accept"  # only a late accept may be downgraded
        ):
            decision.setdefault("route_before_correction", decision.get("route"))
            decision["route"] = corrected_route
            decision["fail_reason"] = fail_reason
        else:
            local_stats["dropped_corrections"] = local_stats.get("dropped_corrections", 0) + 1

    if stats is not None:
        for key, value in local_stats.items():
            stats[key] = stats.get(key, 0) + value

    return committed


def _mean(total: float, count: int) -> float | None:
    return total / count if count else None


def summarize(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a list of committed decision dicts (as returned by :func:`read_receipts`).

    Pure Python: counts decisions by ``route``, ``fail_reason``, ``mode`` and ``is_mock``, plus
    a per-``fingerprint`` breakdown with a count and the mean ``noul`` and ``confidence`` among
    the decisions of that fingerprint that actually carry a numeric value for it.
    """
    by_route: dict[str, int] = {}
    by_fail_reason: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    by_is_mock: dict[bool, int] = {}

    fingerprint_counts: dict[str, int] = {}
    noul_sum: dict[str, float] = {}
    noul_count: dict[str, int] = {}
    confidence_sum: dict[str, float] = {}
    confidence_count: dict[str, int] = {}

    for decision in decisions:
        route = str(decision.get("route"))
        by_route[route] = by_route.get(route, 0) + 1

        fail_reason = decision.get("fail_reason")
        fail_key = str(fail_reason) if fail_reason is not None else "none"
        by_fail_reason[fail_key] = by_fail_reason.get(fail_key, 0) + 1

        mode = str(decision.get("mode"))
        by_mode[mode] = by_mode.get(mode, 0) + 1

        is_mock = bool(decision.get("is_mock", False))
        by_is_mock[is_mock] = by_is_mock.get(is_mock, 0) + 1

        fingerprint = str(decision.get("fingerprint"))
        fingerprint_counts[fingerprint] = fingerprint_counts.get(fingerprint, 0) + 1

        noul = decision.get("noul")
        if isinstance(noul, int | float) and not isinstance(noul, bool):
            noul_sum[fingerprint] = noul_sum.get(fingerprint, 0.0) + float(noul)
            noul_count[fingerprint] = noul_count.get(fingerprint, 0) + 1

        confidence = decision.get("confidence")
        if isinstance(confidence, int | float) and not isinstance(confidence, bool):
            confidence_sum[fingerprint] = confidence_sum.get(fingerprint, 0.0) + float(confidence)
            confidence_count[fingerprint] = confidence_count.get(fingerprint, 0) + 1

    by_fingerprint: dict[str, dict[str, Any]] = {}
    for fingerprint, count in fingerprint_counts.items():
        by_fingerprint[fingerprint] = {
            "count": count,
            "mean_noul": _mean(noul_sum.get(fingerprint, 0.0), noul_count.get(fingerprint, 0)),
            "mean_confidence": _mean(
                confidence_sum.get(fingerprint, 0.0), confidence_count.get(fingerprint, 0)
            ),
        }

    return {
        "total": len(decisions),
        "by_route": by_route,
        "by_fail_reason": by_fail_reason,
        "by_mode": by_mode,
        "by_is_mock": by_is_mock,
        "by_fingerprint": by_fingerprint,
    }


def to_duckdb(path: Path, db_target: str) -> None:
    """Load the committed decisions from ``path`` into a ``receipts_decisions`` table.

    ``db_target`` is a DuckDB database file path (or ``":memory:"``, though nothing will be
    left to inspect once the connection made here closes). DuckDB is an optional, out-of-process
    analysis tool (spec E3), never a runtime dependency of this module: the import happens
    lazily, right here, only when this function is called. If duckdb is not installed, this
    raises a ``RuntimeError`` with an actionable message instead of letting an ``ImportError``
    surface from deep inside the function.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is not installed. Install it with `pip install duckdb` to use "
            "to_duckdb(), or use summarize() for a pure-Python summary instead."
        ) from exc

    decisions = read_receipts(path)

    fd, temp_name = tempfile.mkstemp(suffix=".jsonl")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for decision in decisions:
                handle.write(json.dumps(decision, ensure_ascii=False, default=str))
                handle.write("\n")

        connection = duckdb.connect(db_target)
        try:
            connection.execute("DROP TABLE IF EXISTS receipts_decisions")
            if decisions:
                connection.execute(
                    "CREATE TABLE receipts_decisions AS SELECT * FROM read_json_auto(?)",
                    [str(temp_path)],
                )
            else:
                connection.execute("CREATE TABLE receipts_decisions (decision_id VARCHAR)")
        finally:
            connection.close()
    finally:
        temp_path.unlink(missing_ok=True)


def _read_many(paths: list[Path]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: dict[str, int] = {}
    decisions: list[dict[str, Any]] = []
    for path in paths:
        decisions.extend(read_receipts(path, stats=stats))
    return decisions, stats


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: read one or more receipts files and print a JSON summary."""
    parser = argparse.ArgumentParser(
        prog="receipts_report",
        description="Summarize jev-agent-kit shadow-mode receipts JSONL files.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="one or more receipts .jsonl files")
    parser.add_argument(
        "--duckdb",
        metavar="DB_PATH",
        default=None,
        help="also load the first PATH's committed decisions into this DuckDB database file",
    )
    args = parser.parse_args(argv)

    decisions, stats = _read_many(args.paths)
    summary = summarize(decisions)
    summary["read_stats"] = stats
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))

    if args.duckdb is not None:
        to_duckdb(args.paths[0], args.duckdb)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
