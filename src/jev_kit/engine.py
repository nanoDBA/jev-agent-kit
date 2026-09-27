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
import json
import random
import re
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
from jev_kit.transport import (
    LiveTransport,
    Transport,
    TransportFailure,
    TransportResponse,
    parse_retry_after,
)
from jev_kit.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    ConsequenceClass,
    Mode,
    NoulAnswer,
    NoulQuestion,
    Question,
    Route,
    ScoreAnswer,
    ScoreQuestion,
)
from jev_kit.validation import validate_answer_set

SCHEMA_VERSION = 1
_RETRYABLE = {FailReason.RATE_LIMIT, FailReason.OVERLOADED, FailReason.SERVER}
_DEFAULT_BUDGET: RateBudget | None = None


def _default_budget() -> RateBudget:
    global _DEFAULT_BUDGET
    if _DEFAULT_BUDGET is None:
        _DEFAULT_BUDGET = RateBudget()
    return _DEFAULT_BUDGET


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
    # Reserved. Any non-empty language_profiles is rejected in phase 0 (see effective_contract,
    # finding H3); a declarative, data-only profile format is tracked as jak-t4l.
    language_profiles: Mapping[str, Callable[[str], str]] = field(default_factory=dict)
    language_profile_versions: Mapping[str, str] = field(default_factory=dict)
    rate_budget: RateBudget | None = None
    writer: ReceiptWriter | None = None
    attestation_path: str | None = None
    attestation: dict[str, Any] | None = None  # injected for tests; else loaded from path/env
    max_request_bytes: int = 49_152  # 48 KiB serialized ceiling (finding C13)
    max_request_tokens: int = 16_000  # whole-request token budget (finding C13)
    max_state_question_tokens: int = 12_000  # state plus the longest question (finding C13)


def _config_from_env() -> EngineConfig:
    """Build the engine config for the CLI/hook path from the owner's environment (finding H12).

    A shipped hook needs a way to declare its source allowlist and public names without editing
    code; without this, run_json always used empty allowlists and any free-text field was
    egress-blocked, so a hook produced no evidence. Comma-separated lists; blanks are ignored.
    """
    import os

    def _set(name: str) -> frozenset[str]:
        raw = os.environ.get(name, "")
        return frozenset(item.strip() for item in raw.split(",") if item.strip())

    return EngineConfig(
        source_allowlist=_set("JEV_KIT_SOURCE_ALLOWLIST"),
        public_names=_set("JEV_KIT_PUBLIC_NAMES"),
    )


