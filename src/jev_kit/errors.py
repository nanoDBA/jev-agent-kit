"""Typed errors and the closed fail-reason vocabulary.

Fail reasons are a closed vocabulary so receipts stay queryable (spec stories 54, 81).
Error objects never carry raw server bodies or matched content; the raw body is held
privately by the transport layer and kept out of string forms (spec stories 63, 79a).
"""

from __future__ import annotations

from enum import StrEnum


class FailReason(StrEnum):
    """Every way a decision can fail to reach a real, calibrated answer.

    Closed vocabulary. Add a member here rather than inventing a string elsewhere.
    """

    # Transport and HTTP (spec story 62)
    AUTH = "auth"  # 401, 403
    VALIDATION_REQUEST = "validation_request"  # 422, 400: the API rejected our request
    RATE_LIMIT = "rate_limit"  # 429
    OVERLOADED = "overloaded"  # 529
    NOT_FOUND = "not_found"  # 404
    SERVER = "server"  # 5xx
    TIMEOUT = "timeout"  # 408 or local deadline
    REDIRECT_REFUSED = "redirect_refused"  # any 3xx (spec story 57)
    TRANSPORT = "transport"  # connection reset, TLS, DNS, unclassified

    # Response shape (spec stories 33 to 37)
    RESPONSE_MALFORMED = "response_malformed"  # not JSON, missing answers, wrong shape
    ANSWER_INVALID = "answer_invalid"  # a per-question validation check failed
    ANSWER_SET_MISMATCH = "answer_set_mismatch"  # answer ids do not equal question ids
    MODEL_MISMATCH = "model_mismatch"  # served model is not the pinned model

    # Local guardrails (spec stories 20, 22, 28, 48 to 52, 55, 67)
    CONFIG = "config"  # question set, registry, mode or key configuration is unusable
    UNCALIBRATED = "uncalibrated"  # no calibrated threshold for this question
    EGRESS_BLOCKED = "egress_blocked"  # a Tier 1 detector or transform blocked the request
    BUDGET_EXCEEDED = "budget_exceeded"  # token or size budget exceeded before egress
    RATE_BUDGET = "rate_budget"  # local call cap or rate budget exhausted
    ATTESTATION = "attestation"  # missing or insufficient live-use attestation
    RECEIPT = "receipt"  # a receipt could not be committed
    INTERNAL = "internal"  # an unexpected exception, caught and made safe


# HTTP status to fail reason, for statuses we classify explicitly (spec stories 37, 62).
# Anything not listed and not 2xx is TRANSPORT. Redirects are handled before this map.
_STATUS_FAIL_REASON: dict[int, FailReason] = {
    400: FailReason.VALIDATION_REQUEST,
    401: FailReason.AUTH,
    403: FailReason.AUTH,
    404: FailReason.NOT_FOUND,
    408: FailReason.TIMEOUT,
    422: FailReason.VALIDATION_REQUEST,
    429: FailReason.RATE_LIMIT,
    529: FailReason.OVERLOADED,
}


def fail_reason_for_status(status: int) -> FailReason:
    """Classify an HTTP status. 3xx must be handled as REDIRECT_REFUSED before calling this."""
    if 300 <= status < 400:
        return FailReason.REDIRECT_REFUSED
    if status in _STATUS_FAIL_REASON:
        return _STATUS_FAIL_REASON[status]
    if 500 <= status < 600:
        return FailReason.SERVER
    return FailReason.TRANSPORT


class JevKitError(Exception):
    """Base class. Instances never include raw server bodies or matched content."""


class ValidationError(JevKitError):
    """A response-validation check failed.

    Carries a fail reason and a short static check name (never the offending value),
    so nothing from the server body reaches logs or receipts through the exception.
    """

    def __init__(self, reason: FailReason, check: str) -> None:
        super().__init__(f"{reason.value}: {check}")
        self.reason = reason
        self.check = check
