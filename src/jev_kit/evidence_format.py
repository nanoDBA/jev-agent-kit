"""Strict, bounded data readers for offline maintenance evidence, not runtime authority."""

from __future__ import annotations

import json
import math
import re
import stat
from pathlib import Path
from typing import cast

MAX_BYTES = 8 * 1024 * 1024


class EvidenceError(ValueError):
    """A fixed error code, never input content."""


def record(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise EvidenceError("record_shape")
    return cast(dict[str, object], value)


def items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise EvidenceError("list_shape")
    return cast(list[object], value)


def token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value):
        raise EvidenceError("invalid_token")
    return value


def digest(value: object, size: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value):
        raise EvidenceError("invalid_digest")
    return value


def integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise EvidenceError("invalid_integer")
    return value


def flag(value: object) -> bool:
    if type(value) is not bool:
        raise EvidenceError("invalid_boolean")
    return value


def bounded_number(value: object, minimum: float = 0.0, maximum: float = 1e12) -> float:
    if type(value) not in (int, float):
        raise EvidenceError("invalid_number")
    try:
        number = float(cast(int | float, value))
    except (OverflowError, ValueError):
        raise EvidenceError("invalid_number") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise EvidenceError("invalid_number")
    return number


def read_bytes(path: Path) -> bytes:
    # Reject special files before opening: a FIFO must not turn a read-only check into a hang.
    if not stat.S_ISREG(path.stat().st_mode):
        raise EvidenceError("not_regular_file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise EvidenceError("file_too_large")
    return raw


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_key")
        result[key] = value
    return result


def _constant(_: str) -> object:
    raise EvidenceError("nonfinite_number")


def parse(raw: bytes) -> object:
    if len(raw) > MAX_BYTES:
        raise EvidenceError("file_too_large")
    try:
        value: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                                   parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise EvidenceError("invalid_json") from None
    return value
