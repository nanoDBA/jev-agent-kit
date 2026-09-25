"""Live smoke script for the Jev client (spec stories 89 to 91).

This is a separate operator tool, never imported by ``jev_kit`` itself. It exercises
``jev_kit.engine.run_json`` with synthetic, clearly marked fixtures only: no production
question sets, no production state. It never touches the real network directly (the engine
owns transport construction); the only thing this script can control is which transport gets
passed in, and it passes a ``MockTransport`` unless ``--live`` is given.

Guardrails (spec story 89, ADR 0002 Amendment 1, spec story 52):

- A hard, explicit call cap (``--max-calls``) that the script refuses to exceed even under
  the concurrent scenario; the script simply stops and reports once the cap is reached.
- A live run additionally requires a local attestation file recording that TypeSafe is in the
  data-flow inventory (and whether a DPA is in place). Missing or invalid attestation refuses
  the run before a single call is made. Mock runs never require it.

The engine's ``decide`` core is not implemented yet (``run_json`` currently always raises
``NotImplementedError``); this script treats that as an expected, recorded outcome
(``engine-not-ready``) rather than a crash, so the harness and its tests stay meaningful before
and after that slice lands. Once the engine is implemented, the same script starts producing
real measurements without any change to its own logic (spec story 90: verify served model and
response shapes, record Choice confidence without asserting a formula; spec story 91: latency
by question count, batched-versus-single stability, concurrent-versus-batched wall time).

Standard library only (ADR 0003).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# These are first-party sibling modules (this script lives outside the installed package,
# spec stories 89 to 91), but mypy resolves the editable install through package metadata,
# which has no py.typed marker; the imports themselves are fully typed at runtime.
from jev_kit.engine import run_json  # type: ignore[import-untyped]
from jev_kit.transport import (  # type: ignore[import-untyped]
    MockTransport,
    Transport,
    TransportResponse,
    TransportResult,
)

# Bounded, small worker count for the concurrent-versus-batched probe (spec story 91).
_CONCURRENT_N = 4
_CONCURRENT_WORKERS = 3

# The same question id is reused across the single, five-question and thirteen-question
# scenarios so a future, implemented engine's answer can be compared batched versus single
# (spec story 91: recorded, never asserted, since mock answers are scripted).
_STABILITY_QID = "smoke_stability_choice"

_ATTESTATION_DATE_FORMAT = "%Y-%m-%d"
_REQUIRED_ATTESTATION_KEYS = {"inventory", "inventory_date", "dpa"}


# ---------------------------------------------------------------------------
# Hard call cap
# ---------------------------------------------------------------------------


@dataclass
class CallBudget:
    """A hard, thread-safe ceiling on the number of ``run_json`` calls this run may make."""

    max_calls: int
    used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def try_reserve(self) -> bool:
        """Reserve one call, atomically. Returns False once the cap is reached."""
        with self._lock:
            if self.used >= self.max_calls:
                return False
            self.used += 1
            return True


# ---------------------------------------------------------------------------
# Synthetic fixtures. Marked in-line as smoke fixtures; kept minimal because the engine and
# question-set schema may still evolve (spec stories 82, 90).
# ---------------------------------------------------------------------------


def _synthetic_question(question_id: str) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": f"[SMOKE FIXTURE] Is question {question_id} synthetic?",
        "criteria": ["yes", "no"],
        "kit": {"consequence": "advisory"},
    }


def _synthetic_question_set(question_ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "smoke_fixture": True,
        "questions": {qid: _synthetic_question(qid) for qid in question_ids},
    }


def _synthetic_state(seed: int) -> dict[str, Any]:
    return {
        "smoke_fixture": True,
        "seed": seed,
        "note": "[SMOKE FIXTURE] synthetic state; no production data",
    }


def _build_request(question_ids: list[str], seed: int, mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "question_set": _synthetic_question_set(question_ids),
        "state": _synthetic_state(seed),
        "mode": mode,
        "action_id": f"smoke-{seed}",
    }


def _build_mock_transport(max_calls: int) -> Transport:
    """A scripted transport for mock runs. Never touches the network (spec story 30)."""
    body = json.dumps({"smoke_fixture": True, "model": "mock", "answers": {}}).encode("utf-8")
    outcomes: list[TransportResult] = [
        TransportResponse(200, {}, body) for _ in range(max(max_calls, 1))
    ]
    return MockTransport(outcomes=outcomes)


# ---------------------------------------------------------------------------
# Call wrapper: honors the hard cap and never lets an evolving engine crash the script.
# ---------------------------------------------------------------------------


@dataclass
class CallOutcome:
    status: str  # "ok" | "engine-not-ready" | "cap-reached" | "error"
    elapsed_seconds: float
    response: dict[str, Any] | None = None
    detail: str = ""


def _confidences(outcome: CallOutcome) -> list[float | None]:
    if outcome.response is None:
        return []
    records = outcome.response.get("records", [])
    if not isinstance(records, list):
        return []
    return [record.get("confidence") for record in records if isinstance(record, dict)]


def _invoke(
    request: dict[str, Any], transport: Transport | None, budget: CallBudget
) -> CallOutcome:
    if not budget.try_reserve():
        return CallOutcome(status="cap-reached", elapsed_seconds=0.0)

    start = time.monotonic()
    try:
        response = run_json(request, transport=transport)
    except NotImplementedError:
        # The decide core is filled in on a later slice (see jev_kit.engine). Record this
        # honestly instead of crashing the smoke harness or its tests (spec story 90).
        return CallOutcome(status="engine-not-ready", elapsed_seconds=time.monotonic() - start)
    except Exception as exc:  # an evolving engine must never crash this smoke script
        return CallOutcome(
            status="error", elapsed_seconds=time.monotonic() - start, detail=type(exc).__name__
        )
    return CallOutcome(status="ok", elapsed_seconds=time.monotonic() - start, response=response)


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    question_count: int
    calls_used: int
    wall_seconds: float
    statuses: list[str]
    confidences: list[float | None] = field(default_factory=list)
    note: str = ""


@dataclass
class SmokeReport:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    stability: dict[str, list[float | None]] = field(default_factory=dict)
    cap_reached: bool = False


def run_smoke(transport: Transport | None, budget: CallBudget, mode: str) -> SmokeReport:
    """Run the synthetic workload, stopping the instant the call cap is reached."""
    report = SmokeReport()

    # (a), (b), (c): latency as question count grows, and the stability question threaded
    # through each so a future implemented engine's answer can be compared (spec story 91).
    latency_plan = [
        ("single-question", [_STABILITY_QID]),
        ("five-question", [_STABILITY_QID, *[f"smoke_q{i}" for i in range(1, 5)]]),
        ("thirteen-question", [_STABILITY_QID, *[f"smoke_q{i}" for i in range(1, 13)]]),
    ]

    stability_confidences: dict[str, float | None] = {}
    for name, question_ids in latency_plan:
        request = _build_request(question_ids, seed=len(question_ids), mode=mode)
        outcome = _invoke(request, transport, budget)
        confidences = _confidences(outcome)
        if confidences:
            stability_confidences[name] = confidences[0]
        report.scenarios.append(
            ScenarioResult(
                name=name,
                question_count=len(question_ids),
                calls_used=0 if outcome.status == "cap-reached" else 1,
                wall_seconds=outcome.elapsed_seconds,
                statuses=[outcome.status],
                confidences=confidences,
                note=outcome.detail,
            )
        )
        if outcome.status == "cap-reached":
            report.cap_reached = True
            return report

    if stability_confidences:
        report.stability[_STABILITY_QID] = [
            stability_confidences.get(name) for name, _ in latency_plan
        ]

    # (d): N single-question calls run concurrently versus one batched call.
    concurrent_ids = [f"smoke_conc{i}" for i in range(_CONCURRENT_N)]
    concurrent_requests = [
        _build_request([qid], seed=3000 + index, mode=mode)
        for index, qid in enumerate(concurrent_ids)
    ]

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENT_WORKERS) as executor:
        futures = [
            executor.submit(_invoke, request, transport, budget) for request in concurrent_requests
        ]
        concurrent_outcomes = [future.result() for future in futures]
    concurrent_wall = time.monotonic() - start

    report.scenarios.append(
        ScenarioResult(
            name=f"concurrent-{_CONCURRENT_N}-singles",
            question_count=_CONCURRENT_N,
            calls_used=sum(1 for outcome in concurrent_outcomes if outcome.status != "cap-reached"),
            wall_seconds=concurrent_wall,
            statuses=[outcome.status for outcome in concurrent_outcomes],
            confidences=[c for outcome in concurrent_outcomes for c in _confidences(outcome)],
        )
    )
    if any(outcome.status == "cap-reached" for outcome in concurrent_outcomes):
        report.cap_reached = True
        return report

    batched_request = _build_request(concurrent_ids, seed=4000, mode=mode)
    batched_outcome = _invoke(batched_request, transport, budget)
    report.scenarios.append(
        ScenarioResult(
            name="batched-equivalent",
            question_count=_CONCURRENT_N,
            calls_used=0 if batched_outcome.status == "cap-reached" else 1,
            wall_seconds=batched_outcome.elapsed_seconds,
            statuses=[batched_outcome.status],
            confidences=_confidences(batched_outcome),
            note=batched_outcome.detail,
        )
    )
    if batched_outcome.status == "cap-reached":
        report.cap_reached = True

    return report


# ---------------------------------------------------------------------------
# Attestation gate (spec story 52, ADR 0002 Amendment 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttestationResult:
    valid: bool
    reason: str


def _is_valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, _ATTESTATION_DATE_FORMAT)
    except ValueError:
        return False
    return True


def load_attestation(path: Path) -> AttestationResult:
    """Load and validate the local attestation file required before any live call.

    Requires a JSON object recording ``inventory: true`` (TypeSafe is in the data-flow
    inventory), a valid ``inventory_date`` and a boolean ``dpa`` flag. Never raises: any
    problem comes back as an invalid result so the caller can refuse the live run cleanly.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AttestationResult(False, f"attestation_unreadable: {type(exc).__name__}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return AttestationResult(False, "attestation_not_json")

    if not isinstance(data, dict):
        return AttestationResult(False, "attestation_not_object")

    missing = _REQUIRED_ATTESTATION_KEYS - set(data.keys())
    if missing:
        return AttestationResult(False, "attestation_missing_fields")

    inventory = data["inventory"]
    inventory_date = data["inventory_date"]
    dpa = data["dpa"]

    if inventory is not True:
        return AttestationResult(False, "attestation_inventory_not_recorded")
    if not isinstance(inventory_date, str) or not _is_valid_date(inventory_date):
        return AttestationResult(False, "attestation_inventory_date_invalid")
    if not isinstance(dpa, bool):
        return AttestationResult(False, "attestation_dpa_not_boolean")

    return AttestationResult(True, "ok")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _format_confidences(values: list[float | None]) -> str:
    if not values:
        return "-"
    return ", ".join("-" if value is None else f"{value:.3f}" for value in values)


def _format_optional_confidences(values: list[float | None]) -> str:
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return "n/a (fewer than two recorded values)"
    return f"stdev={statistics.pstdev(present):.4f} over {_format_confidences(values)}"


def render_markdown(
    *,
    today: str,
    live: bool,
    budget: CallBudget,
    attestation_path: Path | None,
    report: SmokeReport,
) -> str:
    lines: list[str] = []
    lines.append(f"# Jev kit live smoke report -- {today}")
    lines.append("")
    lines.append("**All states in this run are synthetic smoke fixtures, never production data.**")
    lines.append("")
    if live:
        lines.append(f"- Mode: LIVE (attestation: {attestation_path})")
    else:
        lines.append("- Mode: MOCK (no network calls; results below are MOCK, not real evidence)")
    lines.append(f"- Call cap (`--max-calls`): {budget.max_calls}")
    lines.append(f"- Calls used: {budget.used}")
    lines.append(f"- Cap reached before the full workload ran: {report.cap_reached}")
    lines.append("")
    lines.append("## Measurements (latency by question count; concurrent vs batched)")
    lines.append("")
    lines.append("| Scenario | Questions | Calls used | Wall time (s) | Status | Confidence |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for scenario in report.scenarios:
        statuses = ", ".join(scenario.statuses)
        lines.append(
            f"| {scenario.name} | {scenario.question_count} | {scenario.calls_used} "
            f"| {scenario.wall_seconds:.4f} | {statuses} "
            f"| {_format_confidences(scenario.confidences)} |"
        )
    lines.append("")
    lines.append("## Stability: batched vs single (recorded only; no formula asserted)")
    lines.append("")
    if report.stability:
        lines.append("| Question | Single | Five-question batch | Thirteen-question batch |")
        lines.append("| --- | --- | --- | --- |")
        for qid, values in report.stability.items():
            padded = [*values, None, None, None][:3]
            lines.append(
                f"| {qid} | {_format_confidences([padded[0]])} "
                f"| {_format_confidences([padded[1]])} | {_format_confidences([padded[2]])} |"
            )
        first_scenario_values = list(report.stability.values())
        if first_scenario_values:
            lines.append("")
            lines.append(_format_optional_confidences(first_scenario_values[0]))
    else:
        lines.append("No confidence values were recorded this run (see scenario statuses above).")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- `engine-not-ready` means `jev_kit.engine.run_json` still raises `NotImplementedError`"
        " (the decide core lands on a later slice); this run only verified the smoke harness"
        " itself, made no real evaluations and needed no live call."
    )
    lines.append("- No secrets, keys or production state are ever written by this script.")
    lines.append("")
    return "\n".join(lines)


def _write_report(
    out_dir: Path,
    *,
    live: bool,
    budget: CallBudget,
    attestation_path: Path | None,
    report: SmokeReport,
) -> Path:
    today = datetime.now(UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"smoke-{today}.md"
    text = render_markdown(
        today=today,
        live=live,
        budget=budget,
        attestation_path=attestation_path,
        report=report,
    )
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jev-kit-smoke",
        description=(
            "Operator smoke script for jev_kit.engine.run_json. Mock by default; synthetic "
            "state only; bounded by a hard call cap; live runs require a valid attestation "
            "(spec stories 89 to 91)."
        ),
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        required=True,
        metavar="N",
        help="hard cap on the number of run_json calls this run may make (required)",
    )
    parser.add_argument(
        "--attestation",
        type=Path,
        default=None,
        metavar="PATH",
        help="path to the local attestation JSON; required for --live runs",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="make live calls (still gated on a valid --attestation); omit to run mock-only",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/smoke"),
        metavar="DIR",
        help="directory to write the dated smoke report into (default docs/smoke)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    max_calls: int = args.max_calls
    live: bool = args.live
    attestation_path: Path | None = args.attestation
    out_dir: Path = args.out

    if max_calls < 1:
        parser.error("--max-calls must be at least 1")

    mode = "shadow"
    transport: Transport | None

    if live:
        if attestation_path is None:
            print("refusing live run: --attestation is required with --live", file=sys.stderr)
            return 1
        attestation = load_attestation(attestation_path)
        if not attestation.valid:
            print(f"refusing live run: {attestation.reason}", file=sys.stderr)
            return 1
        # None means "the engine builds the configured live transport" (spec story 61); this
        # script never opens a connection itself.
        transport = None
    else:
        transport = _build_mock_transport(max_calls)

    budget = CallBudget(max_calls=max_calls)
    report = run_smoke(transport=transport, budget=budget, mode=mode)
    path = _write_report(
        out_dir, live=live, budget=budget, attestation_path=attestation_path, report=report
    )
    print(f"wrote smoke report to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
