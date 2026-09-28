"""Published-schema tests (finding C21, spec story 82).

Each schema file is a valid JSON Schema document, and worked examples load through the real
loaders so the published contracts stay tied to the code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_kit.attestation import check_attestation
from jev_kit.errors import ValidationError
from jev_kit.questionset import load_question_set
from jev_kit.registry import loads_registry

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "schemas"
SCHEMA_FILES = [
    "question-set.schema.json", "registry.schema.json", "attestation.schema.json",
    "cli-request.schema.json", "cli-response.schema.json", "decision-record.schema.json",
    "receipt.schema.json", "outcome.schema.json",
]


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_schema_is_valid_document(name: str) -> None:
    obj = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    assert obj["$schema"].endswith("schema")
    assert obj["$id"].endswith(name)
    assert "title" in obj


def test_example_question_set_loads() -> None:
    qs = load_question_set(
        {
            "schema_version": 1, "id": "example", "version": "1", "model": "jev-1.13.0",
            "escalation_target": "gpt-6",
            "questions": {
                "d": {"type": "noul", "instructions": "Destructive?",
                      "kit": {"consequence": "gate", "gate": {"allow_labels": ["no"]}}},
                "r": {"type": "score", "instructions": "How risky?",
                      "criteria": ["safe", "review", "unsafe"],
                      "kit": {"consequence": "gate", "gate": {"allow_labels": ["safe"]}}},
                "t": {"type": "choice", "instructions": "Which team?",
                      "criteria": {"billing": None, "tech": None},
                      "kit": {"consequence": "advisory"}},
            },
        }
    )
    assert set(qs.questions) == {"d", "r", "t"}


def test_example_registry_loads() -> None:
    reg = loads_registry(json.dumps({
        "schema_version": 1,
        "entries": {
            "a" * 64: {"status": "calibrated", "escalation_target": "gpt-6",
                       "evidence_ref": "ev-1", "type": "noul",
                       "threshold": {"yes_bound": 0.9, "no_bound": 0.1}, "date": "2026-09-25"}
        },
    }))
    assert len(reg) == 1


def test_example_attestation_checks() -> None:
    check_attestation({"inventory": True, "inventory_date": "2026-09-25", "dpa": True},
                      personal_present=True)
    with pytest.raises(ValidationError):
        check_attestation({"inventory": True, "inventory_date": "2026-09-25", "dpa": False},
                          personal_present=True)


def test_example_question_set_with_log_templates_loads() -> None:
    schema = json.loads((SCHEMA_DIR / "question-set.schema.json").read_text(encoding="utf-8"))
    assert "log_templates" in schema["properties"]
    qs = load_question_set(
        {
            "schema_version": 1, "id": "logs", "version": "1", "model": "jev-1.13.0",
            "escalation_target": "gpt-6",
            "questions": {"o": {"type": "noul", "instructions": "Outage?",
                                "kit": {"consequence": "advisory"}}},
            "log_templates": {
                "conn_failed": {
                    "text": "connection to {host} failed: {reason}",
                    "params": {"host": {"kind": "identifier"},
                               "reason": {"kind": "enum", "values": ["timeout"]}},
                }
            },
            "state_schema": {"events": {"kind": "log", "params": {"format": "template"}}},
        }
    )
    assert set(qs.log_templates) == {"conn_failed"}
