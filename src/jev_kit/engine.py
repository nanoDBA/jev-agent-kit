"""The decide core and its JSON-facing entry point.

Wires the pieces together for one request: parse, load the question set and registry, resolve
keys, transform and scan state (egress), reserve the rate budget, call the transport within the
deadline (with bounded retries), validate the response, route each question through the matrix,
and write receipts before returning any accept. It never raises for runtime conditions; a
request whose questions cannot be identified returns the error envelope.

JSON request and response contract: see below.

JSON request (schema_version 1):
    {"schema_version": 1, "question_set_path" | "question_set", "state": {...},
     "mode": "shadow" | "enforce", "action_id": "..."}

JSON response (schema_version 1):
    {"schema_version": 1, "status": "ok", "records": [record, ...]}
  or the error envelope:
    {"schema_version": 1, "status": "error", "reason": "<fail reason>", "records": []}
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from jev_kit import registry as registry_mod
from jev_kit import secrets
from jev_kit.attestation import check_attestation, load_attestation
from jev_kit.deadline import Deadline
from jev_kit.egress import (
    EgressContext,
    personal_kinds_present,
    scan_request,
    scan_text,
    transform_state,
)
from jev_kit.errors import FailReason, ValidationError, fail_reason_for_status
from jev_kit.fingerprint import (
    canonical_bytes,
    parse_canonical,
    question_fingerprint,
    question_set_digest,
)
from jev_kit.questionset import (
    QuestionSet,
    egress_contract,
    load_question_set,
    load_question_set_file,
)
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import (
    ReceiptWriter,
    get_writer,
    new_action_id,
    validate_action_id,
    validate_metadata_id,
)
from jev_kit.routing import (
    Candidate,
    ChoiceThreshold,
    NoulThreshold,
    RegistryEntry,
    ScoreThreshold,
    ThresholdStatus,
    evaluate_candidate,
    resolve_route,
)
from jev_kit.transport import LiveTransport, Transport, TransportFailure, TransportResponse
from jev_kit.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    ConsequenceClass,
    Mode,
    NoulAnswer,
    Question,
    Route,
    ScoreAnswer,
    ScoreQuestion,
)
from jev_kit.validation import validate_answer_set

SCHEMA_VERSION = 1
_RETRYABLE = {FailReason.RATE_LIMIT, FailReason.OVERLOADED, FailReason.SERVER}


@dataclass
class EngineConfig:
    registry_path: str | None = None
    hmac_key: bytes | None = None
    api_key: str | None = None
    deadline_seconds: float = 10.0
    receipt_reserve: float = 0.5
    max_retries: int = 2
    source_allowlist: frozenset[str] = frozenset()
    public_names: frozenset[str] = frozenset()
    language_profiles: Mapping[str, Callable[[str], str]] = field(default_factory=dict)
    rate_budget: RateBudget | None = None
    writer: ReceiptWriter | None = None
    attestation_path: str | None = None
    attestation: dict[str, Any] | None = None  # injected for tests; else loaded from path/env
    max_request_bytes: int = 49_152  # 48 KiB serialized ceiling (finding C13)
    max_request_tokens: int = 16_000  # whole-request token budget (finding C13)
    max_state_question_tokens: int = 12_000  # state plus the longest question (finding C13)


def effective_contract(qset: QuestionSet, config: EngineConfig) -> dict[str, Any]:
    """The full egress contract hashed into question fingerprints: the question set's declared
    schema plus the local effective allowlists and profile identities (finding C07)."""
    contract = egress_contract(qset)
    contract["effective"] = {
        "public_names": sorted(config.public_names),
        "source_allowlist": sorted(config.source_allowlist),
        "language_profiles": sorted(config.language_profiles),
    }
    return contract


def _error_envelope(reason: FailReason) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "reason": reason.value,
        "records": [],
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _wire_question(question: Question) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "type": question.question_type.value,
        "instructions": question.instructions,
    }
    if isinstance(question, ChoiceQuestion):
        wire["criteria"] = {opt: None for opt in question.options}
    elif isinstance(question, ScoreQuestion):
        wire["criteria"] = {str(i): level for i, level in enumerate(question.levels)}
    return wire


def _option_or_level_set(question: Question) -> list[str]:
    if isinstance(question, ChoiceQuestion):
        return list(question.options)
    if isinstance(question, ScoreQuestion):
        return list(question.levels)
    return []


def _send_with_retries(
    transport: Transport,
    body: bytes,
    deadline: Deadline,
    budget: RateBudget,
    est_tokens: int,
    max_retries: int,
) -> TransportResponse | TransportFailure:
    attempt = 0
    last: TransportFailure = TransportFailure(FailReason.TRANSPORT, "no_attempt")
    while attempt <= max_retries:
        if deadline.remaining_for_work() <= 0:
            return TransportFailure(FailReason.TIMEOUT, "deadline")
        if not budget.reserve(est_tokens):
            return TransportFailure(FailReason.RATE_BUDGET, "budget")
        result = transport.send(body, deadline)
        if isinstance(result, TransportResponse):
            return result
        last = result
        if result.reason not in _RETRYABLE or attempt == max_retries:
            return result  # do not sleep after the last permitted attempt (finding C15)
        attempt += 1
        # Honor the server's requested delay when present; otherwise exponential backoff with
        # jitter. Never wait past the remaining budget (finding C15).
        server_delay = result.retry_after
        if server_delay is not None:
            wait = server_delay
        else:
            wait = 0.5 * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
        remaining = deadline.remaining_for_work()
        if wait > remaining:
            return TransportFailure(FailReason.TIMEOUT, "retry_after_exceeds_deadline")
        time.sleep(wait)
    return last


def _candidate_for(
    answer: NoulAnswer | ChoiceAnswer | ScoreAnswer, entry: RegistryEntry
) -> tuple[Candidate | None, FailReason | None]:
    if entry.threshold is None:
        return None, None
    try:
        return evaluate_candidate(answer, entry.threshold), None
    except ValidationError as exc:
        return None, exc.reason


def _record(
    *,
    decision_id: str,
    question: Question,
    answer: Any,
    entry: RegistryEntry | None,
    route: Route,
    would: Route | None,
    reason: FailReason | None,
    mode: Mode,
    is_mock: bool,
    candidate: Candidate | None,
    fingerprint: str,
    served_model: str | None,
    requested_model: str,
) -> dict[str, Any]:
    status = entry.status.value if entry is not None else None
    label: str | None = None
    value: float | None = None
    distribution: dict[str, float] | None = None
    confidence: float | None = None
    noul: float | None = None
    margin: float | None = None
    if isinstance(answer, ChoiceAnswer):
        label = answer.choice
        distribution = answer.probabilities
        confidence = answer.confidence
    elif isinstance(answer, ScoreAnswer):
        value = answer.score
        distribution = answer.probabilities
        confidence = answer.confidence
    elif isinstance(answer, NoulAnswer):
        noul = answer.noul
    if candidate is not None:
        label = candidate.label if candidate.label is not None else label
        margin = candidate.margin
    threshold = _threshold_repr(entry) if entry is not None else None
    # Mock provenance is carried explicitly: model is "mock" and the answering model the mock
    # simulated is recorded separately, never conflated with a real served model (finding C16).
    model = "mock" if is_mock else served_model
    simulated_model = served_model if is_mock else None
    return {
        "decision_id": decision_id,
        "question_id": question.question_id,
        "consequence": question.consequence.value,
        "fingerprint": fingerprint,
        "route": route.value,
        "would_route": would.value if would is not None else None,
        "label": label,
        "value": value,
        "distribution": distribution,
        "confidence": confidence,
        "noul": noul,
        "margin": margin,
        "threshold": threshold,
        "threshold_status": status,
        "requested_model": requested_model,
        "model": model,
        "simulated_model": simulated_model,
        "mode": mode.value,
        "fail_reason": reason.value if reason is not None else None,
        "is_mock": is_mock,
        "receipt_written": False,  # set True after the receipt commits
    }


def _threshold_repr(entry: RegistryEntry) -> dict[str, Any] | None:
    """A JSON-safe view of an entry's threshold values, for records and receipts (C16)."""
    t = entry.threshold
    if isinstance(t, NoulThreshold):
        return {"kind": "noul", "yes_bound": t.yes_bound, "no_bound": t.no_bound}
    if isinstance(t, ChoiceThreshold):
        return {"kind": "choice", "min_confidence": t.min_confidence, "min_margin": t.min_margin}
    if isinstance(t, ScoreThreshold):
        return {
            "kind": "score",
            "min_confidence": t.min_confidence,
            "intervals": [[i.lower, i.upper, i.label] for i in t.intervals],
        }
    return None


