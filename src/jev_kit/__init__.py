"""jev_kit: a cautious client for TypeSafe's Jev.

Code owns authority; Jev supplies evidence. Advisory calls fail open to "no advice";
gates fail closed to "ask". See docs/specs/phase-1-client.md and docs/adr/.

This package is being built incrementally against that spec. The public surface
(decide, decide_batch, record_outcome, the CLI and the transport interface) is not
complete yet; only the pieces exported below are ready.
"""

from jev_kit.errors import FailReason, ValidationError
from jev_kit.types import (
    ConsequenceClass,
    Mode,
    QuestionType,
    Route,
)

__all__ = [
    "ConsequenceClass",
    "FailReason",
    "Mode",
    "QuestionType",
    "Route",
    "ValidationError",
]

SCHEMA_VERSION = 1
