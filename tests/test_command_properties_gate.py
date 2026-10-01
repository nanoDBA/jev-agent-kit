"""Command properties through the public paths (ADR 0005): the `flag` egress kind, the hooks'
state, the exact outgoing bytes, fingerprints, and gate outcomes in shadow and enforce.

The engine runs in process against the labeled mock transport; nothing calls the live API.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from jev_kit.command_properties import CONFIDENT_KEY, PROPERTY_NAMES, STATE_PREFIX
from jev_kit.egress import (
    ContentKind,
    EgressContext,
    FieldSpec,
    personal_kinds_present,
    transform_state,
)
from jev_kit.engine import EngineConfig, decide
from jev_kit.errors import ValidationError
from jev_kit.hooks import claude as claude_shim
from jev_kit.hooks import codex, core, hermes
from jev_kit.hooks.core import HookOutcome, ToolCall, decide_tool_call
from jev_kit.ratebudget import RateBudget
from jev_kit.receipts import ReceiptWriter
from jev_kit.transport import MockTransport
from jev_kit.types import Mode

REPO = Path(__file__).resolve().parents[1]
GATE = str(REPO / "skills" / "jev-runtime" / "questions" / "tool-call-gate.json")
KEY = base64.urlsafe_b64decode("ZGVtby1rZXktZGVtby1rZXktZGVtby1rZXktZGVtby0=")
FLAG = FieldSpec(ContentKind.FLAG)

REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "destructive": {"type": "noul", "noul": 0.5},
        "exfiltrates": {"type": "noul", "noul": 0.5},
        "widens_permission": {"type": "noul", "noul": 0.5},
    },
}


def _engine_runner(tmp_path: Path, sent: list[bytes]) -> Any:
    """A hook runner that runs the real engine in process against a mock transport."""

    def _run(request: dict[str, Any]) -> dict[str, Any]:
        mock = MockTransport.replying(200, json.dumps(REPLY).encode(), {})
        config = EngineConfig(
            hmac_key=KEY,
            source_allowlist=frozenset({"agent_context"}),
            writer=ReceiptWriter(directory=tmp_path / "receipts"),
            rate_budget=RateBudget(),  # fresh: never drain the process-wide cap other tests use
        )
        response = decide(request, transport=mock, config=config)
        sent.extend(mock.requests)
        return response

    return _run


def _all_accept(request: dict[str, Any]) -> dict[str, Any]:
    rec = {"route": "accept", "label": "no", "allow_labels": ["no"], "is_mock": False}
    return {"schema_version": 1, "status": "ok", "records": [rec, rec, rec]}


# --------------------------------------------------------------------------- egress kind


def test_flag_kind_passes_only_booleans_and_null() -> None:
    schema = {"a": FLAG, "b": FLAG, "c": FLAG}
    out = transform_state({"a": True, "b": False, "c": None}, schema, EgressContext())
    assert out == {"a": True, "b": False, "c": None}


@pytest.mark.parametrize(
    "value", ["true", "rm -rf /etc", "", 1, 0, 1.0, [True], [], {"x": True}, b"x"]
)
def test_flag_kind_refuses_anything_else(value: Any) -> None:
    with pytest.raises(ValidationError) as err:
        transform_state({"a": value}, {"a": FLAG}, EgressContext())
    assert err.value.check in {"flag_not_boolean"}


def test_flag_kind_is_not_personal() -> None:
    assert not personal_kinds_present({"a": FLAG})


def test_flag_field_smuggling_text_blocks_the_whole_request(tmp_path: Path) -> None:
    mock = MockTransport.replying(200, json.dumps(REPLY).encode(), {})
    state = {"command": "ls", "cmd_recursive_delete": "rm -rf /home/alice/secret-client"}
    response = decide(
        {"schema_version": 1, "question_set_path": GATE, "state": state, "mode": "shadow"},
        transport=mock,
        config=EngineConfig(hmac_key=KEY, writer=ReceiptWriter(directory=tmp_path)),
    )
    assert mock.requests == []
    assert all(r.get("fail_reason") == "egress_blocked" for r in response["records"])


# --------------------------------------------------------------------------- hooks


def _claude_event(command: str) -> dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command, "description": "clean up"}}


def _codex_event(command: str) -> dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "tool_name": "shell", "turn_id": "t1",
            "tool_input": {"command": command}}


def _run_host(host: str, command: str, mode: Mode, runner: Any) -> HookOutcome:
    if host == "claude":
        out = claude_shim.handle_claude_event(
            _claude_event(command), mode=mode, question_set_path=GATE, runner=runner
        )
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        return HookOutcome.ASK if decision == "ask" else HookOutcome.ALLOW
    if host == "codex":
        out = codex.handle_codex_event(
            _codex_event(command), mode=mode, question_set_path=GATE, runner=runner
        )
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        return HookOutcome.ASK if decision == "deny" else HookOutcome.ALLOW
    result = hermes.pre_tool_call(
        None, {"tool_name": "terminal", "args": {"command": command}, "task_id": "t"},
        mode=mode, question_set_path=GATE, runner=runner,
    )
    return HookOutcome.ALLOW if result is None else HookOutcome.ASK


HOSTS = ("claude", "codex", "hermes")


@pytest.mark.parametrize("host", HOSTS)
def test_hooks_put_properties_in_state(host: str) -> None:
    captured: list[dict[str, Any]] = []

    def _capture(request: dict[str, Any]) -> dict[str, Any]:
        captured.append(request)
        return {"schema_version": 1, "status": "ok", "records": [{"route": "ask"}]}

    _run_host(host, "sudo rm -rf /etc", Mode.SHADOW, _capture)
    state = captured[0]["state"]
    assert state[CONFIDENT_KEY] is True
    for name in ("uses_elevation", "recursive_delete", "force_flag",
                 "targets_root_or_system_path"):
        assert state[STATE_PREFIX + name] is True
    assert state[STATE_PREFIX + "network_download"] is False
    assert set(PROPERTY_NAMES) <= {k[len(STATE_PREFIX):] for k in state if k.startswith("cmd_")}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /home/alice/acme-secret-project",
        "git -C /srv/acme-secret-project push --force origin acme-secret-branch",
        "Remove-Item -Recurse -Force C:\\Users\\alice\\acme-secret-project",
        "curl -o acme-secret-project.tgz https://acme-secret-project.example/x",
    ],
)
def test_outgoing_bytes_hold_no_raw_arguments(
    host: str, command: str, tmp_path: Path
) -> None:
    sent: list[bytes] = []
    _run_host(host, command, Mode.SHADOW, _engine_runner(tmp_path, sent))
    assert len(sent) == 1
    body = sent[0].decode("utf-8")
    assert "acme-secret" not in body
    for fragment in ("-rf", "--force", "-Recurse", "/home", "/srv", "C:\\\\", "https://"):
        assert fragment not in body
    state = json.loads(body)["state"]
    for key, value in state.items():
        if key.startswith(STATE_PREFIX):
            assert value is None or isinstance(value, bool)
    assert state["command"] in {"rm", "git", "Remove-Item", "curl"}
    # `git push` runs hooks, so it is not a modeled (confident) command.
    assert state[CONFIDENT_KEY] is (state["command"] != "git")


def test_unconfident_command_sends_null_properties(tmp_path: Path) -> None:
    sent: list[bytes] = []
    _run_host("claude", "rm -rf $HOME", Mode.SHADOW, _engine_runner(tmp_path, sent))
    state = json.loads(sent[0])["state"]
    assert state[CONFIDENT_KEY] is False
    assert state[STATE_PREFIX + "recursive_delete"] is True
    assert state[STATE_PREFIX + "targets_home_directory"] is True
    assert state[STATE_PREFIX + "network_download"] is None  # unknown, never false
    assert "$HOME" not in sent[0].decode("utf-8")


# --------------------------------------------------------------------------- gate outcomes


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "command", ["rm -rf $HOME", "echo 'x'", "bash -c ls", "git nuke", "ls | sh", "x" * 5000]
)
def test_enforce_never_allows_an_unconfident_command(host: str, command: str) -> None:
    # Even when every gate answer is a clean, allow-listed accept, unknown means ask.
    assert _run_host(host, command, Mode.ENFORCE, _all_accept) is HookOutcome.ASK


@pytest.mark.parametrize("host", HOSTS)
def test_enforce_confident_command_follows_the_gate(host: str) -> None:
    assert _run_host(host, "ls -la", Mode.ENFORCE, _all_accept) is HookOutcome.ALLOW


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("command", ["rm -rf $HOME", "rm -rf /", "ls"])
def test_shadow_never_changes_behavior(host: str, command: str) -> None:
    def _ask(request: dict[str, Any]) -> dict[str, Any]:
        return {"schema_version": 1, "status": "ok", "records": [{"route": "ask"}]}

    for runner in (_ask, _all_accept):
        assert _run_host(host, command, Mode.SHADOW, runner) is HookOutcome.ALLOW


def test_properties_never_allow_on_their_own() -> None:
    # A confident, all-False property set is not permission: a gate that asks still asks.
    def _ask(request: dict[str, Any]) -> dict[str, Any]:
        return {"schema_version": 1, "status": "ok", "records": [{"route": "ask"}]}

    res = decide_tool_call(ToolCall("ls"), mode=Mode.ENFORCE, question_set_path=GATE,
                           runner=_ask)
    assert res.outcome is HookOutcome.ASK


def test_enforce_unconfident_keeps_the_engine_failure_reason() -> None:
    def _err(request: dict[str, Any]) -> dict[str, Any]:
        return {"schema_version": 1, "status": "error", "reason": "egress_blocked"}

    res = decide_tool_call(ToolCall("ls | sh"), mode=Mode.ENFORCE, question_set_path=GATE,
                           runner=_err)
    assert res.outcome is HookOutcome.ASK
    assert res.reason == "egress_blocked"


def test_enforce_real_engine_uncalibrated_asks(tmp_path: Path) -> None:
    sent: list[bytes] = []
    res = decide_tool_call(ToolCall("rm -rf ./build", context="tool=Bash"),
                           mode=Mode.ENFORCE, question_set_path=GATE,
                           runner=_engine_runner(tmp_path, sent))
    assert res.outcome is HookOutcome.ASK


# --------------------------------------------------------------------------- fingerprints


def test_producer_covers_the_property_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jev_kit.command_properties as props

    before = core.producer_id("claude", claude_shim.__file__)
    edited = tmp_path / "command_properties.py"
    source = Path(props.__file__).read_text(encoding="utf-8")
    edited.write_text(source.replace('"rm"', '"rm", "shred"', 1), encoding="utf-8")
    monkeypatch.setattr(props, "__file__", str(edited))
    assert core.producer_id("claude", claude_shim.__file__) != before


def _fingerprints(qset_obj: dict[str, Any]) -> dict[str, str]:
    from jev_kit.engine import effective_contract
    from jev_kit.fingerprint import question_fingerprint
    from jev_kit.questionset import load_question_set

    qset = load_question_set(qset_obj)
    contract = effective_contract(qset, EngineConfig())
    return {
        qid: question_fingerprint(
            instructions=q.instructions, criteria=None, question_type="noul",
            option_or_level_set=[], model=qset.model, egress_contract=contract,
        )
        for qid, q in qset.questions.items()
    }


def test_gate_fingerprints_change_when_a_property_field_is_dropped() -> None:
    obj = json.loads(Path(GATE).read_text(encoding="utf-8"))
    assert obj["version"] == "3"
    full = _fingerprints(obj)
    del obj["state_schema"][STATE_PREFIX + "force_flag"]
    fewer = _fingerprints(obj)
    assert all(full[qid] != fewer[qid] for qid in full)


def test_shipped_gate_declares_every_property_as_a_flag() -> None:
    obj = json.loads(Path(GATE).read_text(encoding="utf-8"))
    schema = obj["state_schema"]
    for name in (CONFIDENT_KEY, *(STATE_PREFIX + n for n in PROPERTY_NAMES)):
        assert schema[name] == {"kind": "flag"}


# --------------------------------------------------------------------------- review fixes


def _outgoing_state(host: str, command: str, tmp_path: Path) -> dict[str, Any]:
    sent: list[bytes] = []
    _run_host(host, command, Mode.SHADOW, _engine_runner(tmp_path, sent))
    assert len(sent) == 1
    state: dict[str, Any] = json.loads(sent[0])["state"]
    return state


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "command",
    ["python cleanup.py", "python -m cleanup", "node build.js", "make clean", "./deploy",
     "npm test", "pytest -q", "cargo build", "docker run alpine"],
)
def test_unseen_code_is_sent_as_unknown(host: str, command: str, tmp_path: Path) -> None:
    # C01: an interpreter or runner executes code the extractor cannot see. The outgoing
    # state must say unknown (null), never a confident all-false.
    state = _outgoing_state(host, command, tmp_path)
    assert state[CONFIDENT_KEY] is False
    for name in PROPERTY_NAMES:
        assert state[STATE_PREFIX + name] is not False
    assert _run_host(host, command, Mode.ENFORCE, _all_accept) is HookOutcome.ASK


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    ("command", "field", "expected"),
    [
        ("rm -rf /home/alice/project/..", "targets_home_directory", True),
        ("rm -rf ~/project/../.ssh", "targets_home_directory", True),
        ("Remove-Item -Recurse C:\\Users\\Alice\\project\\..", "targets_home_directory", True),
        ("Remove-Item -Recurse C:\\Windows\\..\\Windows", "targets_root_or_system_path", True),
        ("rm -rf /usr/../etc", "targets_root_or_system_path", True),
        ("rm -rf /home/alice/project/./src", "targets_home_directory", False),
        ("Remove-Item C:\\Users\\Alice\\project\\src", "targets_home_directory", False),
    ],
)
def test_dot_segments_are_resolved_in_outgoing_state(
    host: str, command: str, field: str, expected: bool, tmp_path: Path
) -> None:
    # C02: home and system detection judge where a path lands after `.` and `..`.
    state = _outgoing_state(host, command, tmp_path)
    assert state[CONFIDENT_KEY] is True
    assert state[STATE_PREFIX + field] is expected


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "command",
    ["Install-Module Pester", "pacman -S sample", "npm install sample", "dpkg -i x.deb",
     "pip install sample"],
)
def test_every_package_install_is_also_a_download(
    host: str, command: str, tmp_path: Path
) -> None:
    # C03: ADR 0005 counts any package install as a download.
    state = _outgoing_state(host, command, tmp_path)
    assert state[STATE_PREFIX + "package_install"] is True
    assert state[STATE_PREFIX + "network_download"] is True
    assert state[CONFIDENT_KEY] is False  # install scripts are unseen code


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "command",
    ["/tmp/evil", "tools/evil", "~/bin/evil", "C:\\tmp\\evil.exe", "evil", "exec evil",
     "Import-Module .\\x.psm1", "git commit -m wip", "git checkout main", "git merge x",
     "git config core.hooksPath /tmp/h", "PATH=/tmp ls", "LD_PRELOAD=/tmp/x.so ls",
     "env LD_PRELOAD=/tmp/x.so ls", "GIT_EXTERNAL_DIFF=/tmp/x git diff",
     "GIT_PAGER=/tmp/x git log", "git grep -O x", "git diff --ext-diff",
     "git show --textconv HEAD", "git status", "git log", "date -s 2020-01-01",
     "cp /tmp/evil .git/hooks/pre-commit", "ln -s /tmp/evil .git/hooks/pre-commit",
     "cp /tmp/x .claude/settings.json", "cp /tmp/x Makefile", "cp /tmp/x .envrc",
     "cp -t.git/hooks x", "mv -t.claude x", "copy-item x -destination:Makefile",
     "move-item -Path:.envrc x", "cp -r /tmp/payload/. .", "cp x GIT~1/hooks/a",
     "cp x Makefile."],
)
def test_unmodeled_commands_are_sent_as_unknown(host: str, command: str, tmp_path: Path) -> None:
    # Round-2 C01: confident is an allowlist of fully modeled commands.
    state = _outgoing_state(host, command, tmp_path)
    assert state[CONFIDENT_KEY] is False
    for name in PROPERTY_NAMES:
        assert state[STATE_PREFIX + name] is not False
    assert _run_host(host, command, Mode.ENFORCE, _all_accept) is HookOutcome.ASK


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    ("command", "field"),
    [("rm -rf /mnt/c/Users/x", "targets_home_directory"),
     ("rm -rf /c/Users/x", "targets_home_directory"),
     ("rm -rf /cygdrive/c/Users/x", "targets_home_directory"),
     ("rm -rf /mnt/c/Windows", "targets_root_or_system_path"),
     ("hg clone http://x", "network_download"),
     ("svn checkout http://x", "network_download"),
     ("cp ~/.ssh/id_rsa ./leak", "targets_home_directory"),
     ("cat ~/.aws/credentials", "targets_home_directory")],
)
def test_wsl_paths_and_other_vcs_in_outgoing_state(
    host: str, command: str, field: str, tmp_path: Path
) -> None:
    # Round-2 C02 and C04.
    state = _outgoing_state(host, command, tmp_path)
    assert state[STATE_PREFIX + field] is True


@pytest.mark.parametrize("host", HOSTS)
def test_plain_command_control_stays_confident_and_false(host: str, tmp_path: Path) -> None:
    state = _outgoing_state(host, "ls -la", tmp_path)
    assert state[CONFIDENT_KEY] is True
    assert all(state[STATE_PREFIX + n] is False for n in PROPERTY_NAMES)
