"""Canonical serialization and fingerprints (spec stories 69 to 71).

A question fingerprint covers everything that could change the answer: the question text
and criteria, its type and option or level set in order, the pinned model, and the full
effective egress contract for its state. Per-call state and secret key material are never
hashed in. Canonical JSON is UTF-8 with sorted object keys, preserved array order, finite
numbers only, and duplicate keys rejected on the way in.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key in canonical input: {key}")
        seen[key] = value
    return seen


def canonical_bytes(obj: Any) -> bytes:
    """Serialize obj to canonical JSON bytes: sorted keys, compact, no NaN or Infinity.

    Object keys are sorted; array order is preserved. Raises ValueError on non-finite
    numbers. The input is expected to be built from plain dicts, lists, str, int, float,
    bool and None.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def parse_canonical(text: str) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite numbers (spec story 71)."""
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number in JSON input")


def question_fingerprint(
    *,
    instructions: Any,
    criteria: Any,
    question_type: str,
    option_or_level_set: list[str],
    model: str,
    egress_contract: dict[str, Any],
) -> str:
    """Fingerprint one question's complete effective contract (spec story 69).

    option_or_level_set is kept in order (it is a list, and array order is preserved).
    egress_contract is the effective per-field egress settings plus the content hashes of
    the policy and profile rule files, built by the caller.
    """
    payload = {
        "instructions": instructions,
        "criteria": criteria,
        "type": question_type,
        "option_or_level_set": option_or_level_set,
        "model": model,
        "egress": egress_contract,
    }
    return sha256_hex(payload)


def question_set_digest(question_set_file: dict[str, Any]) -> str:
    """Digest of the whole question-set file, recorded alongside each fingerprint (story 70)."""
    return sha256_hex(question_set_file)


def content_hash(rule_file_obj: dict[str, Any]) -> str:
    """Content identity of a policy or profile rule file, for the egress contract."""
    return sha256_hex(rule_file_obj)
