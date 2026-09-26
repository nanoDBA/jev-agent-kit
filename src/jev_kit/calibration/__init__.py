"""Calibration tooling: metrics, isotonic fitting (PAV) and promotion criteria.

Operates on labeled samples supplied by the caller, never on live traffic (spec
Phase 3/4 plan, E5: tooling and synthetic-fixture tests only; a real corpus and
enforce promotion are owner-gated). Stdlib only, per ADR 0003.
"""

from __future__ import annotations