def _decide(
    request: Mapping[str, Any], transport: Transport, config: EngineConfig
) -> dict[str, Any]:
    if not isinstance(request, Mapping) or request.get("schema_version") != 1:
        return _error_envelope(FailReason.CONFIG)
    mode_raw = request.get("mode", Mode.SHADOW.value)
    if mode_raw not in (Mode.SHADOW.value, Mode.ENFORCE.value):
        return _error_envelope(FailReason.CONFIG)
    mode = Mode(mode_raw)
    state = request.get("state", {})
    if not isinstance(state, Mapping):
        return _error_envelope(FailReason.CONFIG)

    # Identify the questions first; if we cannot, return the safe envelope.
    try:
        if "question_set" in request:
            qset = load_question_set(request["question_set"])
        elif "question_set_path" in request:
            qset = load_question_set_file(request["question_set_path"])
        else:
            return _error_envelope(FailReason.CONFIG)
    except ValidationError:
        return _error_envelope(FailReason.CONFIG)

    action_id = request.get("action_id")
    if action_id is not None:
        try:
            validate_action_id(action_id)
        except ValidationError:
            return _error_envelope(FailReason.CONFIG)

    deadline = Deadline(config.deadline_seconds, reserve_seconds=config.receipt_reserve)
    budget = config.rate_budget or RateBudget()
    writer = config.writer or get_writer()
    call_id = new_action_id()
    set_digest = question_set_digest(qset.raw)
    # The fingerprint contract includes the local effective egress configuration and profile
    # identities, so changing an allowlist or profile invalidates a prior calibration (C07).
    contract = effective_contract(qset, config)

    # A whole-request failure (alias, registry, egress, transport, validation) sets these.
    whole_fail: FailReason | None = None
    answers: dict[str, Any] = {}
    served_model: str | None = None
    sent_digest: str | None = None
    est_tokens: int | None = None
    reported_tokens: int | None = None

    if qset.model in ("jev-latest", "jev-preview"):
        whole_fail = FailReason.CONFIG  # aliases are refused (spec story 28)

    registry: dict[str, RegistryEntry] = {}
    if whole_fail is None:
        try:
            registry_path = registry_mod.resolve_registry_path(config.registry_path)
            registry = registry_mod.load_registry(registry_path) if registry_path else {}
        except ValidationError:
            whole_fail = FailReason.CONFIG

    if whole_fail is None:
        try:
            hmac_key = config.hmac_key
            if hmac_key is None and personal_kinds_present(qset.schema):
                hmac_key = secrets.resolve_hmac_key(timeout=deadline.remaining_for_work())
            ctx = EgressContext(
                hmac_key=hmac_key,
                public_names=config.public_names,
                source_allowlist=config.source_allowlist,
                language_profiles=config.language_profiles,
                transcript_enabled=qset.transcripts_enabled,
                transcript_cap=qset.transcripts_cap,
            )
            outgoing_state = transform_state(state, qset.schema, ctx)
            wire_questions: dict[str, Any] = {
                qid: _wire_question(q) for qid, q in qset.questions.items()
            }
            wire: dict[str, Any] = {
                "model": qset.model,
                "state": outgoing_state,
                "questions": wire_questions,
            }
            serialized = canonical_bytes(wire)
            # Size and token budgets before scan or send (finding C13). ceil(bytes/3).
            est_tokens = -(-len(serialized) // 3)
            state_bytes = len(canonical_bytes(outgoing_state))
            longest_q = max(
                (len(canonical_bytes(v)) for v in wire_questions.values()), default=0
            )
            state_plus_longest = -(-(state_bytes + longest_q) // 3)
            over_budget = (
                len(serialized) > config.max_request_bytes
                or est_tokens > config.max_request_tokens
                or state_plus_longest > config.max_state_question_tokens
            )
            if over_budget:
                whole_fail = FailReason.BUDGET_EXCEEDED
            else:
                hit = scan_request(serialized, wire)
                if hit is not None:
                    whole_fail = FailReason.EGRESS_BLOCKED
                elif not transport.is_mock:
                    # Live send: enforce the inventory/DPA attestation (finding C03).
                    personal = personal_kinds_present(qset.schema)
                    attest = config.attestation or load_attestation(config.attestation_path)
                    check_attestation(attest, personal_present=personal)
        except ValidationError as exc:
            whole_fail = exc.reason
        except Exception:
            # An unexpected failure after questions are identified still produces per-question
            # failure records, never an empty envelope (finding C17).
            whole_fail = FailReason.INTERNAL

    if whole_fail is None:
        result = _send_with_retries(
            transport, serialized, deadline, budget, est_tokens or 1, config.max_retries
        )
        sent_digest = hashlib.sha256(serialized).hexdigest()  # something was sent this attempt
        if isinstance(result, TransportFailure):
            whole_fail = result.reason
        elif not 200 <= result.status < 300:
            # A non-2xx raw response is a failure, never a body to validate (finding C09).
            whole_fail = fail_reason_for_status(result.status)
        elif deadline.expired():
            # Evidence that arrived after the deadline is discarded (finding C02).
            whole_fail = FailReason.TIMEOUT
        else:
            try:
                body = _parse_response_body(result.body)
                served_model = body.get("model") if isinstance(body, dict) else None
                usage = body.get("usage") if isinstance(body, dict) else None
                if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                    reported_tokens = usage["input_tokens"]
                if served_model != qset.model:
                    whole_fail = FailReason.MODEL_MISMATCH
                else:
                    answers = validate_answer_set(qset.questions, body.get("answers"))
            except ValidationError as exc:
                whole_fail = exc.reason

    # Route every question.
    records: list[dict[str, Any]] = []
    for qid, question in qset.questions.items():
        fp = question_fingerprint(
            instructions=question.instructions,
            criteria=_wire_question(question).get("criteria"),
            question_type=question.question_type.value,
            option_or_level_set=_option_or_level_set(question),
            model=qset.model,
            egress_contract=contract,
        )
        entry = registry.get(fp)
        answer = answers.get(qid)
        failed = whole_fail is not None
        reason = whole_fail
        candidate: Candidate | None = None
        if not failed and entry is not None and entry.status is ThresholdStatus.CALIBRATED:
            if entry.escalation_target != qset.escalation_target:
                failed, reason = True, FailReason.CONFIG
            elif answer is not None:
                candidate, cand_reason = _candidate_for(answer, entry)
                if cand_reason is not None:
                    failed, reason = True, cand_reason
        resolution = resolve_route(
            consequence=question.consequence,
            mode=mode,
            entry=entry,
            candidate=candidate,
            failed=failed,
            fail_reason=reason,
            is_mock=transport.is_mock,
        )
        records.append(
            _record(
                decision_id=new_action_id(),
                question=question,
                answer=answer,
                entry=entry,
                route=resolution.route,
                would=resolution.would_route,
                reason=resolution.reason,
                mode=mode,
                is_mock=transport.is_mock,
                candidate=candidate,
                fingerprint=fp,
                served_model=served_model,
                requested_model=qset.model,
            )
        )

    _write_receipts(
        writer, records, call_id, set_digest, qset, served_model, sent_digest, action_id,
        est_tokens, reported_tokens,
    )
    return {"schema_version": SCHEMA_VERSION, "status": "ok", "records": records}


def _parse_response_body(body: bytes) -> Any:
    # Strict parse: duplicate keys and non-finite numbers are rejected (finding C10).
    try:
        return parse_canonical(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(FailReason.RESPONSE_MALFORMED, "body_not_json") from exc


def _safe_metadata(value: str | None) -> str | None:
    """Omit a metadata string that is unsafe or carries a secret pattern (finding C08).

    Metadata fields are a separate egress channel from the request body, so they are scanned
    and pattern-validated independently before being persisted or returned.
    """
    if value is None:
        return None
    if validate_metadata_id(value, "metadata") is None:
        return None
    if scan_text(value) is not None:
        return None
    return value


RECEIPT_SCHEMA_VERSION = 1
_committed_decisions: set[str] = set()  # decision ids with a committed receipt this process


def _write_receipts(
    writer: ReceiptWriter,
    records: list[dict[str, Any]],
    call_id: str,
    set_digest: str,
    qset: QuestionSet,
    served_model: str | None,
    sent_digest: str | None,
    action_id: str | None,
    est_tokens: int | None,
    reported_tokens: int | None,
) -> None:
    lines = [
        {
            "kind": "decision",
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "timestamp": _now_iso(),
            "call_id": call_id,
            "decision_id": rec["decision_id"],
            "question_id": rec["question_id"],
            "fingerprint": rec["fingerprint"],
            "question_set": {"id": qset.set_id, "version": qset.version, "digest": set_digest},
            "requested_model": qset.model,
            "served_model": served_model,
            "model": rec["model"],
            "simulated_model": rec["simulated_model"],
            "sent_digest": sent_digest,
            "estimated_tokens": est_tokens,
            "reported_tokens": reported_tokens,
            "distribution": rec["distribution"],
            "label": rec["label"],
            "value": rec["value"],
            "noul": rec["noul"],
            "threshold": rec["threshold"],
            "threshold_status": rec["threshold_status"],
            "would_route": rec["would_route"],
            "route": rec["route"],
            "mode": rec["mode"],
            "fail_reason": rec["fail_reason"],
            "is_mock": rec["is_mock"],
            "action_id": _safe_metadata(action_id),
        }
        for rec in records
    ]
    committed = writer.write_call(lines)
    for rec in records:
        if committed:
            rec["receipt_written"] = True
            _committed_decisions.add(rec["decision_id"])
            continue
        # A receipt failure is itself a failure: route every record failure-first, in every
        # mode (gate -> ask, advisory -> no advice), not only downgrade an existing accept
        # (finding C17). No decision is acted on without a durable record.
        is_gate = rec["consequence"] == ConsequenceClass.GATE.value
        rec["route"] = Route.ASK.value if is_gate else Route.NO_ADVICE.value
        rec["fail_reason"] = FailReason.RECEIPT.value
        rec["receipt_written"] = False


def run_json(request: dict[str, Any], *, transport: Transport | None = None) -> dict[str, Any]:
    """Run one decision request expressed as JSON and return the JSON response.

    ``transport`` None builds the live transport from the resolved API key. This never raises
    for runtime conditions; a caught exception becomes an internal error envelope.
    """
    try:
        config = EngineConfig()
        if transport is None:
            api_key = config.api_key or secrets.resolve_api_key(timeout=config.deadline_seconds)
            if api_key is None:
                return _error_envelope(FailReason.CONFIG)
            transport = LiveTransport(api_key)
        return _decide(request, transport, config)
    except ValidationError as exc:
        return _error_envelope(exc.reason)
    except Exception:
        return _error_envelope(FailReason.INTERNAL)


def decide(
    request: dict[str, Any], *, transport: Transport, config: EngineConfig | None = None
) -> dict[str, Any]:
    """Programmatic entry point with an injected transport and optional config."""
    try:
        return _decide(request, transport, config or EngineConfig())
    except ValidationError as exc:
        return _error_envelope(exc.reason)
    except Exception:
        return _error_envelope(FailReason.INTERNAL)


def decide_batch(
    requests: list[dict[str, Any]],
    *,
    transport: Transport,
    config: EngineConfig | None = None,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """Run several requests concurrently through a bounded pool, results in input order.

    All requests share one rate budget so the per-process cap and rate limits apply across the
    whole batch (spec stories 8, 65, 67).
    """
    import concurrent.futures

    shared = config or EngineConfig()
    if shared.rate_budget is None:
        shared = replace(shared, rate_budget=RateBudget())
    results: list[dict[str, Any]] = [{} for _ in requests]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(decide, req, transport=transport, config=shared): i
            for i, req in enumerate(requests)
        }
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results


def record_outcome(decision_id: str, outcome_code: str, action_id: str | None = None) -> bool:
    """Append what the caller did with a decision (spec stories 55, 80).

    outcome_code is drawn from a closed vocabulary; an unknown code or id returns False rather
    than raising. Returns whether the outcome line was durably written.
    """
    if outcome_code not in _OUTCOME_CODES:
        return False
    try:
        validate_action_id(decision_id)
    except ValidationError:
        return False
    if decision_id not in _committed_decisions:
        # An outcome must reference a decision committed in this process (finding C16).
        return False
    line = {
        "kind": "outcome",
        "timestamp": _now_iso(),
        "decision_id": decision_id,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "outcome": outcome_code,
        "action_id": _safe_metadata(action_id),
    }
    return get_writer().append_outcome(line)


_OUTCOME_CODES = frozenset({"applied", "not_applied", "overridden_by_host", "overridden_by_human"})
