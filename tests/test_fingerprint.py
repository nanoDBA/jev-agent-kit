"""Fingerprint and canonical-serialization tests (spec stories 69 to 71; findings R02, R14)."""

from __future__ import annotations

import pytest

from jev_kit.fingerprint import (
    canonical_bytes,
    parse_canonical,
    question_fingerprint,
    sha256_hex,
)


def test_canonical_is_key_order_independent() -> None:
    assert canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1})


def test_canonical_preserves_array_order() -> None:
    assert canonical_bytes(["a", "b"]) != canonical_bytes(["b", "a"])


def test_canonical_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})


def test_parse_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError):
        parse_canonical('{"a": 1, "a": 2}')


def test_parse_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        parse_canonical('{"x": NaN}')


def _fp(egress: dict[str, object]) -> str:
    return question_fingerprint(
        instructions="Is it destructive?",
        criteria=None,
        question_type="noul",
        option_or_level_set=[],
        model="jev-1.13.0",
        egress_contract=egress,
    )


def test_fingerprint_changes_when_egress_contract_changes() -> None:
    # Finding R02: changing a transform must invalidate calibration.
    masked = _fp({"body": {"kind": "contact", "transform": "mask"}})
    hashed = _fp({"body": {"kind": "identifier", "transform": "hmac"}})
    assert masked != hashed


def test_fingerprint_stable_for_same_contract() -> None:
    assert _fp({"body": {"kind": "contact"}}) == _fp({"body": {"kind": "contact"}})


def test_sha256_hex_is_hex_64() -> None:
    value = sha256_hex({"a": 1})
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