def effective_contract(qset: QuestionSet, config: EngineConfig) -> dict[str, Any]:
    """The full egress contract hashed into question fingerprints: the question set's declared
    schema plus the local effective allowlists and profile identities (finding C07)."""
    contract = egress_contract(qset)
    # Language profiles (injected Python callables that normalize CODE fields) are not
    # supported in phase 0. A Python callable cannot be bound to a fingerprint that captures its
    # behavior: module attributes, substituted builtins, captured subclasses and hash-seeded
    # iteration all change output without changing any inspectable identity, so a calibrated
    # threshold could be silently reused after the behavior changed (finding H3). Any configured
    # profile therefore fails closed. CODE fields stay blocked without a profile. A declarative,
    # data-only profile format that can be bound by value is tracked as jak-t4l.
    if config.language_profiles:
        raise ValidationError(FailReason.CONFIG, "language_profiles_unsupported")
    contract["effective"] = {
        "public_names": sorted(config.public_names),
        "source_allowlist": sorted(config.source_allowlist),
        "language_profiles": {},
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
        # Choice criteria is the option map (option key -> description or null).
        wire["criteria"] = dict(question.criteria) if question.criteria else {
            opt: None for opt in question.options
        }
    elif isinstance(question, ScoreQuestion):
        # Score criteria is the ordered array of level descriptions (finding C12).
        wire["criteria"] = list(question.levels)
    elif isinstance(question, NoulQuestion) and question.criteria is not None:
        wire["criteria"] = dict(question.criteria)
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
) -> tuple[TransportResponse | TransportFailure, int]:
    """Return the transport result and the number of actual send attempts made, so the caller
    can tell a genuine send from a request that never left (findings GATE-06)."""
    attempt = 0
    sends = 0
    last: TransportFailure = TransportFailure(FailReason.TRANSPORT, "no_attempt")
    while attempt <= max_retries:
        if deadline.remaining_for_work() <= 0:
            return TransportFailure(FailReason.TIMEOUT, "deadline"), sends
        if not budget.reserve(est_tokens):
            return TransportFailure(FailReason.RATE_BUDGET, "budget"), sends
        sends += 1
        result = transport.send(body, deadline)
        if isinstance(result, TransportResponse) and not 200 <= result.status < 300:
            # Normalize a raw non-2xx response into the typed failure path before the dispatch
            # decision, so a raw TransportResponse(429/529/5xx) retries on the same terms as an
            # equivalent typed rate-limit/overloaded/server failure (finding H19).
            result = TransportFailure(
                fail_reason_for_status(result.status),
                f"http_{result.status}",
                retry_after=parse_retry_after(result.headers),
            )
        if isinstance(result, TransportResponse):
            return result, sends
        last = result
        if result.reason not in _RETRYABLE or attempt == max_retries:
            return result, sends  # do not sleep after the last permitted attempt (finding C15)
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
            return TransportFailure(FailReason.TIMEOUT, "retry_after_exceeds_deadline"), sends
        time.sleep(wait)
    return last, sends


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
        "allow_labels": sorted(question.gate_allow_labels) if question.gate_allow_labels else None,
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
    # Provenance is a property of the transport TYPE; an instance attribute cannot override it
    # to claim non-mock and reach accept (finding GATE-01).
    is_mock = type(transport).is_mock
    if not isinstance(request, Mapping):
        return _error_envelope(FailReason.CONFIG)
    # schema_version must be exactly the integer 1. A bare `!= 1` would accept True and 1.0,
    # because True == 1 and 1.0 == 1 in Python (finding H18); require the int type exactly.
    if not _is_schema_version_one(request.get("schema_version")):
        return _error_envelope(FailReason.CONFIG)
    # Reject any field the request contract does not define, so a typo or an injected field
    # cannot ride along unnoticed into a call that then accepts (finding H18).
    if not set(request.keys()) <= _DECIDE_ALLOWED_FIELDS:
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
    # A process-wide default budget so the call cap and rate limits hold across sequential
    # calls, not only within one batch (finding GATE-05).
    budget = config.rate_budget or _default_budget()
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
    server_request_id: str | None = None
    est_tokens: int | None = None
    reported_tokens: int | None = None
    transforms = {name: spec.kind.value for name, spec in qset.schema.items()}

    if not _is_pinned_model(qset.model):
        # A pinned, immutable, fully versioned model id is required. This refuses the moving
        # aliases jev-latest/jev-preview AND a bare or partially versioned id like "jev" or
        # "jev-1" that could resolve to different models over time (finding H22, spec story 28).
        whole_fail = FailReason.CONFIG

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
                elif not is_mock:
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
        try:
            result, sends = _send_with_retries(
                transport, serialized, deadline, budget, est_tokens or 1, config.max_retries
            )
            if sends > 0:
                # Only record a sent digest when a send actually left (finding GATE-06).
                sent_digest = hashlib.sha256(serialized).hexdigest()
            if isinstance(result, TransportFailure):
                whole_fail = result.reason
            elif not 200 <= result.status < 300:
                # A non-2xx raw response is a failure, never a body to validate (finding C09).
                whole_fail = fail_reason_for_status(result.status)
            elif deadline.expired():
                # Evidence that arrived after the deadline is discarded (finding C02).
                whole_fail = FailReason.TIMEOUT
            else:
                # Remote metadata is a separate egress channel: scan it for secret shapes and
                # drop anything unsafe before it can reach a record or receipt (finding H6). A
                # charset-valid id (AKIA..., a JWT) would otherwise persist verbatim.
                server_request_id = _safe_metadata(
                    result.headers.get("x-typesafe-request-id", "")
                )
                body = _parse_response_body(result.body)
                raw_served_model = body.get("model") if isinstance(body, dict) else None
                usage = body.get("usage") if isinstance(body, dict) else None
                tok = usage.get("input_tokens") if isinstance(usage, dict) else None
                if isinstance(tok, int) and not isinstance(tok, bool):
                    reported_tokens = tok
                # The pin check compares the raw served model; a mismatch (including a
                # secret-shaped value that can never equal the pinned id) fails the call. Either
                # way the value stored/returned is the scanned, safe form, never the raw one
                # (finding H6: a rejected served model must not appear in records).
                served_model = _safe_metadata(
                    raw_served_model if isinstance(raw_served_model, str) else None
                )
                if raw_served_model != qset.model:
                    whole_fail = FailReason.MODEL_MISMATCH
                else:
                    answers = validate_answer_set(qset.questions, body.get("answers"))
        except ValidationError as exc:
            whole_fail = exc.reason
        except Exception:
            # A transport (or any) exception still yields per-question records (finding GATE-08).
            whole_fail = FailReason.INTERNAL

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
            is_mock=is_mock,
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
                is_mock=is_mock,
                candidate=candidate,
                fingerprint=fp,
                served_model=served_model,
                requested_model=qset.model,
            )
        )

    # A decision that only became an accept after the deadline passed (for example because the
    # response or receipt work overran) must not be returned as accept (finding H8/C02). Recheck
    # expiry before receipts, so the downgraded route is what is both recorded and returned.
    if deadline.expired():
        for rec in records:
            if rec["route"] == Route.ACCEPT.value:
                is_gate = rec["consequence"] == ConsequenceClass.GATE.value
                rec["route"] = Route.ASK.value if is_gate else Route.NO_ADVICE.value
                rec["fail_reason"] = FailReason.TIMEOUT.value

    _write_receipts(
        writer, records, call_id, set_digest, qset, served_model, sent_digest, action_id,
        est_tokens, reported_tokens, server_request_id, transforms,
    )

    # Re-check expiry AFTER the receipt write: the write itself can overrun the total deadline
    # (a slow writer), and an accept that only completed past the deadline must not be returned
    # (finding H8/C02). Downgrade any surviving accept and record a durable correction so the
    # receipt's final state for that decision matches the returned route.
    if deadline.expired():
        for rec in records:
            if rec["route"] == Route.ACCEPT.value:
                is_gate = rec["consequence"] == ConsequenceClass.GATE.value
                rec["route"] = Route.ASK.value if is_gate else Route.NO_ADVICE.value
                rec["fail_reason"] = FailReason.TIMEOUT.value
                corrected = writer.append_correction(
                    {
                        "kind": "route_correction",
                        "schema_version": RECEIPT_SCHEMA_VERSION,
                        "timestamp": _now_iso(),
                        "call_id": call_id,
                        "decision_id": rec["decision_id"],
                        "route": rec["route"],
                        "fail_reason": FailReason.TIMEOUT.value,
                        "reason_detail": "deadline_expired_during_receipt_write",
                    }
                )
                if not corrected:
                    # The correction could not be persisted, so the durable record still shows
                    # the original accept while we return a downgraded route. Report the receipt
                    # as not written rather than claim a truthful record exists (finding H8).
                    rec["receipt_written"] = False
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
    if not value:  # absent (None) or an empty header is absent, not unsafe
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
    server_request_id: str | None,
    transforms: dict[str, str],
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
            "server_request_id": server_request_id,
            "confidence": rec["confidence"],
            "margin": rec["margin"],
            "egress_transforms": transforms,
            "egress_transform_count": len(transforms),
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
        config = _config_from_env()
        if not isinstance(request, dict):
            return _error_envelope(FailReason.CONFIG)
        op = request.get("op", "decide")
        if op == "record_outcome":
            # An outcome operation needs no transport (finding C21). Its envelope is validated
            # on the same strict terms as a decide request (finding H18): exact integer version,
            # allowed fields only, before anything is recorded.
            if not _is_schema_version_one(request.get("schema_version")):
                return _error_envelope(FailReason.CONFIG)
            if not set(request.keys()) <= _OUTCOME_ALLOWED_FIELDS:
                return _error_envelope(FailReason.CONFIG)
            ok = record_outcome(
                str(request.get("decision_id", "")),
                str(request.get("outcome", "")),
                request.get("action_id"),
            )
            return {"schema_version": SCHEMA_VERSION, "status": "ok" if ok else "error",
                    "recorded": ok, "records": []}
        if op == "decide_batch":
            # Validate the batch envelope before dispatch, so a bad version or an unknown field
            # cannot ride along and still send (finding H18). Each inner request is then
            # validated by _decide on its own terms.
            if not _is_schema_version_one(request.get("schema_version")):
                return _error_envelope(FailReason.CONFIG)
            if not set(request.keys()) <= _BATCH_ALLOWED_FIELDS:
                return _error_envelope(FailReason.CONFIG)
            reqs = request.get("requests")
            if not isinstance(reqs, list):
                return _error_envelope(FailReason.CONFIG)
            if transport is None:
                api_key = config.api_key or secrets.resolve_api_key(
                    timeout=config.deadline_seconds
                )
                if api_key is None:
                    return _error_envelope(FailReason.CONFIG)
                transport = LiveTransport(api_key)
            results = decide_batch(reqs, transport=transport, config=config)
            return {"schema_version": SCHEMA_VERSION, "status": "ok", "results": results}
        if op != "decide":
            return _error_envelope(FailReason.CONFIG)
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
        # Share the process-wide budget so the cap spans batches and batch-vs-single calls
        # (finding MAJOR-1; spec story 67), not a fresh per-batch budget.
        shared = replace(shared, rate_budget=_default_budget())
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
    if decision_id not in _committed_decisions and not _decision_committed_on_disk(decision_id):
        # An outcome must reference a decision with a committed receipt, in this process or a
        # prior one (finding H11): the in-memory set is a fast path, disk is the durable check.
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

