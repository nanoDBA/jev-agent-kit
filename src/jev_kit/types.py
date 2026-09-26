"""Core types: question kinds, consequence classes, routes, modes, and validated answers.

These are the kit's own view of a question (what it needs to validate a response and
route it). The wire request that goes to the API is built separately and carries only
the API fields; the kit fields here are never sent (spec story 39, Implementation Decisions).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class QuestionType(StrEnum):
    CHOICE = "choice"
    SCORE = "score"
    NOUL = "noul"


class ConsequenceClass(StrEnum):
    ADVISORY = "advisory"  # fails open to no_advice
    GATE = "gate"  # fails closed to ask


class Mode(StrEnum):
    SHADOW = "shadow"
    ENFORCE = "enforce"


class Route(StrEnum):
    ACCEPT = "accept"  # evidence cleared a calibrated, target-matched threshold
    ASK = "ask"  # send to a human or deterministic check
    NO_ADVICE = "no_advice"  # advisory gave up; workflow continues without evidence


# ---------------------------------------------------------------------------
# Questions (the kit view). Instructions and criteria are the exact strings the
# reviewed question-set file holds; they are also what goes on the wire.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceQuestion:
    question_id: str
    instructions: str
    options: tuple[str, ...]  # option keys, in file order; 2..255 (spec story 38)
    consequence: ConsequenceClass
    criteria: Mapping[str, Any] | None = None  # option key -> description, sent as-is
    question_type: QuestionType = QuestionType.CHOICE


@dataclass(frozen=True)
class ScoreQuestion:
    question_id: str
    instructions: str
    # Level descriptions in order, index 0..n-1; 2..10 levels (spec story 38).
    # legend[str(i)] returned by the API must equal levels[i] exactly (spec story 35).
    levels: tuple[str, ...]
    consequence: ConsequenceClass
    question_type: QuestionType = QuestionType.SCORE


@dataclass(frozen=True)
class NoulQuestion:
    question_id: str
    instructions: str
    consequence: ConsequenceClass
    criteria: Mapping[str, Any] | None = None  # optional {true, false} descriptions, sent as-is
    question_type: QuestionType = QuestionType.NOUL


Question = ChoiceQuestion | ScoreQuestion | NoulQuestion


# ---------------------------------------------------------------------------
# Validated answers. Produced only after every check in validation.py passes,
# so downstream routing can trust these fields.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceAnswer:
    question_id: str
    choice: str
    probabilities: dict[str, float]
    confidence: float
    question_type: QuestionType = QuestionType.CHOICE


@dataclass(frozen=True)
class ScoreAnswer:
    question_id: str
    score: float
    probabilities: dict[str, float]  # keys "0".."n-1"
    legend: dict[str, str]
    confidence: float
    question_type: QuestionType = QuestionType.SCORE


@dataclass(frozen=True)
class NoulAnswer:
    question_id: str
    noul: float
    question_type: QuestionType = QuestionType.NOUL


Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer
