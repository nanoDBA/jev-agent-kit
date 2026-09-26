"""parse_retry_after must never yield a non-finite or negative delay, and must not raise on a
malformed header (finding H19)."""

from __future__ import annotations

from jev_kit.transport import parse_retry_after


def test_seconds_form() -> None:
    assert parse_retry_after({"retry-after": "5"}) == 5.0


def test_ms_form_preferred() -> None:
    assert parse_retry_after({"retry-after-ms": "2500"}) == 2.5


def test_infinity_is_ignored() -> None:
    # "inf"/"nan" parse as floats but must never become an unbounded or undefined delay.
    assert parse_retry_after({"retry-after": "inf"}) is None
    assert parse_retry_after({"retry-after-ms": "nan"}) is None


def test_negative_is_ignored() -> None:
    assert parse_retry_after({"retry-after": "-3"}) is None


def test_malformed_date_does_not_raise() -> None:
    # An unparseable HTTP-date raises inside parsedate_to_datetime on modern Python; it must be
    # swallowed and treated as "no header", not propagated.
    assert parse_retry_after({"retry-after": "not-a-date"}) is None
    assert parse_retry_after({"retry-after": "Mon, 99 Xxx 9999 99:99:99 ZZZ"}) is None
