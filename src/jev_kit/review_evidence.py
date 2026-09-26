"""Check evidence completeness against an independently frozen review contract.

This checks supplied records and local artifact bytes. It cannot authenticate a reviewer,
prove a test ran, or establish that a contract covers every defect. Never use it as a gate
authorization, automatic issue closer, or substitute for independent review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import NoReturn

from jev_kit.evidence_format import (
    EvidenceError,
    digest,
    integer,
    items,
    parse,
    read_bytes,
    record,
    token,
)


def _artifact(value: object, root: Path) -> None:
    obj = record(value, {"path", "sha256"})
    name = obj["path"]
    if not isinstance(name, str) or not name or len(name) > 256:
        raise EvidenceError("artifact_path")
    path = PurePosixPath(name)
    # Portable paths only: reject Windows drives, streams, separators and traversal.
    if path.is_absolute() or any(p in (".", "..") for p in name.split("/")):
        raise EvidenceError("artifact_path")
    if any(char in name for char in "\\:\x00"):
        raise EvidenceError("artifact_path")
    resolved_root = root.resolve(strict=True)
    target = (resolved_root / path).resolve(strict=True)
    if not target.is_relative_to(resolved_root):
        raise EvidenceError("artifact_outside_root")
    if hashlib.sha256(read_bytes(target)).hexdigest() != digest(obj["sha256"]):
        raise EvidenceError("artifact_digest")


def _version(value: object) -> None:
    if integer(value) != 1:
        raise EvidenceError("unsupported_version")


def check(
    contract_raw: bytes,
    evidence: object,
    *,
    candidate_sha: str,
    reviewer: str,
    artifact_root: Path,
) -> dict[str, object]:
    """Validate all obligations; expected SHA and reviewer are caller-owned inputs."""
    digest(candidate_sha, 40)
    token(reviewer)
    contract = record(parse(contract_raw), {"schema_version", "author", "obligations"})
    _version(contract["schema_version"])
    if token(contract["author"]) == reviewer:
        raise EvidenceError("self_review")
    expected: dict[str, tuple[str, set[str]]] = {}
    findings: set[str] = set()
    for entry in items(contract["obligations"]):
        obligation = record(entry, {"id", "finding_id", "requirement", "references", "host",
                                    "checks"})
        oid = token(obligation["id"])
        if oid in expected:
            raise EvidenceError("duplicate_obligation")
        findings.add(token(obligation["finding_id"]))
        requirement = obligation["requirement"]
        if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 2048:
            raise EvidenceError("invalid_requirement")
        references = items(obligation["references"])
        if not references:
            raise EvidenceError("missing_reference")
        for reference in references:
            token(reference)
        checks: set[str] = set()
        kinds: set[str] = set()
        for raw_check in items(obligation["checks"]):
            probe = record(raw_check, {"id", "kind"})
            cid = token(probe["id"])
            kind = token(probe["kind"])
            if cid in checks or kind not in {"regression", "positive_control"}:
                raise EvidenceError("invalid_check")
            checks.add(cid)
            kinds.add(kind)
        if kinds != {"regression", "positive_control"}:
            raise EvidenceError("missing_control")
        expected[oid] = (token(obligation["host"]), checks)
    if not expected:
        raise EvidenceError("empty_contract")

    run = record(evidence, {"schema_version", "contract_sha256", "candidate_sha", "reviewer",
                            "environment_sha256", "obligations", "gates"})
    _version(run["schema_version"])
    if digest(run["contract_sha256"]) != hashlib.sha256(contract_raw).hexdigest():
        raise EvidenceError("contract_digest")
    if digest(run["candidate_sha"], 40) != candidate_sha:
        raise EvidenceError("stale_candidate")
    if token(run["reviewer"]) != reviewer:
        raise EvidenceError("reviewer_mismatch")
    digest(run["environment_sha256"])
    seen: set[str] = set()
    failures = 0
    for entry in items(run["obligations"]):
        result = record(entry, {"id", "host", "checks"})
        oid = token(result["id"])
        if oid not in expected or oid in seen:
            raise EvidenceError("obligation_mismatch")
        seen.add(oid)
        host, required_checks = expected[oid]
        if token(result["host"]) != host:
            raise EvidenceError("host_mismatch")
        observed: set[str] = set()
        for raw_check in items(result["checks"]):
            probe = record(raw_check, {"id", "status", "artifact"})
            cid = token(probe["id"])
            if cid not in required_checks or cid in observed:
                raise EvidenceError("check_mismatch")
            observed.add(cid)
            status = token(probe["status"])
            if status not in {"pass", "fail", "skip", "not_run"}:
                raise EvidenceError("invalid_status")
            _artifact(probe["artifact"], artifact_root)
            failures += status != "pass"
        if observed != required_checks:
            raise EvidenceError("missing_check")
    if seen != set(expected):
        raise EvidenceError("missing_obligation")
    gates = record(run["gates"], {"pytest", "ruff", "mypy"})
    for value in gates.values():
        gate = record(value, {"status", "exit_code", "artifact"})
        status = token(gate["status"])
        if status not in {"pass", "fail", "skip", "not_run"}:
            raise EvidenceError("invalid_status")
        exit_code = integer(gate["exit_code"])
        failures += status != "pass" or exit_code != 0
        _artifact(gate["artifact"], artifact_root)
    return {"verdict": "record_consistent" if failures == 0 else "revise",
            "candidate_sha": candidate_sha, "findings": len(findings),
            "obligations": len(expected), "failed_checks": failures,
            "independent_review_required": True, "authorizes_action": False}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise EvidenceError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("check")
    verify.add_argument("contract", type=Path)
    verify.add_argument("evidence", type=Path)
    verify.add_argument("--candidate", required=True)
    verify.add_argument("--reviewer", required=True)
    verify.add_argument("--artifacts", required=True, type=Path)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("record", type=Path)
    compare_parser.add_argument("--parent", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--contract-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "check":
            result = check(read_bytes(args.contract), parse(read_bytes(args.evidence)),
                           candidate_sha=args.candidate, reviewer=args.reviewer,
                           artifact_root=args.artifacts)
        else:
            from jev_kit.evidence_compare import compare

            result = compare(parse(read_bytes(args.record)), args.parent, args.candidate,
                             expected_contract_sha256=args.contract_sha256)
    except (EvidenceError, OSError, ValueError, RecursionError):
        # Never echo untrusted data, filenames, credentials, or exception messages.
        print('{"verdict":"invalid_record","authorizes_action":false}', file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0 if result["verdict"] in {"record_consistent", "offline_candidate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
