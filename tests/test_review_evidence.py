"""Synthetic offline counterexamples; no actual finding is closed by these fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import traceback
from pathlib import Path

import pytest

from jev_kit.evidence_format import (
    MAX_BYTES,
    EvidenceError,
    bounded_number,
    digest,
    flag,
    integer,
    parse,
    read_bytes,
    record,
)
from jev_kit.review_evidence import check, main

SHA = "a" * 40


def fixture(root: Path) -> tuple[bytes, dict[str, object]]:
    artifact = b"synthetic probe observed expected result\n"
    (root / "probe.txt").write_bytes(artifact)
    ref = {"path": "probe.txt", "sha256": hashlib.sha256(artifact).hexdigest()}
    contract = json.dumps({
        "schema_version": 1, "author": "implementer",
        "obligations": [{"id": "F1.windows", "finding_id": "F1",
                         "requirement": "Synthetic requirement, not a safety proof",
                         "references": ["docs/specs/example.md:10"], "host": "windows",
                         "checks": [{"id": "test_counterexample", "kind": "regression"},
                                    {"id": "test_observable", "kind": "positive_control"}]}],
    }).encode()
    evidence: dict[str, object] = {
        "schema_version": 1, "contract_sha256": hashlib.sha256(contract).hexdigest(),
        "candidate_sha": SHA, "reviewer": "independent-reviewer",
        "environment_sha256": "b" * 64,
        "obligations": [{"id": "F1.windows", "host": "windows", "checks": [
            {"id": "test_counterexample", "status": "pass", "artifact": ref},
            {"id": "test_observable", "status": "pass", "artifact": ref}]}],
        "gates": {name: {"status": "pass", "exit_code": 0, "artifact": ref}
                  for name in ("pytest", "ruff", "mypy")},
    }
    return contract, evidence


def run(root: Path, contract: bytes, evidence: object) -> dict[str, object]:
    return check(contract, evidence, candidate_sha=SHA, reviewer="independent-reviewer",
                 artifact_root=root)


def rewrite(value: object, before: str, after: str) -> object:
    """Mutate a typed JSON fixture without an Any escape hatch."""
    return parse(json.dumps(value).replace(before, after).encode())


def test_complete_record_is_not_authority(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    result = run(tmp_path, contract, evidence)
    assert result == {"verdict": "record_consistent", "candidate_sha": SHA, "findings": 1,
                      "obligations": 1, "failed_checks": 0, "authorizes_action": False,
                      "independent_review_required": True}


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("candidate_sha", "c" * 40),
    ("reviewer", "implementer"), ("contract_sha256", "c" * 64),
    ("environment_sha256", "not-a-digest"), ("obligations", []), ("gates", {}),
])
def test_fail_closed_for_stale_or_incomplete_records(
    tmp_path: Path, field: str, value: object,
) -> None:
    contract, evidence = fixture(tmp_path)
    evidence[field] = value
    with pytest.raises(EvidenceError):
        run(tmp_path, contract, evidence)


@pytest.mark.parametrize("status", ["fail", "skip", "not_run"])
def test_unsuccessful_evidence_never_passes(tmp_path: Path, status: str) -> None:
    contract, evidence = fixture(tmp_path)
    result = run(tmp_path, contract, rewrite(evidence, '"pass"', json.dumps(status)))
    assert result["verdict"] == "revise"
    assert result["failed_checks"] == 5


@pytest.mark.parametrize("before,after", [
    ('"windows"', '"linux"'), ('"test_observable"', '"test_counterexample"'),
    ('"pass"', '"claimed_fixed"'), ('"exit_code": 0', '"exit_code": true'),
])
def test_invalid_checks_and_hosts(tmp_path: Path, before: str, after: str) -> None:
    contract, evidence = fixture(tmp_path)
    with pytest.raises(EvidenceError):
        run(tmp_path, contract, rewrite(evidence, before, after))


def test_changed_artifact_invalidates_record(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    (tmp_path / "probe.txt").write_text("edited after review")
    with pytest.raises(EvidenceError, match="artifact_digest"):
        run(tmp_path, contract, evidence)


@pytest.mark.parametrize("name", ["../probe.txt", "C:/probe.txt", "/probe.txt",
                                 "probe.txt:stream", "folder\\probe.txt", "./probe.txt"])
def test_artifact_paths_are_confined(tmp_path: Path, name: str) -> None:
    contract, evidence = fixture(tmp_path)
    with pytest.raises(EvidenceError):
        run(tmp_path, contract, rewrite(evidence, '"probe.txt"', json.dumps(name)))


@pytest.mark.parametrize("control", ["\x00", "\x08", "\t", "\n", "\r", "\x7f", "\x85", "\u202e"])
def test_library_rejects_nonprintable_artifact_paths(tmp_path: Path, control: str) -> None:
    contract, evidence = fixture(tmp_path)
    assert run(tmp_path, contract, evidence)["verdict"] == "record_consistent"
    changed = rewrite(evidence, '"probe.txt"', json.dumps(f"PRIVATE_SENTINEL{control}.txt"))
    with pytest.raises(EvidenceError, match=r"^artifact_path$") as caught:
        run(tmp_path, contract, changed)
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("missing_root", [False, True])
def test_library_missing_artifacts_have_safe_errors(tmp_path: Path, missing_root: bool) -> None:
    contract, evidence = fixture(tmp_path)
    assert run(tmp_path, contract, evidence)["verdict"] == "record_consistent"
    root = tmp_path / "PRIVATE_SENTINEL" if missing_root else tmp_path
    changed = evidence if missing_root else rewrite(evidence, '"probe.txt"', '"PRIVATE_SENTINEL"')
    with pytest.raises(EvidenceError, match=r"^artifact_io$") as caught:
        run(root, contract, changed)
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("stage", ["resolve", "read"])
@pytest.mark.parametrize("failure", [PermissionError, OSError, ValueError, RuntimeError])
def test_library_sanitizes_artifact_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, failure: type[Exception],
) -> None:
    contract, evidence = fixture(tmp_path)
    assert run(tmp_path, contract, evidence)["verdict"] == "record_consistent"
    reached = 0

    def broken(*args: object, **kwargs: object) -> bytes:
        nonlocal reached
        reached += 1
        raise failure("PRIVATE_SENTINEL")

    with monkeypatch.context() as patch:
        if stage == "resolve":
            patch.setattr(Path, "resolve", broken)
        else:
            patch.setattr("jev_kit.review_evidence.read_bytes", broken)
        with pytest.raises(EvidenceError, match=r"^artifact_io$") as caught:
            run(tmp_path, contract, evidence)
    assert reached == 1
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("name", ["PRIVATE_SENTINEL\x08.txt", "PRIVATE_SENTINEL_missing.txt"])
def test_cli_artifact_failures_stay_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str,
) -> None:
    contract, evidence = fixture(tmp_path)
    changed = rewrite(evidence, '"probe.txt"', json.dumps(name))
    contract_path, evidence_path = tmp_path / "contract.json", tmp_path / "evidence.json"
    contract_path.write_bytes(contract)
    evidence_path.write_text(json.dumps(changed), encoding="utf-8")
    assert main(["check", str(contract_path), str(evidence_path), "--candidate", SHA,
                 "--reviewer", "independent-reviewer", "--artifacts", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == '{"verdict":"invalid_record","authorizes_action":false}\n'


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    contract, evidence = fixture(root)
    (tmp_path / "outside.txt").write_text("private")
    try:
        (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    except OSError:
        pytest.skip("This platform has no symlink privilege")
    with pytest.raises(EvidenceError, match="artifact_outside_root"):
        run(root, contract, rewrite(evidence, '"probe.txt"', '"link.txt"'))


def test_missing_control_and_self_review(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    missing = contract.replace(b"positive_control", b"regression")
    with pytest.raises(EvidenceError, match="missing_control"):
        run(tmp_path, missing, evidence)
    with pytest.raises(EvidenceError, match="self_review"):
        check(contract, evidence, candidate_sha=SHA, reviewer="implementer", artifact_root=tmp_path)


def test_unknown_and_duplicate_fields_rejected(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    evidence["closed"] = True
    with pytest.raises(EvidenceError):
        run(tmp_path, contract, evidence)
    with pytest.raises(EvidenceError):
        parse(b'{"x":1,"x":2}')
    with pytest.raises(EvidenceError):
        record({1: "value"}, {"1"})


@pytest.mark.parametrize("raw", [b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b'{'])
def test_strict_json(raw: bytes) -> None:
    with pytest.raises(EvidenceError):
        parse(raw)


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), -1, 10**400])
def test_finite_numbers(value: object) -> None:
    with pytest.raises(EvidenceError):
        bounded_number(value)


def test_strict_primitives() -> None:
    assert flag(False) is False
    assert integer(0) == 0
    assert bounded_number(0.5) == 0.5
    assert digest("a" * 40, 40) == "a" * 40
    with pytest.raises(EvidenceError):
        flag(1)
    with pytest.raises(EvidenceError):
        integer(True)


def test_cli_positive_control_and_private_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    contract, evidence = fixture(tmp_path)
    contract_path = tmp_path / "contract.json"
    evidence_path = tmp_path / "evidence.json"
    contract_path.write_bytes(contract)
    evidence_path.write_text(json.dumps(evidence))
    args = ["check", str(contract_path), str(evidence_path), "--candidate", SHA,
            "--reviewer", "independent-reviewer", "--artifacts", str(tmp_path)]
    assert main(args) == 0
    assert '"authorizes_action": false' in capsys.readouterr().out
    evidence_path.write_text('{"PRIVATE_SECRET":1,"PRIVATE_SECRET":2}')
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == '{"verdict":"invalid_record","authorizes_action":false}\n'


def test_nonzero_gate_exit_requires_revision(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    result = run(tmp_path, contract, rewrite(evidence, '"exit_code": 0', '"exit_code": 1'))
    assert result["verdict"] == "revise"


@pytest.mark.parametrize("status", ["pass", "fail", "skip", "not_run"])
@pytest.mark.parametrize("exit_code", [True, "0", None, -1, 0.0])
def test_gate_status_cannot_short_circuit_exit_validation(
    tmp_path: Path, status: str, exit_code: object,
) -> None:
    contract, evidence = fixture(tmp_path)
    changed = rewrite(evidence, '"pass"', json.dumps(status))
    changed = rewrite(changed, '"exit_code": 0', '"exit_code": ' + json.dumps(exit_code))
    with pytest.raises(EvidenceError, match="invalid_integer"):
        run(tmp_path, contract, changed)


def test_file_limits_and_nonregular_input(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="not_regular_file"):
        read_bytes(tmp_path)
    with pytest.raises(EvidenceError, match="file_too_large"):
        parse(b" " * (MAX_BYTES + 1))
    large = tmp_path / "large.json"
    large.write_bytes(b" " * (MAX_BYTES + 1))
    with pytest.raises(EvidenceError, match="file_too_large"):
        read_bytes(large)


def test_replaced_file_descriptor_is_checked_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "initially-regular.txt"
    path.write_bytes(b"control")
    assert read_bytes(path) == b"control"
    reader, writer = os.pipe()

    def replaced_open(requested: Path, flags: int) -> int:
        assert requested == path
        nonblocking = getattr(os, "O_NONBLOCK", 0)
        assert flags & nonblocking == nonblocking
        return reader

    try:
        monkeypatch.setattr(os, "open", replaced_open)
        # The writer remains open: reading this empty pipe would wait indefinitely.
        with pytest.raises(EvidenceError, match="not_regular_file"):
            read_bytes(path)
        with pytest.raises(OSError):
            os.fstat(reader)  # The rejected descriptor was closed by the context manager.
    finally:
        os.close(writer)


def test_missing_artifact_errors_do_not_echo_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["check", str(tmp_path / "PRIVATE_SECRET.json"), "unused.json",
                   "--candidate", SHA, "--reviewer", "reviewer", "--artifacts", str(tmp_path)])
    assert result == 2
    captured = capsys.readouterr()
    assert "PRIVATE_SECRET" not in captured.out + captured.err
    assert "invalid_record" in captured.err


def test_invalid_arguments_do_not_echo_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["PRIVATE_SECRET"]) == 2
    assert "PRIVATE_SECRET" not in capsys.readouterr().err


def test_omitted_check_cannot_vouch(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    obligations = evidence["obligations"]
    assert isinstance(obligations, list)
    assert isinstance(obligations[0], dict)
    checks = obligations[0]["checks"]
    assert isinstance(checks, list)
    checks.pop()
    with pytest.raises(EvidenceError, match="missing_check"):
        run(tmp_path, contract, evidence)


def test_duplicate_obligation_cannot_replace_coverage(tmp_path: Path) -> None:
    contract, evidence = fixture(tmp_path)
    obligations = evidence["obligations"]
    assert isinstance(obligations, list)
    obligations.append(obligations[0])
    with pytest.raises(EvidenceError, match="obligation_mismatch"):
        run(tmp_path, contract, evidence)


def test_compare_cli_binds_frozen_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "evidence-comparison.md"
    sample = doc.read_text(encoding="utf-8").split("```json\n", 1)[1].split("```", 1)[0]
    manifest = record(parse(sample.encode()), {"schema_version", "contract", "parent", "candidate"})
    frozen = json.dumps(manifest["contract"], sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False).encode("ascii")
    expected = hashlib.sha256(frozen).hexdigest()
    path = tmp_path / "paired.json"
    path.write_text(sample)
    args = ["compare", str(path), "--parent", "a" * 40, "--candidate", "b" * 40,
            "--contract-sha256", expected]
    assert main(args) == 0
    output = capsys.readouterr()
    assert '"verdict": "offline_candidate"' in output.out
    assert '"authorizes_action": false' in output.out
    assert '"host"' not in output.out
    path.write_text(sample.replace('"minimum_correctness_improvement": 0.1',
                                   '"minimum_correctness_improvement": 0.01'))
    assert main(args) == 2
    assert "invalid_record" in capsys.readouterr().err
    path.write_text(sample.replace('"correct": true', '"correct": false'))
    assert main(args) == 1
    assert '"verdict": "revise"' in capsys.readouterr().out
