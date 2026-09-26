"""Hermes plugin: register the jev-kit tool-call gate as a pre_tool_call hook.

Mode and question set come from the environment:
  JEV_KIT_HOOK_MODE               shadow (default) or enforce
  JEV_KIT_HOOK_QUESTION_SET_PATH  absolute path to tool-call-gate.json
"""

from jev_kit.hooks.hermes import register as _register_gate


def register(ctx):
    _register_gate(ctx)
