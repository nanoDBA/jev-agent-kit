"""Keep examples/calibrate_walkthrough.py and docs/guides/calibration.md honest."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = (REPO / "docs" / "guides" / "calibration.md").read_text(encoding="utf-8")


def _run() -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JEV_KIT_", "TYPESAFE_"))}
    proc = subprocess.run(
        [sys.executable, str(REPO / "examples" / "calibrate_walkthrough.py")],
        env=env, capture_output=True, text=True, timeout=60, check=True,
    )
    assert proc.stderr == ""
    return proc.stdout


def test_walkthrough_declines_and_never_acts_on_a_replayed_answer() -> None:
    out = _run()
    assert "answered: 57  timed out: 3" in out
    assert "decision: do not promote; collect more labeled answers" in out
    assert "no fit samples: insufficient_data" in out
    assert ("with registry:    threshold_status=calibrated  would_route=accept  route=no_advice"
            in out)
    assert "model=mock" in out
    assert "  route=accept" not in out  # would_route may say accept; route never does


def test_guide_shows_the_walkthrough_output() -> None:
    # The guide quotes the walkthrough's output; every quoted line must be real.
    for line in _run().splitlines():
        assert line in GUIDE, line
