"""Live smoke script for the Jev client (spec stories 89 to 91).

This is a separate operator tool, never imported by ``jev_kit`` itself. It exercises the real
decide core (``jev_kit.engine.decide``) with synthetic, clearly marked fixtures only: no
production question sets, no production state. A mock run never touches the real network (the
only transport it builds is ``jev_kit.transport.MockTransport``, wrapped so every attempt this
run makes, including retries, is counted against the hard cap); a ``--live`` run hands the
engine no transport at all, so the engine builds its own live transport from the resolved API
key, and this script never opens a connection itself.

Guardrails (spec story 89, ADR 0002 Amendment 1, spec story 52):

- A hard, explicit call cap (``--max-calls``) that the script refuses to exceed even under the
  concurrent scenario or an engine retry loop; the script simply stops and reports once the cap
  is reached (finding C14: every outbound attempt is counted, not just top-level calls).
- A live run additionally requires a local attestation file recording that TypeSafe is in the
  data-flow inventory (and whether a DPA is in place). Missing or invalid attestation refuses
  the run before a single call is made. Mock runs never require it.

Finding C20: earlier versions of this script built question sets in a shape the parser already
rejected and declared success without ever checking the engine's outcome, so a broken fixture
looked identical to a working one. This version builds schema-valid fixtures for all three
question types (noul, choice, score) in the current shape (finding C12: ``kit.consequence``,
Choice criteria as an option map, Score criteria as an ordered level array), scripts a
``MockTransport`` whose bodies validate against those fixtures (matching answers, model
``jev-1.13.0``), calls the real engine through ``jev_kit.engine.decide``, and only reports a
probe as a successful round trip when the response status is ``ok`` and its records carry no
unexpected fail reason. A mock gate routing to ``ask`` and a mock advisory routing to
``no_advice`` are both expected, successful round trips (the route-resolution matrix never lets
a mock reach ``accept``); a probe that instead hits the call cap or a genuine validation failure
is reported as such, never silently as success. Probes this run never got to are labeled "not
performed", not folded into the success count.

Standard library only for this script's own logic (ADR 0003); it imports the sibling
``jev_kit`` package, the thing under test.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

# These are first-party sibling modules (this script lives outside the installed package,
# spec stories 89 to 91), but mypy resolves the editable install through package metadata,
# which has no py.typed marker; the imports themselves are fully typed at runtime.
from jev_kit.deadline import Deadline  # type: ignore[import-untyped]
from jev_kit.engine import EngineConfig, decide, run_json  # type: ignore[import-untyped]
from jev_kit.errors import FailReason  # type: ignore[import-untyped]
from jev_kit.questionset import load_question_set  # type: ignore[import-untyped]
from jev_kit.ratebudget import RateBudget  # type: ignore[import-untyped]
from jev_kit.receipts import ReceiptWriter  # type: ignore[import-untyped]
from jev_kit.transport import (  # type: ignore[import-untyped]
    MockTransport,
    TransportFailure,
    TransportResponse,
    TransportResult,
)

# The pinned model every synthetic fixture declares and every scripted reply claims to be
# answering as (finding C20: a mismatch here would make every probe fail model validation).
MODEL = "jev-1.13.0"

# Bounded, small worker count for the concurrent-versus-batched probe (spec story 91).
_CONCURRENT_N = 4
_CONCURRENT_WORKERS = 3

# Reused across the single, five-question and thirteen-question latency scenarios so a
# calibrated confidence can be compared batched versus single (spec story 91: recorded, never
# asserted, since mock answers are scripted). It is always answered as a Choice question.
_STABILITY_QID = "smoke_stability_choice"
_LATENCY_SIZES = (1, 5, 13)

_ATTESTATION_DATE_FORMAT = "%Y-%m-%d"
_REQUIRED_ATTESTATION_KEYS = {"inventory", "inventory_date", "dpa"}

_ROUND_TRIP_ROUTES = frozenset({"accept", "ask", "no_advice"})


# ---------------------------------------------------------------------------
# Synthetic fixtures in the current question-set shape (finding C12): each question carries
# the API fields (type, instructions, criteria) plus a separate kit object with the
# consequence class. Choice criteria is an option -> description map; Score criteria is an
# ordered array of level strings; Noul takes no options. These are validated through the real
# parser, never hand-waved (finding C20).
# ---------------------------------------------------------------------------


def _question_spec(question_type: str) -> dict[str, Any]:
    if question_type == "noul":
        return {
            "type": "noul",
            "instructions": "[SMOKE FIXTURE] Does the synthetic condition hold?",
            "kit": {"consequence": "advisory"},
        }
    if question_type == "choice":
        return {
            "type": "choice",
            "instructions": "[SMOKE FIXTURE] Which synthetic option applies?",
            "criteria": {"yes": "The synthetic condition holds.", "no": "It does not."},
            "kit": {"consequence": "gate", "gate": {"allow_labels": ["no"]}},
        }
    if question_type == "score":
        return {
            "type": "score",
            "instructions": "[SMOKE FIXTURE] Rate the synthetic condition.",
            "criteria": ["low", "medium", "high"],
            "kit": {"consequence": "advisory"},
        }
    raise ValueError(f"unknown synthetic question type: {question_type}")


def _answer_for(question_type: str) -> dict[str, Any]:
    """A scripted answer that passes ``jev_kit.validation`` for the matching question spec."""
    if question_type == "noul":
        return {"type": "noul", "noul": 0.9}
    if question_type == "choice":
        return {
            "type": "choice",
            "probabilities": {"yes": 0.8, "no": 0.2},
            "choice": "yes",
            "confidence": 0.8,
        }
    if question_type == "score":
        # levels are ["low", "medium", "high"] -> keys "0", "1", "2"; score must equal the
        # probability-weighted mean of level indices (finding R06), so an all-weight-on-top
        # distribution keeps this exact and simple to verify.
        return {
            "type": "score",
            "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0},
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "confidence": 0.9,
            "score": 2.0,
        }
    raise ValueError(f"unknown synthetic question type: {question_type}")


def _question_set(set_id: str, ids_and_types: Sequence[tuple[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": set_id,
        "version": "1",
        "model": MODEL,
        "escalation_target": "smoke-operator",
        "questions": {qid: _question_spec(qtype) for qid, qtype in ids_and_types},
    }


def _mock_body(ids_and_types: Sequence[tuple[str, str]]) -> bytes:
    answers = {qid: _answer_for(qtype) for qid, qtype in ids_and_types}
    return json.dumps({"model": MODEL, "answers": answers}).encode("utf-8")


# One schema-valid, single-question set per type, covering noul, choice and score (finding
# C20). Each loads through the real parser; ``tests/test_smoke.py`` pins that directly.
SYNTHETIC_QUESTION_SETS: dict[str, dict[str, Any]] = {
    "noul": _question_set("smoke-noul-set", [("smoke_noul_q", "noul")]),
    "choice": _question_set("smoke-choice-set", [("smoke_choice_q", "choice")]),
    "score": _question_set("smoke-score-set", [("smoke_score_q", "score")]),
}


def validate_synthetic_fixtures() -> None:
    """Load every synthetic question set through the real parser before using it.

    Raises whatever ``jev_kit.questionset.load_question_set`` raises on a bad fixture, so a
    schema drift is caught loudly here rather than producing a silent false "success" further
    down the pipeline (finding C20).
    """
    for qset in SYNTHETIC_QUESTION_SETS.values():
        load_question_set(qset)


def _synthetic_state(seed: int) -> dict[str, Any]:
    return {
        "smoke_fixture": True,
        "seed": seed,
        "note": "[SMOKE FIXTURE] synthetic state; no production data",
    }


def _build_request(qset: dict[str, Any], seed: int, mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "question_set": qset,
        "state": _synthetic_state(seed),
        "mode": mode,
        "action_id": f"smoke-{seed}",
    }


# ---------------------------------------------------------------------------
# The planned workload: every decide/run_json call this run will attempt, in the exact order
# it will attempt them, so a shared scripted transport's outcomes line up with the calls.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlannedCall:
    name: str
    request: dict[str, Any]
    ids_and_types: tuple[tuple[str, str], ...]


def _type_probe_calls(mode: str) -> list[_PlannedCall]:
    """One round trip per question type: noul, choice, score (finding C20, spec story 90)."""
    calls = []
    for index, (qtype, qset) in enumerate(SYNTHETIC_QUESTION_SETS.items()):
        (qid,) = qset["questions"].keys()
        ids_and_types = ((qid, qtype),)
        calls.append(
            _PlannedCall(
                name=f"type-{qtype}",
                request=_build_request(qset, seed=100 + index, mode=mode),
                ids_and_types=ids_and_types,
            )
        )
    return calls


def _latency_calls(mode: str) -> list[_PlannedCall]:
    """Latency as question count grows, with a stability question threaded through each
    (spec story 91). Every question is Choice, so the scripted answers stay simple."""
    calls = []
    for size in _LATENCY_SIZES:
        ids_and_types: tuple[tuple[str, str], ...] = ((_STABILITY_QID, "choice"),)
        ids_and_types += tuple((f"smoke_lat_{size}_{i}", "choice") for i in range(1, size))
        qset = _question_set(f"smoke-latency-{size}-set", ids_and_types)
        calls.append(
            _PlannedCall(
                name=f"latency-{size}-question",
                request=_build_request(qset, seed=size, mode=mode),
                ids_and_types=ids_and_types,
            )
        )
    return calls


def _concurrent_calls(mode: str) -> list[_PlannedCall]:
    """N single-question calls run concurrently (spec story 91). Every one asks the exact
    same question, so whichever scripted reply a thread's send happens to consume is still a
    valid, matching answer regardless of scheduling order."""
    qset = SYNTHETIC_QUESTION_SETS["choice"]
    (qid,) = qset["questions"].keys()
    ids_and_types = ((qid, "choice"),)
    return [
        _PlannedCall(
            name=f"concurrent-{i}",
            request=_build_request(qset, seed=3000 + i, mode=mode),
            ids_and_types=ids_and_types,
        )
        for i in range(_CONCURRENT_N)
    ]


def _batched_call(mode: str) -> _PlannedCall:
    """One call whose question set batches as many questions as the concurrent probe used,
    for a batched-versus-concurrent wall-time comparison (spec story 91)."""
    ids_and_types = tuple((f"smoke_batch_{i}", "choice") for i in range(_CONCURRENT_N))
    qset = _question_set("smoke-batched-set", ids_and_types)
    return _PlannedCall(
        name="batched-equivalent",
        request=_build_request(qset, seed=4000, mode=mode),
        ids_and_types=ids_and_types,
    )


# ---------------------------------------------------------------------------
# Hard call cap: every outbound attempt through a mock transport, including retries, is
# counted (finding C14). ``CallBudget`` is a simpler pre-invocation gate used only for the
# (untested-against-real-network) live path, where there is no transport of ours to wrap.
# ---------------------------------------------------------------------------


@dataclass
class CallBudget:
    """A hard, thread-safe ceiling on the number of top-level calls a live run may attempt."""

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


@dataclass
class CappedTransport:
    """Wrap a mock transport and hard-refuse once ``max_calls`` sends have been forwarded.

    Every attempt the engine makes reaches this wrapper first, including retries the engine
    issues within a single ``decide`` call (finding C14): the cap can bite mid retry loop, not
    only once per top-level call. Thread-safe, so the concurrent probe cannot punch through it.
    Only ever used to wrap a ``MockTransport`` in this script, so ``is_mock`` is fixed True,
    matching the wrapped transport's own fixed provenance (finding C01): a capped-out send is
    still, unambiguously, a mock send, never claimed as anything else.
    """

    is_mock: ClassVar[bool] = True

    inner: MockTransport
    max_calls: int
    sent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def send(self, body: bytes, deadline: Deadline) -> TransportResult:
        with self._lock:
            if self.sent >= self.max_calls:
                return TransportFailure(FailReason.RATE_BUDGET, "smoke_cap_reached")
            self.sent += 1
        return self.inner.send(body, deadline)


# ---------------------------------------------------------------------------
# Call wrapper: actually checks the engine's outcome before calling anything a success
# (finding C20), instead of only checking that a call returned without raising.
# ---------------------------------------------------------------------------


@dataclass
class CallOutcome:
    status: str  # "ok" | "cap_reached" | "failed" | "error" | "exception"
    elapsed_seconds: float
    response: dict[str, Any] | None = None
    fail_reasons: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    confidences: list[float | None] = field(default_factory=list)
    detail: str = ""


def _classify(response: Any) -> tuple[str, list[str], list[str], list[float | None]]:
    """Turn one decide/run_json response into a status, never trusting "it returned" alone.

    "ok" only when the envelope says ``status: ok``, records are present, and no record
    carries an unexpected fail reason. A cap-triggered failure (this script's own
    ``smoke_cap_reached`` reservation, surfaced as ``rate_budget``) is reported separately as
    "cap_reached", not folded into either success or a genuine failure (finding C14, C20).
    """
    if not isinstance(response, dict) or response.get("status") != "ok":
        reason = response.get("reason") if isinstance(response, dict) else None
        return "error", ([str(reason)] if reason else ["malformed_response"]), [], []
    records = response.get("records")
    if not isinstance(records, list) or not records:
        return "error", ["no_records"], [], []
    fail_reasons = [
        str(r["fail_reason"]) for r in records if isinstance(r, dict) and r.get("fail_reason")
    ]
    routes = [str(r["route"]) for r in records if isinstance(r, dict) and "route" in r]
    confidences: list[float | None] = [
        r.get("confidence") if isinstance(r, dict) else None for r in records
    ]
    if fail_reasons:
        if all(fr == FailReason.RATE_BUDGET.value for fr in fail_reasons):
            return "cap_reached", fail_reasons, routes, confidences
        return "failed", fail_reasons, routes, confidences
    if any(route not in _ROUND_TRIP_ROUTES for route in routes):
        return "failed", fail_reasons, routes, confidences
    return "ok", fail_reasons, routes, confidences


def _invoke(
    request: dict[str, Any], call: Callable[[dict[str, Any]], dict[str, Any]]
) -> CallOutcome:
    start = time.monotonic()
    try:
        response = call(request)
    except Exception as exc:  # an evolving engine must never crash this smoke script
        return CallOutcome(
            status="exception", elapsed_seconds=time.monotonic() - start, detail=type(exc).__name__
        )
    elapsed = time.monotonic() - start
    status, fail_reasons, routes, confidences = _classify(response)
    return CallOutcome(
        status=status,
        elapsed_seconds=elapsed,
        response=response if isinstance(response, dict) else None,
        fail_reasons=fail_reasons,
        routes=routes,
        confidences=confidences,
    )


def _confidence_for(outcome: CallOutcome, question_id: str) -> float | None:
    if outcome.response is None:
        return None
    records = outcome.response.get("records")
    if not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, dict) and record.get("question_id") == question_id:
            confidence = record.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                return None
            return float(confidence)
    return None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    question_count: int
    status: str
    wall_seconds: float
    fail_reasons: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    confidences: list[float | None] = field(default_factory=list)
    note: str = ""


@dataclass
class SmokeReport:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    stability: dict[str, list[float | None]] = field(default_factory=dict)
    cap_reached: bool = False


def _scenario_from_outcome(name: str, question_count: int, outcome: CallOutcome) -> ScenarioResult:
    note = outcome.detail
    if outcome.status == "cap_reached":
        note = "not performed: the hard call cap was reached before this round trip"
    return ScenarioResult(
        name=name,
        question_count=question_count,
        status=outcome.status,
        wall_seconds=outcome.elapsed_seconds,
        fail_reasons=list(outcome.fail_reasons),
        routes=list(outcome.routes),
        confidences=list(outcome.confidences),
        note=note,
    )


def _scenario_from_outcomes(
    name: str, question_count: int, wall_seconds: float, outcomes: list[CallOutcome]
) -> ScenarioResult:
    statuses = {outcome.status for outcome in outcomes}
    if statuses == {"ok"}:
        status = "ok"
    elif statuses <= {"ok", "cap_reached"}:
        status = "cap_reached"
    else:
        status = "failed"
    return ScenarioResult(
        name=name,
        question_count=question_count,
        status=status,
        wall_seconds=wall_seconds,
        fail_reasons=[fr for outcome in outcomes for fr in outcome.fail_reasons],
        routes=[route for outcome in outcomes for route in outcome.routes],
        confidences=[c for outcome in outcomes for c in outcome.confidences],
    )


# ---------------------------------------------------------------------------
# Mock workload: the real engine, through the real parser, over a scripted transport that
# never touches the network (spec stories 89 to 91; finding C20).
# ---------------------------------------------------------------------------


def run_smoke(
    *, max_calls: int, mode: str, writer: ReceiptWriter
) -> tuple[SmokeReport, CappedTransport]:
    validate_synthetic_fixtures()

    type_calls = _type_probe_calls(mode)
    latency_calls = _latency_calls(mode)
    concurrent_calls = _concurrent_calls(mode)
    batched = _batched_call(mode)
    ordered = [*type_calls, *latency_calls, *concurrent_calls, batched]

    outcomes: list[TransportResult] = [
        TransportResponse(200, {}, _mock_body(call.ids_and_types)) for call in ordered
    ]
    mock = MockTransport(outcomes=outcomes)
    transport = CappedTransport(inner=mock, max_calls=max_calls)
    config = EngineConfig(writer=writer, rate_budget=RateBudget())

    def call_engine(request: dict[str, Any]) -> dict[str, Any]:
        # jev_kit has no py.typed marker for this out-of-package script (see the import
        # comments above), so mypy sees decide's return as Any; the real signature is
        # dict[str, Any], pinned in jev_kit.engine.
        return cast(dict[str, Any], decide(request, transport=transport, config=config))

    report = SmokeReport()

    for call in type_calls:
        outcome = _invoke(call.request, call_engine)
        report.scenarios.append(_scenario_from_outcome(call.name, len(call.ids_and_types), outcome))

    stability_values: list[float | None] = []
    for call in latency_calls:
        outcome = _invoke(call.request, call_engine)
        report.scenarios.append(_scenario_from_outcome(call.name, len(call.ids_and_types), outcome))
        stability_values.append(_confidence_for(outcome, _STABILITY_QID))
    if any(value is not None for value in stability_values):
        report.stability[_STABILITY_QID] = stability_values

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENT_WORKERS) as executor:
        futures = [executor.submit(_invoke, call.request, call_engine) for call in concurrent_calls]
        concurrent_outcomes = [future.result() for future in futures]
    concurrent_wall = time.monotonic() - start
    report.scenarios.append(
        _scenario_from_outcomes(
            f"concurrent-{_CONCURRENT_N}-singles",
            _CONCURRENT_N,
            concurrent_wall,
            concurrent_outcomes,
        )
    )

    batched_outcome = _invoke(batched.request, call_engine)
    report.scenarios.append(
        _scenario_from_outcome(batched.name, len(batched.ids_and_types), batched_outcome)
    )

    report.cap_reached = any(scenario.status == "cap_reached" for scenario in report.scenarios)
    return report, transport


def _run_live(*, budget: CallBudget, mode: str) -> SmokeReport:
    """Same planned workload as the mock run, through ``run_json`` with no transport of ours
    (the engine builds its own live transport). Never exercised against a real network by this
    repository's tests; the cap here is a simple pre-invocation gate, since there is no
    transport of ours to wrap for a live call."""
    report = SmokeReport()
    calls = [
        *_type_probe_calls(mode),
        *_latency_calls(mode),
        *_concurrent_calls(mode),
        _batched_call(mode),
    ]
    for call in calls:
        if not budget.try_reserve():
            report.scenarios.append(
                ScenarioResult(
                    name=call.name,
                    question_count=len(call.ids_and_types),
                    status="cap_reached",
                    wall_seconds=0.0,
                    note="not performed: the hard call cap was reached before this round trip",
                )
            )
            report.cap_reached = True
            continue
        outcome = _invoke(call.request, run_json)
        report.scenarios.append(
            _scenario_from_outcome(call.name, len(call.ids_and_types), outcome)
        )
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
# Report rendering
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
    max_calls: int,
    calls_used: int,
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
        lines.append(
            "- Mode: MOCK (no network calls; every round trip below went through the real "
            "`jev_kit.engine.decide` over a scripted `MockTransport`)"
        )
    lines.append(f"- Call cap (`--max-calls`): {max_calls}")
    lines.append(f"- Calls used: {calls_used}")
    lines.append(f"- Cap reached before the full workload ran: {report.cap_reached}")
    lines.append("")

    lines.append("## Per-type round trip (finding C20; spec story 90)")
    lines.append("")
    lines.append("| Type | Scenario | Status | Route(s) | Fail reason(s) | Confidence |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for scenario in report.scenarios:
        if not scenario.name.startswith("type-"):
            continue
        qtype = scenario.name.removeprefix("type-")
        lines.append(
            f"| {qtype} | {scenario.name} | {scenario.status} "
            f"| {', '.join(scenario.routes) or '-'} | {', '.join(scenario.fail_reasons) or '-'} "
            f"| {_format_confidences(scenario.confidences)} |"
        )
    lines.append("")

    lines.append(
        "## Measurements: latency by question count; concurrent vs batched (spec story 91)"
    )
    lines.append("")
    lines.append("| Scenario | Questions | Status | Wall time (s) | Fail reason(s) |")
    lines.append("| --- | --- | --- | --- | --- |")
    for scenario in report.scenarios:
        if scenario.name.startswith("type-"):
            continue
        lines.append(
            f"| {scenario.name} | {scenario.question_count} | {scenario.status} "
            f"| {scenario.wall_seconds:.4f} | {', '.join(scenario.fail_reasons) or '-'} |"
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

    lines.append("## Not performed")
    lines.append("")
    not_performed = [s for s in report.scenarios if s.status == "cap_reached"]
    if not_performed:
        for scenario in not_performed:
            lines.append(f"- {scenario.name}: {scenario.note or 'not performed (cap reached)'}")
    else:
        lines.append("- None; every planned probe ran to completion within the call cap.")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    failed = [s for s in report.scenarios if s.status in ("failed", "error", "exception")]
    if failed:
        lines.append("- FAILED or noteworthy probes (not a successful round trip):")
        for scenario in failed:
            reasons = ", ".join(scenario.fail_reasons) or scenario.note or "-"
            lines.append(f"  - {scenario.name}: status={scenario.status} ({reasons})")
    else:
        lines.append("- No probe reported a failure or an unexpected fail reason this run.")
    lines.append(
        "- Mock provenance: a mock record's `model` field is always `mock`; the model the "
        "scripted response simulated answering as is carried separately in `simulated_model` "
        "(finding C16), never conflated with a real served model."
    )
    lines.append(
        "- A mock gate always routes to `ask` and a mock advisory always routes to `no_advice` "
        "(route-resolution matrix, finding C01); both count as a successful round trip here, "
        "since the point is that the engine evaluated and validated the answer, not that it "
        "accepted it."
    )
    lines.append("- No secrets, keys or production state are ever written by this script.")
    lines.append("")
    return "\n".join(lines)


def _write_report(
    out_dir: Path,
    *,
    live: bool,
    max_calls: int,
    calls_used: int,
    attestation_path: Path | None,
    report: SmokeReport,
) -> Path:
    today = datetime.now(UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"smoke-{today}.md"
    text = render_markdown(
        today=today,
        live=live,
        max_calls=max_calls,
        calls_used=calls_used,
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
            "Operator smoke script for jev_kit.engine. Mock by default; synthetic state only; "
            "bounded by a hard call cap; live runs require a valid attestation "
            "(spec stories 89 to 91)."
        ),
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        required=True,
        metavar="N",
        help="hard cap on the number of outbound attempts this run may make (required)",
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

    if live:
        if attestation_path is None:
            print("refusing live run: --attestation is required with --live", file=sys.stderr)
            return 1
        attestation = load_attestation(attestation_path)
        if not attestation.valid:
            print(f"refusing live run: {attestation.reason}", file=sys.stderr)
            return 1
        budget = CallBudget(max_calls=max_calls)
        report = _run_live(budget=budget, mode=mode)
        calls_used = budget.used
    else:
        # Receipts from a mock smoke run are kept alongside its report, never in the real
        # process-default receipts directory (spec story 78), so repeated mock runs never
        # accumulate synthetic receipts outside the requested --out tree.
        writer = ReceiptWriter(directory=out_dir / "receipts")
        report, transport = run_smoke(max_calls=max_calls, mode=mode, writer=writer)
        calls_used = transport.sent

    path = _write_report(
        out_dir,
        live=live,
        max_calls=max_calls,
        calls_used=calls_used,
        attestation_path=attestation_path,
        report=report,
    )
    print(f"wrote smoke report to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
