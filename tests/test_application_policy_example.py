"""Application selection uses real engine answers without changing built-in routes."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from jev_kit.engine import EngineConfig, decide
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import ReceiptWriter
from jev_kit.transport import MockTransport

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # Import the runnable example without touching an installed host or requesting a key.
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    before = sys.path[:]
    try:
        return runpy.run_path(str(ROOT / "examples" / "route_request.py"))
    finally:
        sys.path[:] = before


def test_direct_selection_without_registry(
    example: dict[str, Any], tmp_path: Path,
) -> None:
    transport = MockTransport.replying(200, json.dumps(example["MOCK_REPLY"]).encode())
    response = decide(
        {"schema_version": 1, "mode": "shadow",
         "question_set_path": str(example["ROUTER"]),
         "state": {"request": "Customer asks for a refund."}},
        transport=transport,
        config=EngineConfig(source_allowlist=frozenset({"agent_request"}),
                            writer=ReceiptWriter(directory=tmp_path), rate_budget=RateBudget()),
    )
    assert response["status"] == "ok"
    record = response["records"][0]
    assert len(transport.requests) == 1
    assert record["route"] == "no_advice"
    assert record["threshold_status"] is None
    assert record["is_mock"] is True
    assert record["receipt_written"] is True
    assert record["label"] == "human"
    assert example["select_handler"](record) == "human"


@pytest.mark.parametrize("record", [
    {}, {"label": None}, {"label": "unclear"}, {"label": "unknown"},
    {"label": "human", "fail_reason": "timeout"},
    {"label": "human", "fail_reason": "receipt_failure"},
])
def test_selection_fallback(example: dict[str, Any], record: dict[str, object]) -> None:
    assert example["select_handler"](record) == "specialist_llm"


@pytest.mark.parametrize("response", [
    {"status": "error", "records": []},
    {"status": "ok", "records": [{"distribution": None, "label": None,
                                   "route": "no_advice", "fail_reason": "timeout"}]},
])
def test_demo_handles_missing_answer(
    example: dict[str, Any], response: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = example["handle"]
    monkeypatch.setitem(handle.__globals__, "decide", lambda *args, **kwargs: response)
    assert handle("Synthetic request", offline=True) == "specialist_llm"
