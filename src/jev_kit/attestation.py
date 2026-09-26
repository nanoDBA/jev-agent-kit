"""Live-use attestation (spec story 52, ADR 0002 Amendment 1).

Before any live (non-mock) send, the engine requires a local attestation recording that
TypeSafe is in the owner's data-flow inventory. A DPA is required whenever any sent field is
of a kind that may carry personal data; ``dpa: false`` is allowed only when every sent field
is a non-personal Tier 3 kind. Mock transports never require an attestation.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from jev_kit.errors import FailReason, ValidationError

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def attestation_path(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("JEV_KIT_ATTESTATION")
    if env:
        return Path(env)
    return None


def load_attestation(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    resolved = attestation_path(path)
    if resolved is None:
        raise ValidationError(FailReason.ATTESTATION, "attestation_missing")
    try:
        obj = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(FailReason.ATTESTATION, "attestation_unreadable") from exc
    except ValueError as exc:
        raise ValidationError(FailReason.ATTESTATION, "attestation_bad_json") from exc
    if not isinstance(obj, dict):
        raise ValidationError(FailReason.ATTESTATION, "attestation_not_object")
    return obj


def check_attestation(attestation: dict[str, Any], *, personal_present: bool) -> None:
    """Raise ValidationError(ATTESTATION, ...) unless the attestation permits this send."""
    if attestation.get("inventory") is not True:
        raise ValidationError(FailReason.ATTESTATION, "inventory_not_attested")
    date = attestation.get("inventory_date")
    if not isinstance(date, str) or not _DATE.match(date):
        raise ValidationError(FailReason.ATTESTATION, "inventory_date")
    if personal_present and attestation.get("dpa") is not True:
        raise ValidationError(FailReason.ATTESTATION, "dpa_required")
