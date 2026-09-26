"""Host pre-tool-call hook adapters (Phase 3).

A shared core maps a host tool-call event to a decision request against the tool-call-gate
question set, runs it, and maps the route to a host-neutral outcome (allow or ask). Shadow
mode never changes host behavior; enforce fails closed to ask. The adapter always owns its own
deadline, so it never depends on a host hook timeout to fail closed (Codex fails open, Hermes
fails closed). Arming a host is owner-gated.
"""

from jev_kit.hooks.core import HookOutcome, HookResult, ToolCall, decide_tool_call

__all__ = ["HookOutcome", "HookResult", "ToolCall", "decide_tool_call"]
