"""jev_kit: a cautious client for TypeSafe's Jev.

Code owns authority; Jev supplies evidence. Advisory calls fail open to "no advice";
gates fail closed to "ask". See docs/specs/phase-1-client.md and docs/adr/.
"""

from jev_kit.engine import EngineConfig, decide, decide_batch, record_outcome, run_json
from jev_kit.errors import FailReason, ValidationError
from jev_kit.transport import LiveTransport, MockTransport, Transport
from jev_kit.types import (
    ConsequenceClass,
    Mode,
    QuestionType,
    Route,
)

__all__ = [
    "ConsequenceClass",
    "EngineConfig",
    "FailReason",
    "LiveTransport",
    "MockTransport",
    "Mode",
    "QuestionType",
    "Route",
    "Transport",
    "ValidationError",
    "decide",
    "decide_batch",
    "record_outcome",
    "run_json",
]

SCHEMA_VERSION = 1
