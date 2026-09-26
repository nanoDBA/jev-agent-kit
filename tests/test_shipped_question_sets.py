"""The shipped question sets load and meet the Phase 2 acceptance bar (D1, D6).

Each set in skills/jev-runtime/questions/ must load through the real loader, pin the versioned
model, name a non-model escalation target, and use gate consequence only where intended.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_kit.questionset import load_question_set_file
from jev_kit.types import ConsequenceClass

QUESTIONS_DIR = Path(__file__).resolve().parents[1] / "skills" / "jev-runtime" / "questions"
SET_FILES = sorted(QUESTIONS_DIR.glob("*.json"))
# Model names that must never be an escalation target (evidence must not escalate to a model).
_MODEL_HINTS = ("jev", "gpt", "claude", "gemini", "llm", "opus", "sonnet", "haiku")


def test_there_are_four_core_sets() -> None:
    names = {p.stem for p in SET_FILES}
    assert names == {"preflight-route", "postflight-verify", "tool-call-gate", "stop-or-continue"}


@pytest.mark.parametrize("path", SET_FILES, ids=lambda p: p.stem)
def test_set_loads_and_pins_model(path: Path) -> None:
    qs = load_question_set_file(path)
    assert qs.model == "jev-1.13.0"  # pinned, never an alias
    assert qs.questions


@pytest.mark.parametrize("path", SET_FILES, ids=lambda p: p.stem)
def test_escalation_target_is_not_a_model(path: Path) -> None:
    qs = load_question_set_file(path)
    assert not any(hint in qs.escalation_target.lower() for hint in _MODEL_HINTS)


def test_only_the_gate_set_has_gate_questions() -> None:
    for path in SET_FILES:
        qs = load_question_set_file(path)
        gate = any(q.consequence is ConsequenceClass.GATE for q in qs.questions.values())
        assert gate == (path.stem == "tool-call-gate")
