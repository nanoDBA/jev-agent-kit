"""Walk one risky tool call through the gate, step by step. The command never runs.

An agent wants to run `rm -rf ./build`. This script shows:

1. the request that would leave your machine (after the kit reduces it),
2. what Jev answers,
3. the route the kit derives from each answer in enforce mode, as a hook would.

Run it from the repository root (add --offline to replay the answer recorded on 2026-09-30):

    python examples/gate_walkthrough.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from _demo import setup  # noqa: E402 (after the src path setup)

from jev_kit.command_properties import (  # noqa: E402 (after the src path setup)
    extract_command_properties,
)
from jev_kit.engine import EngineConfig, decide  # noqa: E402 (after the src path setup)
from jev_kit.receipts import ReceiptWriter  # noqa: E402 (after the src path setup)

GATE = REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json"

COMMAND = "rm -rf ./build"

# The state a Claude Code hook builds from one PreToolUse event. The `cmd_*` facts are computed
# locally, in code, from the full command line (ADR 0005).
STATE = {
    "command": COMMAND,
    "target": "/home/alice/private-repo/build",
    "context": "tool=Bash; description=Delete everything in the build output folder",
    **extract_command_properties(COMMAND).as_state(),
}

MOCK_REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "destructive": {"type": "noul", "noul": 0.93},
        "exfiltrates": {"type": "noul", "noul": 0.01},
        "widens_permission": {"type": "noul", "noul": 0.04},
    },
}


def main(offline: bool | None = None) -> None:
    demo = setup(offline, recorded_on="2026-09-30")
    config = EngineConfig(
        attestation=demo.attestation,
        hmac_key=base64.urlsafe_b64decode("ZGVtby1rZXktZGVtby1rZXktZGVtby1rZXktZGVtby0="),
        source_allowlist=frozenset({"agent_context"}),
        writer=ReceiptWriter(directory=Path(tempfile.mkdtemp(prefix="jev-receipts-"))),
    )
    response = decide(
        {"schema_version": 1, "question_set_path": str(GATE), "state": STATE, "mode": "enforce"},
        transport=demo.transport(MOCK_REPLY),
        config=config,
    )

    sent = json.loads(demo.sent()[0])
    print("1. Your agent's tool call becomes this state:")
    print(json.dumps(STATE, indent=2))
    print("\n   What is actually sent to Jev (state only):")
    print(json.dumps(sent["state"], indent=2))
    print(f"\n2. Jev answers each question with p(yes)  ({demo.label})")
    print("3. The kit derives a route:")
    for rec in response["records"]:
        print(f"   {rec['question_id']:<18} p(yes)={rec['noul']:<5} -> {rec['route']}")
    print("\n   All routes are 'ask': no threshold is calibrated, so no answer can clear")
    print("   a gate, and a replayed answer never could.")


if __name__ == "__main__":
    main()