# The fields a decide request may carry; anything else is rejected before egress (finding H18).
_DECIDE_ALLOWED_FIELDS = frozenset(
    {"schema_version", "op", "question_set", "question_set_path", "state", "mode", "action_id"}
)
_BATCH_ALLOWED_FIELDS = frozenset({"schema_version", "op", "requests"})
_OUTCOME_ALLOWED_FIELDS = frozenset(
    {"schema_version", "op", "decision_id", "outcome", "action_id"}
)


def _is_schema_version_one(value: Any) -> bool:
    """True only for the exact integer 1. bool and float 1.0 are rejected (finding H18)."""
    return type(value) is int and value == 1


# A pinned, immutable, fully versioned Jev model id, e.g. "jev-1.13.0". Matched with fullmatch
# over an ASCII-only grammar: `$` would accept a trailing newline and `\d` would admit non-ASCII
# digits, either of which could smuggle a distinct id past the pin (finding H22, spec story 28).
# A moving alias (jev-latest/jev-preview) or a partial id (jev, jev-1) is refused too.
_PINNED_MODEL_RE = re.compile(r"jev-[0-9]+\.[0-9]+\.[0-9]+")


def _is_pinned_model(model: str) -> bool:
    return _PINNED_MODEL_RE.fullmatch(model) is not None


