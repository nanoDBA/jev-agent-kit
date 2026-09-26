"""Tests for tools/receipts_report.py: commit-marker honoring, summarizing, and the optional
DuckDB loader.

Everything here builds synthetic receipt dicts by hand (mirroring the JSONL shape written by
``jev_kit.receipts.ReceiptWriter`` and ``jev_kit.engine._write_receipts``); nothing calls the
engine. No network, and the DuckDB-present test is skipped if duckdb is not installed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "tools" / "receipts_report.py"
_spec = importlib.util.spec_from_file_location("receipts_report", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
receipts_report = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = receipts_report
_spec.loader.exec_module(receipts_report)

read_receipts = receipts_report.read_receipts
summarize = receipts_report.summarize
to_duckdb = receipts_report.to_duckdb
main = receipts_report.main


def _decision(
    call_id: str,
    decision_id: str,
    *,
    route: str = "ask",
    fail_reason: str | None = None,
    mode: str = "shadow",
    is_mock: bool = False,
    fingerprint: str = "fp-1",
    noul: float | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    return {
        "kind": "decision",
        "call_id": call_id,
        "decision_id": decision_id,
        "question_id": "q1",
        "fingerprint": fingerprint,
        "route": route,
        "would_route": route,
        "threshold": 0.5,
        "threshold_status": "met",
        "model": "mock-model",
        "simulated_model": None,
        "is_mock": is_mock,
        "fail_reason": fail_reason,
        "mode": mode,
        "estimated_tokens": 10,
        "reported_tokens": 12,
        "noul": noul,
        "confidence": confidence,
    }


def _commit(call_id: str, lines: int) -> dict[str, Any]:
    return {"kind": "commit", "call_id": call_id, "lines": lines}


def _outcome(decision_id: str, outcome: str) -> dict[str, Any]:
    return {"kind": "outcome", "decision_id": decision_id, "outcome": outcome}


def _write_jsonl(path: Path, lines: list[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            if isinstance(line, str):
                handle.write(line)
            else:
                handle.write(json.dumps(line))
            handle.write("\n")


# --- read_receipts: commit markers -------------------------------------------------------


def test_read_receipts_drops_truncated_trailing_batch(tmp_path: Path) -> None:
    call_1 = [_decision("call-1", "d1"), _decision("call-1", "d2")]
    call_2 = [_decision("call-2", "d3"), _decision("call-2", "d4"), _decision("call-2", "d5")]
    truncated = [_decision("call-3", "d6"), _decision("call-3", "d7")]  # no commit marker

    path = tmp_path / "receipts.jsonl"
    _write_jsonl(
        path,
        [*call_1, _commit("call-1", 2), *call_2, _commit("call-2", 3), *truncated],
    )

    stats: dict[str, int] = {}
    decisions = read_receipts(path, stats=stats)

    assert [d["decision_id"] for d in decisions] == ["d1", "d2", "d3", "d4", "d5"]
    assert stats["dropped_batches"] == 1
    assert stats["committed_calls"] == 2
    assert stats["malformed_lines"] == 0


def test_read_receipts_drops_batch_with_wrong_commit_line_count(tmp_path: Path) -> None:
    good = [_decision("call-1", "d1"), _decision("call-1", "d2")]
    bad = [_decision("call-2", "d3"), _decision("call-2", "d4")]

    path = tmp_path / "receipts.jsonl"
    _write_jsonl(
        path,
        [
            *good,
            _commit("call-1", 2),
            *bad,
            _commit("call-2", 3),  # wrong count: only 2 decision lines were written
        ],
    )

    stats: dict[str, int] = {}
    decisions = read_receipts(path, stats=stats)

    assert [d["decision_id"] for d in decisions] == ["d1", "d2"]
    assert stats["dropped_batches"] == 1
    assert stats["committed_calls"] == 1


def test_read_receipts_skips_malformed_lines_and_counts(tmp_path: Path) -> None:
    good = [_decision("call-1", "d1"), _decision("call-1", "d2")]

    path = tmp_path / "receipts.jsonl"
    _write_jsonl(
        path,
        [
            "{not valid json",
            *good,
            "[1, 2, 3]",  # valid JSON, not an object with a recognized kind
            _commit("call-1", 2),
            "another garbage line }{",
        ],
    )

    stats: dict[str, int] = {}
    decisions = read_receipts(path, stats=stats)

    assert [d["decision_id"] for d in decisions] == ["d1", "d2"]
    assert stats["malformed_lines"] == 3
    assert stats["dropped_batches"] == 0


def test_read_receipts_attaches_outcomes_to_matching_decision(tmp_path: Path) -> None:
    call = [_decision("call-1", "d1"), _decision("call-1", "d2")]

    path = tmp_path / "receipts.jsonl"
    _write_jsonl(
        path,
        [*call, _commit("call-1", 2), _outcome("d1", "applied"), _outcome("d1", "overridden")],
    )

    decisions = read_receipts(path)
    by_id = {d["decision_id"]: d for d in decisions}

    assert [o["outcome"] for o in by_id["d1"]["outcomes"]] == ["applied", "overridden"]
    assert by_id["d2"]["outcomes"] == []


def test_read_receipts_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    stats: dict[str, int] = {}
    assert read_receipts(path, stats=stats) == []
    assert stats == {"malformed_lines": 0, "dropped_batches": 0, "committed_calls": 0}


# --- summarize -----------------------------------------------------------------------------


def test_summarize_counts_route_fail_reason_mode_and_is_mock() -> None:
    decisions = [
        _decision("c", "d1", route="accept", mode="shadow", is_mock=False, fingerprint="fp-a"),
        _decision(
            "c",
            "d2",
            route="ask",
            fail_reason="uncalibrated",
            mode="shadow",
            is_mock=True,
            fingerprint="fp-a",
        ),
        _decision(
            "c",
            "d3",
            route="no_advice",
            fail_reason="timeout",
            mode="advisory",
            is_mock=False,
            fingerprint="fp-b",
        ),
    ]

    summary = summarize(decisions)

    assert summary["total"] == 3
    assert summary["by_route"] == {"accept": 1, "ask": 1, "no_advice": 1}
    assert summary["by_fail_reason"] == {"none": 1, "uncalibrated": 1, "timeout": 1}
    assert summary["by_mode"] == {"shadow": 2, "advisory": 1}
    assert summary["by_is_mock"] == {False: 2, True: 1}


def test_summarize_per_fingerprint_counts_and_means() -> None:
    decisions = [
        _decision("c", "d1", fingerprint="fp-a", noul=0.2, confidence=0.9),
        _decision("c", "d2", fingerprint="fp-a", noul=0.6, confidence=None),
        _decision("c", "d3", fingerprint="fp-b"),
    ]

    summary = summarize(decisions)

    fp_a = summary["by_fingerprint"]["fp-a"]
    assert fp_a["count"] == 2
    assert fp_a["mean_noul"] == pytest.approx(0.4)
    assert fp_a["mean_confidence"] == pytest.approx(0.9)

    fp_b = summary["by_fingerprint"]["fp-b"]
    assert fp_b["count"] == 1
    assert fp_b["mean_noul"] is None
    assert fp_b["mean_confidence"] is None


def test_summarize_empty_list() -> None:
    summary = summarize([])
    assert summary["total"] == 0
    assert summary["by_route"] == {}
    assert summary["by_fingerprint"] == {}


# --- to_duckdb -------------------------------------------------------------------------------


def test_to_duckdb_raises_clear_error_when_duckdb_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "duckdb", None)  # forces "import duckdb" to raise

    path = tmp_path / "receipts.jsonl"
    _write_jsonl(path, [_decision("call-1", "d1"), _commit("call-1", 1)])

    with pytest.raises(RuntimeError, match="duckdb is not installed"):
        to_duckdb(path, str(tmp_path / "out.duckdb"))


def test_to_duckdb_loads_committed_decisions_when_available(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")

    path = tmp_path / "receipts.jsonl"
    truncated = [_decision("call-2", "d3")]  # no commit: must not be loaded
    _write_jsonl(
        path,
        [
            _decision("call-1", "d1"),
            _decision("call-1", "d2"),
            _commit("call-1", 2),
            *truncated,
        ],
    )

    db_path = tmp_path / "out.duckdb"
    to_duckdb(path, str(db_path))

    connection = duckdb.connect(str(db_path))
    try:
        rows = connection.execute(
            "SELECT decision_id FROM receipts_decisions ORDER BY decision_id"
        ).fetchall()
    finally:
        connection.close()

    assert [row[0] for row in rows] == ["d1", "d2"]


# --- CLI ---------------------------------------------------------------------------------


def test_main_prints_json_summary_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "receipts.jsonl"
    _write_jsonl(path, [_decision("call-1", "d1"), _commit("call-1", 1)])

    exit_code = main([str(path)])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total"] == 1
    assert output["read_stats"]["committed_calls"] == 1


def test_route_correction_supersedes_decision_route_h8(tmp_path: Any) -> None:
    # A route_correction downgrades an already-committed accept (a late accept the engine
    # reversed after a slow receipt write); the reader and summary must report the corrected
    # route, not the stale accept (finding H8).
    import json as _json

    from tools.receipts_report import read_receipts, summarize

    path = tmp_path / "r.jsonl"
    lines = [
        {"kind": "decision", "call_id": "c1", "decision_id": "d1", "route": "accept",
         "fail_reason": None},
        {"kind": "commit", "call_id": "c1", "lines": 1},
        {"kind": "route_correction", "call_id": "c1", "decision_id": "d1", "route": "ask",
         "fail_reason": "timeout"},
    ]
    path.write_text("\n".join(_json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    stats: dict[str, int] = {}
    decisions = read_receipts(path, stats=stats)
    assert decisions[0]["route"] == "ask"
    assert decisions[0]["fail_reason"] == "timeout"
    assert decisions[0]["route_before_correction"] == "accept"
    assert stats["malformed_lines"] == 0  # the correction line is a recognized kind
    assert summarize(decisions)["by_route"] == {"ask": 1}
