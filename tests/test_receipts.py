"""Receipt writer tests: durable append, commit markers, safe ids, and fault injection.

Covers spec stories 74 to 81 and 79a, and the receipt-write fault injection exception suite
(story 86, owner decision 4). No network. Everything is scoped to ``tmp_path`` and to a
process-local ``ReceiptWriter`` instance; nothing here touches the real receipts directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from jev_kit import receipts
from jev_kit.errors import FailReason, ValidationError
from jev_kit.receipts import (
    ReceiptWriter,
    new_action_id,
    validate_action_id,
    validate_metadata_id,
)


def _read_lines(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


# --- write_call: commit marker and durability -------------------------------


def test_write_call_ends_with_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    ok = writer.write_call(
        [
            {"call_id": "call-1", "decision_id": "d1", "route": "ask"},
            {"call_id": "call-1", "decision_id": "d2", "route": "no_advice"},
        ]
    )

    assert ok is True
    lines = _read_lines(writer.path)
    assert len(lines) == 3
    assert lines[-1] == {"kind": "commit", "call_id": "call-1", "lines": 2}


def test_write_call_commit_marker_null_call_id_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    ok = writer.write_call([{"decision_id": "d1"}])

    assert ok is True
    lines = _read_lines(writer.path)
    assert lines[-1] == {"kind": "commit", "call_id": None, "lines": 1}


def test_append_outcome_has_no_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    assert writer.append_outcome({"decision_id": "d1", "outcome_code": "applied"}) is True
    outcome_2 = {"decision_id": "d1", "outcome_code": "overridden_by_human"}
    assert writer.append_outcome(outcome_2) is True

    lines = _read_lines(writer.path)
    assert lines == [
        {"decision_id": "d1", "outcome_code": "applied"},
        {"decision_id": "d1", "outcome_code": "overridden_by_human"},
    ]


# --- Fault injection (spec story 86) -----------------------------------------


def test_write_call_returns_false_when_directory_unwritable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    writer = ReceiptWriter(directory=blocker / "receipts")

    ok = writer.write_call([{"call_id": "call-1"}])

    assert ok is False
    assert not (blocker / "receipts").exists()


def test_write_call_returns_false_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    def _raise_fsync(_fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", _raise_fsync)

    assert writer.write_call([{"call_id": "call-1"}]) is False


def test_append_outcome_returns_false_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    def _raise_fsync(_fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", _raise_fsync)

    assert writer.append_outcome({"decision_id": "d1", "outcome_code": "applied"}) is False


def test_write_call_returns_false_on_nan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    writer = ReceiptWriter()

    assert writer.write_call([{"call_id": "call-1", "value": float("nan")}]) is False


# --- Safe ids (spec story 79a) ------------------------------------------------


def test_new_action_id_round_trips_through_validate_action_id() -> None:
    action_id = new_action_id()
    assert validate_action_id(action_id) == action_id


def test_unsafe_action_id_raises_without_echoing_value() -> None:
    unsafe = "not an id! has spaces and !"
    with pytest.raises(ValidationError) as excinfo:
        validate_action_id(unsafe)

    assert excinfo.value.reason is FailReason.CONFIG
    assert unsafe not in str(excinfo.value)
    assert unsafe not in repr(excinfo.value)


def test_validate_metadata_id_accepts_safe_value() -> None:
    safe = "req_abc-123.456:789"
    assert validate_metadata_id(safe, "server_request_id") == safe


def test_validate_metadata_id_drops_unsafe_value() -> None:
    assert validate_metadata_id("has a space", "server_request_id") is None


def test_validate_metadata_id_passes_through_none() -> None:
    assert validate_metadata_id(None, "server_request_id") is None


def test_validate_metadata_id_drops_oversized_value() -> None:
    assert validate_metadata_id("a" * 201, "server_request_id") is None


# --- receipts_dir: override and platform defaults (spec story 78) -----------


def test_relative_receipts_dir_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", "relative/path")

    result = receipts.receipts_dir()

    assert result.is_absolute()


def test_absolute_receipts_dir_override_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))

    assert receipts.receipts_dir() == tmp_path


def test_receipts_dir_windows_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    result = receipts.receipts_dir()

    assert result == tmp_path / "jev-agent-kit" / "receipts"


def test_receipts_dir_macos_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = receipts.receipts_dir()

    expected = tmp_path / "Library" / "Application Support" / "jev-agent-kit" / "receipts"
    assert result == expected


def test_receipts_dir_linux_xdg_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    result = receipts.receipts_dir()

    assert result == tmp_path / "jev-agent-kit" / "receipts"


def test_receipts_dir_linux_relative_xdg_state_home_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", "relative/xdg")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = receipts.receipts_dir()

    assert result == tmp_path / ".local" / "state" / "jev-agent-kit" / "receipts"


def test_receipts_dir_linux_default_without_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = receipts.receipts_dir()

    assert result == tmp_path / ".local" / "state" / "jev-agent-kit" / "receipts"


# --- get_writer: lazy per-process singleton ----------------------------------


def test_get_writer_returns_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_KIT_RECEIPTS_DIR", str(tmp_path))
    monkeypatch.setattr(receipts, "_default_writer", None)

    first = receipts.get_writer()
    second = receipts.get_writer()

    assert first is second


def test_receipts_dir_keeps_a_pre_rename_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("JEV_KIT_RECEIPTS_DIR", raising=False)
    monkeypatch.setattr(receipts, "_state_base", lambda: tmp_path)
    assert receipts.receipts_dir() == tmp_path / "jev-agent-kit" / "receipts"
    legacy = tmp_path / "jev_agent_kit" / "receipts"
    legacy.mkdir(parents=True)
    assert receipts.receipts_dir() == legacy
    (tmp_path / "jev-agent-kit" / "receipts").mkdir(parents=True)
    assert receipts.receipts_dir() == tmp_path / "jev-agent-kit" / "receipts"