def _decision_committed_on_disk(decision_id: str) -> bool:
    """True if a committed decision receipt with this id exists in the receipts directory.

    Scans this process's and prior processes' receipt files, honoring the per-call commit
    marker so a decision in a truncated (uncommitted) batch does not count (finding H11).
    """
    from jev_kit.receipts import receipts_dir

    try:
        files = sorted(receipts_dir().glob("*.jsonl"))
    except OSError:
        return False
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        target_call_ids: set[str] = set()
        per_call_lines: dict[str, int] = {}
        for raw in lines:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("kind")
            call_id = rec.get("call_id")
            if kind == "decision" and isinstance(call_id, str):
                per_call_lines[call_id] = per_call_lines.get(call_id, 0) + 1
                if rec.get("decision_id") == decision_id:
                    target_call_ids.add(call_id)
            elif kind == "commit" and call_id in target_call_ids:
                # The decision's own call must be committed AND the marker's line count must
                # match the decision lines actually seen for that call; a malformed or wrong
                # count is a truncated/corrupt batch and does not count (finding H11 MAJOR-2).
                lines_field = rec.get("lines")
                if (
                    isinstance(lines_field, int)
                    and not isinstance(lines_field, bool)
                    and lines_field == per_call_lines.get(call_id, 0)
                ):
                    return True
    return False
