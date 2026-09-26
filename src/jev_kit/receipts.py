"""Append-only JSONL receipts: durable, safe, and never the source of an unrecorded accept.

This module writes what the decide engine hands it. It does not build receipt content or
make routing decisions (that is the engine's job); it only serializes, appends, and commits.

Durability note (spec stories 74 to 77): every write in this module is a single open/append
followed by ``flush()`` and ``os.fsync(fileno())``. That is a promise of flush-to-disk, not of
crash-atomicity across the whole file: a crash between two calls' appends leaves the earlier
call's bytes intact and durable, but says nothing about the filesystem's own atomicity
guarantees for a single ``write``. The commit marker line at the end of each call's lines lets
a reader tell a complete call batch from one truncated mid-write (a missing or malformed final
line means the batch before it may be incomplete).

Any failure here (an unwritable directory, a full disk, a failed fsync) is reported as ``False``
rather than raised, so the engine can downgrade the calling decision to a safe route (``ask`` or
``no_advice``) instead of ever returning ``accept`` without a durable record (spec story 76).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jev_kit.errors import FailReason, ValidationError

_logger = logging.getLogger("jev_kit.receipts")

# Opaque local action ids, e.g. from new_action_id(). Bounded and safe to log (spec story 79a).
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Remote or server-supplied metadata ids (server request ids and similar). Slightly more
# generous length: these are not ours to shape, only to bound (spec story 79a).
_METADATA_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def new_action_id() -> str:
    """A fresh opaque local action id. Safe to persist and to echo back to the caller."""
    return "act_" + secrets.token_hex(16)


def validate_action_id(value: str) -> str:
    """Validate a caller-supplied action id against the safe id pattern.

    Returns the value unchanged when it matches. Raises otherwise, without echoing the
    value anywhere in the exception, so an unsafe action id can never carry content into a
    log or a receipt through its own rejection (spec story 79a).
    """
    if SAFE_ID_PATTERN.match(value) is None:
        raise ValidationError(FailReason.CONFIG, "action_id_invalid")
    return value


def validate_metadata_id(value: str | None, field_name: str) -> str | None:
    """Validate a piece of remote metadata (a server request id and similar).

    Unlike ``validate_action_id`` this never raises: an unsafe or oversized id is simply
    omitted (returned as ``None``) so a hostile or malformed remote value cannot reach a
    receipt or a record (spec story 79a). ``field_name`` names the field for diagnostics
    only; it is never included with the rejected value, because the value is never logged.
    """
    if value is None:
        return None
    if _METADATA_ID_PATTERN.match(value) is None:
        _best_effort_log("dropped unsafe metadata id for field %s", field_name)
        return None
    return value


def receipts_dir() -> Path:
    """Where receipts are written for this process.

    ``JEV_KIT_RECEIPTS_DIR`` is honored only when it is set and absolute; a relative value is
    ignored in favor of the platform default (spec story 78). This function never creates the
    directory; that happens on first write.
    """
    override = os.environ.get("JEV_KIT_RECEIPTS_DIR")
    if override:
        candidate = Path(override)
        if candidate.is_absolute():
            return candidate

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "jev_agent_kit" / "receipts"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "jev_agent_kit" / "receipts"

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        xdg_candidate = Path(xdg_state_home)
        if xdg_candidate.is_absolute():
            return xdg_candidate / "jev_agent_kit" / "receipts"
    return Path.home() / ".local" / "state" / "jev_agent_kit" / "receipts"


def _best_effort_log(message: str, *args: object) -> None:
    """Log a diagnostic without ever raising or recursing into receipt writing."""
    with contextlib.suppress(Exception):
        _logger.error(message, *args)


def _dump_line(payload: dict[str, Any]) -> str:
    # allow_nan=False makes a stray NaN or Infinity raise ValueError here rather than
    # silently write invalid JSON; callers treat that the same as an OSError (write failed).
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)


class ReceiptWriter:
    """Writes one process's receipts to one append-only JSONL file.

    The file name is fixed at construction time from the UTC start time and the process id
    (spec story 77), so every worker and every separate hook process gets its own file and
    concurrent writers never interleave lines. Within one process, a lock serializes appends.
    """

    def __init__(self, directory: Path | None = None) -> None:
        base = directory if directory is not None else receipts_dir()
        started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path: Path = base / f"{started}-{os.getpid()}.jsonl"
        self._lock = threading.Lock()

    def write_call(self, lines: list[dict[str, Any]]) -> bool:
        """Append every line for one call, then a commit marker, in a single durable write.

        Returns ``True`` once the bytes are flushed and fsynced. Returns ``False`` on any
        failure (an ``OSError`` from opening, writing or syncing, or a ``ValueError`` from a
        non-finite number reaching ``json.dumps``); it never raises, so the engine can fall
        back to a safe route instead of returning ``accept`` without a receipt.
        """
        call_id = lines[0].get("call_id") if lines else None
        commit = {"kind": "commit", "call_id": call_id, "lines": len(lines)}
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    for line in lines:
                        handle.write(_dump_line(line))
                        handle.write("\n")
                    handle.write(_dump_line(commit))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            return True
        except (OSError, ValueError):
            _best_effort_log("receipt write_call failed for %s", str(self.path))
            return False

    def append_outcome(self, outcome_line: dict[str, Any]) -> bool:
        """Append one outcome line, durably. The line itself is the record; no commit marker.

        Repeated outcomes for the same decision are allowed: each call appends a separate
        line. Returns ``False`` on any failure, on the same terms as ``write_call``.
        """
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(_dump_line(outcome_line))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            return True
        except (OSError, ValueError):
            _best_effort_log("receipt append_outcome failed for %s", str(self.path))
            return False


_default_writer: ReceiptWriter | None = None
_default_writer_lock = threading.Lock()


def get_writer() -> ReceiptWriter:
    """The process-wide default receipt writer, created lazily on first use."""
    global _default_writer
    with _default_writer_lock:
        if _default_writer is None:
            _default_writer = ReceiptWriter()
        return _default_writer
