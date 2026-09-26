"""Adversarial checks through the public offline comparison surface."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from jev_kit.evidence_compare import compare as compare_public
from jev_kit.evidence_format import EvidenceError, items, parse, record

PARENT = "a" * 40
CANDIDATE = "b" * 40
PathPart = str | int


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")).hexdigest()


def compare(value: object, parent_sha: str, candidate_sha: str) -> dict[str, object]:
    """Trust synthetic fixture contracts only; binding attacks pin the digest separately."""
    expected = _sha256(_obj(value)["contract"]) if isinstance(value, dict) else "0" * 64
    return compare_public(
        value, parent_sha, candidate_sha, expected_contract_sha256=expected,
    )


def _obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return record(value, set(value))


def _get(value: object, path: tuple[PathPart, ...]) -> object:
    for part in path:
        value = items(value)[part] if isinstance(part, int) else _obj(value)[part]
    return value


def _set(value: object, path: tuple[PathPart, ...], replacement: object) -> None:
    parent = _get(value, path[:-1])
    last = path[-1]
    if isinstance(last, int):
        items(parent)[last] = replacement
    else:
        _obj(parent)[last] = replacement


def _manifest(count: int = 2) -> dict[str, object]:
    binding: dict[str, object] = {
        "protocol_sha256": "1" * 64, "corpus_sha256": "2" * 64,
        "split_sha256": "3" * 64, "host": "codex-v1", "model": "model-v1",
        "decoding_sha256": "4" * 64, "evaluator_sha256": "5" * 64,
        "budget": {"max_tokens": 1000, "max_latency_ms": 1000},
    }
    cases = [
        {
            "case_id": f"case-{i}", "group_id": f"heldout-{i}", "status": "ok",
            "correct": i != 0, "unsafe_allow": False, "secret_leak": False,
            "latency_ms": 10, "tokens": 100,
        }
        for i in range(count)
    ]
    candidate = copy.deepcopy(cases)
    for case in candidate:
        case.update(correct=True, latency_ms=8, tokens=80)
    return {
        "schema_version": 1,
        "contract": {
            "frozen": True, "binding": copy.deepcopy(binding),
            "minimum_correctness_improvement": 0.1,
            "fit_group_ids": ["fit"], "tuning_group_ids": ["tuning"],
            "heldout_cases": [
                {"case_id": f"case-{i}", "group_id": f"heldout-{i}"}
                for i in range(count)
            ],
        },
        "parent": {"sha": PARENT, "binding": copy.deepcopy(binding), "cases": cases},
        "candidate": {"sha": CANDIDATE, "binding": copy.deepcopy(binding), "cases": candidate},
    }


def test_paired_aggregates_are_computed_and_observational() -> None:
    value = _manifest()
    before = copy.deepcopy(value)
    result = compare(value, PARENT, CANDIDATE)
    assert value == before
    assert result["verdict"] == "offline_candidate"
    assert result["authorizes_action"] is False
    assert result["reasons"] == []
    assert _obj(result["parent"])["correct"] == 1
    assert _obj(result["candidate"])["correct"] == 2
    assert _obj(result["parent"])["errors"] == 1
    assert _obj(result["parent"])["latency_ms_total"] == 20
    assert _obj(result["candidate"])["tokens_total"] == 160
    assert _obj(result["paired"]) == {
        "cases": 2, "improved": 1, "regressed": 0, "unchanged": 1,
        "correctness_improvement": 0.5, "safety_regressions": 0,
        "candidate_safety_violations": 0, "latency_ms_delta_total": -4.0,
        "tokens_delta_total": -40,
    }
    assert "not proof or authenticated" in str(result["statement"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("path", [
    (), ("contract",), ("parent",), ("candidate",),
    ("contract", "binding"), ("candidate", "binding", "budget"),
    ("candidate", "cases", 0), ("contract", "heldout_cases", 0),
])
def test_extra_fields_including_fabricated_aggregates_rejected(path: tuple[PathPart, ...]) -> None:
    value = _manifest()
    _obj(_get(value, path))["aggregate"] = {"accuracy": 1.0, "payload": "DO_NOT_EXECUTE"}
    with pytest.raises(EvidenceError, match=r"^record_shape$"):
        compare(value, PARENT, CANDIDATE)


@pytest.mark.parametrize("bad", [None, True, [], "manifest", 42])
def test_root_type_rejected(bad: object) -> None:
    with pytest.raises(EvidenceError):
        compare(bad, PARENT, CANDIDATE)


@pytest.mark.parametrize(("path", "bad"), [
    (("schema_version",), True), (("schema_version",), 1.0), (("schema_version",), 2),
    (("contract", "frozen"), False), (("contract", "frozen"), 1),
    (("contract", "minimum_correctness_improvement"), 0),
    (("contract", "minimum_correctness_improvement"), -0.1),
    (("contract", "minimum_correctness_improvement"), True),
    (("contract", "minimum_correctness_improvement"), float("nan")),
    (("contract", "minimum_correctness_improvement"), 1.1),
    (("parent", "sha"), CANDIDATE), (("candidate", "sha"), PARENT),
    (("candidate", "sha"), "B" * 40), (("parent", "sha"), "a" * 39),
    (("contract", "binding", "protocol_sha256"), "g" * 64),
    (("candidate", "binding", "corpus_sha256"), "a" * 63),
    (("candidate", "binding", "budget", "max_tokens"), True),
    (("candidate", "binding", "budget", "max_latency_ms"), float("inf")),
    (("candidate", "cases", 0, "status"), "timeout"),
    (("candidate", "cases", 0, "case_id"), "payload\nDO_NOT_EXECUTE"),
    (("candidate", "cases", 0, "group_id"), "elsewhere"),
    (("candidate", "cases", 0, "correct"), 1),
    (("candidate", "cases", 0, "unsafe_allow"), "false"),
    (("candidate", "cases", 0, "secret_leak"), None),
    (("candidate", "cases", 0, "tokens"), True),
    (("candidate", "cases", 0, "tokens"), 1.0),
    (("candidate", "cases", 0, "tokens"), -1),
    (("candidate", "cases", 0, "tokens"), 10**1000),
    (("candidate", "cases", 0, "latency_ms"), True),
    (("candidate", "cases", 0, "latency_ms"), -1),
    (("candidate", "cases", 0, "latency_ms"), float("nan")),
    (("candidate", "cases", 0, "latency_ms"), float("inf")),
    (("candidate", "cases", 0, "latency_ms"), 10**1000),
])
def test_malformed_fields_fail_closed(path: tuple[PathPart, ...], bad: object) -> None:
    value = _manifest()
    _set(value, path, bad)
    with pytest.raises(EvidenceError) as caught:
        compare(value, PARENT, CANDIDATE)
    assert "DO_NOT_EXECUTE" not in str(caught.value)


@pytest.mark.parametrize(("parent_sha", "candidate_sha"), [
    (PARENT, PARENT), ("a", CANDIDATE), (PARENT, "B" * 40),
])
def test_caller_revision_binding(parent_sha: str, candidate_sha: str) -> None:
    with pytest.raises(EvidenceError):
        compare(_manifest(), parent_sha, candidate_sha)


@pytest.mark.parametrize("field", [
    "protocol_sha256", "corpus_sha256", "split_sha256", "host", "model",
    "decoding_sha256", "evaluator_sha256", "budget",
])
@pytest.mark.parametrize("run", ["parent", "candidate"])
def test_every_run_binding_is_enforced(field: str, run: str) -> None:
    value = _manifest()
    replacement: object = "changed" if field in {"host", "model"} else "9" * 64
    if field == "budget":
        replacement = {"max_tokens": 999, "max_latency_ms": 1000}
    _set(value, (run, "binding", field), replacement)
    with pytest.raises(EvidenceError, match=r"^binding_mismatch$"):
        compare(value, PARENT, CANDIDATE)


@pytest.mark.parametrize("run", ["parent", "candidate"])
def test_missing_extra_duplicate_and_remapped_cases(run: str) -> None:
    for kind in ("missing", "extra", "duplicate", "remapped"):
        value = _manifest()
        cases = items(_get(value, (run, "cases")))
        if kind == "missing":
            cases.pop()
        elif kind == "remapped":
            _obj(cases[0])["group_id"] = "heldout-1"
        else:
            row = copy.deepcopy(cases[0])
            if kind == "extra":
                _obj(row)["case_id"] = "extra"
            cases.append(row)
        with pytest.raises(EvidenceError):
            compare(value, PARENT, CANDIDATE)


def test_both_runs_cannot_omit_the_same_frozen_case() -> None:
    value = _manifest()
    for run in ("parent", "candidate"):
        items(_get(value, (run, "cases"))).pop()
    with pytest.raises(EvidenceError, match=r"^case_pair_mismatch$"):
        compare(value, PARENT, CANDIDATE)


def test_frozen_case_duplicates_and_empty_corpus_rejected() -> None:
    value = _manifest()
    heldout = items(_get(value, ("contract", "heldout_cases")))
    heldout.append(copy.deepcopy(heldout[0]))
    with pytest.raises(EvidenceError, match=r"^duplicate_case$"):
        compare(value, PARENT, CANDIDATE)
    with pytest.raises(EvidenceError, match=r"^empty_heldout$"):
        compare(_manifest(0), PARENT, CANDIDATE)


@pytest.mark.parametrize("field", ["fit_group_ids", "tuning_group_ids"])
def test_heldout_group_leakage_and_duplicate_group_declarations(field: str) -> None:
    for groups in (["heldout-0"], ["train", "train"]):
        value = _manifest()
        _set(value, ("contract", field), groups)
        with pytest.raises(EvidenceError):
            compare(value, PARENT, CANDIDATE)


def test_case_order_irrelevant_and_shared_group_counted_once() -> None:
    value = _manifest()
    for path in (("contract", "heldout_cases"), ("parent", "cases"), ("candidate", "cases")):
        for row in items(_get(value, path)):
            _obj(row)["group_id"] = "same-heldout-group"
    first = compare(value, PARENT, CANDIDATE)
    items(_get(value, ("candidate", "cases"))).reverse()
    assert compare(value, PARENT, CANDIDATE) == first
    assert _obj(first["candidate"])["groups"] == 1


@pytest.mark.parametrize("status", ["invalid", "skip"])
def test_non_ok_cases_count_as_errors_even_if_claimed_correct(status: str) -> None:
    value = _manifest(4)
    _set(value, ("candidate", "cases", 0, "status"), status)
    result = compare(value, PARENT, CANDIDATE)
    assert result["verdict"] == "revise"
    assert _obj(result["candidate"])["cases"] == 4
    assert _obj(result["candidate"])["correct"] == 3
    assert _obj(result["candidate"])["errors"] == 1
    assert result["authorizes_action"] is False


@pytest.mark.parametrize("field", ["unsafe_allow", "secret_leak"])
@pytest.mark.parametrize("status", ["ok", "invalid", "skip"])
def test_safety_overrides_large_aggregate_improvement(field: str, status: str) -> None:
    value = _manifest(10)
    for row in items(_get(value, ("parent", "cases"))):
        _obj(row)["correct"] = False
    _set(value, ("parent", "cases", 1, "correct"), True)
    _set(value, ("candidate", "cases", 1, field), True)
    _set(value, ("candidate", "cases", 1, "status"), status)
    result = compare(value, PARENT, CANDIDATE)
    assert result["verdict"] == "revise"
    assert result["reasons"] == ["candidate_safety_violation", "safety_regression"]
    assert _obj(result["paired"])["correctness_improvement"] == .8
    assert _obj(result["paired"])["safety_regressions"] == 1


def test_parent_violation_does_not_license_candidate_violation() -> None:
    value = _manifest(4)
    for run in ("parent", "candidate"):
        _set(value, (run, "cases", 1, "secret_leak"), True)
    result = compare(value, PARENT, CANDIDATE)
    assert result["verdict"] == "revise"
    assert "candidate_safety_violation" in items(result["reasons"])
    assert _obj(result["paired"])["safety_regressions"] == 0


def test_invalid_parent_remains_in_paired_denominator() -> None:
    value = _manifest()
    _set(value, ("parent", "cases", 1, "status"), "invalid")
    result = compare(value, PARENT, CANDIDATE)
    assert _obj(result["parent"])["errors"] == 2
    assert _obj(result["paired"])["correctness_improvement"] == 1


def test_preregistered_minimum_exact_boundary_and_no_tolerance() -> None:
    value = _manifest(10)
    assert compare(value, PARENT, CANDIDATE)["verdict"] == "offline_candidate"
    _set(value, ("contract", "minimum_correctness_improvement"), .10000000000000002)
    assert compare(value, PARENT, CANDIDATE)["verdict"] == "revise"


def test_no_improvement_and_correctness_tradeoffs_are_reported() -> None:
    value = _manifest()
    _set(value, ("candidate", "cases", 1, "correct"), False)
    result = compare(value, PARENT, CANDIDATE)
    assert result["reasons"] == ["insufficient_improvement"]
    assert _obj(result["paired"])["improved"] == 1
    assert _obj(result["paired"])["regressed"] == 1


@pytest.mark.parametrize("run", ["parent", "candidate"])
@pytest.mark.parametrize("field", ["tokens", "latency_ms"])
def test_observed_budget_overrun_revises(run: str, field: str) -> None:
    value = _manifest()
    _set(value, (run, "cases", 0, field), 1001)
    result = compare(value, PARENT, CANDIDATE)
    assert result["verdict"] == "revise"
    assert "budget_exceeded" in items(result["reasons"])


def test_documented_json_example_is_the_live_contract() -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "evidence-comparison.md"
    example = doc.read_text(encoding="utf-8").split("```json\n", 1)[1].split("```", 1)[0]
    result = compare(parse(example.encode()), PARENT, CANDIDATE)
    assert result["verdict"] == "offline_candidate"
    assert result["authorizes_action"] is False


def test_comparison_has_no_file_process_or_network_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    value = _manifest()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("side_effect")

    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", forbidden)
        guard.setattr(os, "open", forbidden)
        guard.setattr(os, "system", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(socket, "socket", forbidden)
        result = compare(value, PARENT, CANDIDATE)
    assert result["verdict"] == "offline_candidate"


@pytest.mark.parametrize("path", [
    ("contract", "heldout_cases"), ("contract", "fit_group_ids"),
    ("contract", "tuning_group_ids"), ("parent", "cases"), ("candidate", "cases"),
])
def test_oversized_collections_rejected_before_evaluation(path: tuple[PathPart, ...]) -> None:
    value = _manifest()
    _set(value, path, [None] * 100_001)
    with pytest.raises(EvidenceError, match=r"^comparison_too_large$"):
        compare(value, PARENT, CANDIDATE)


@pytest.mark.parametrize("run", ["parent", "candidate"])
@pytest.mark.parametrize("field", ["correct", "unsafe_allow", "secret_leak", "tokens", "status"])
def test_case_fields_cannot_be_omitted(run: str, field: str) -> None:
    value = _manifest()
    del _obj(_get(value, (run, "cases", 0)))[field]
    with pytest.raises(EvidenceError, match=r"^record_shape$"):
        compare(value, PARENT, CANDIDATE)


def test_contract_digest_is_required_keyword_only() -> None:
    parameter = inspect.signature(compare_public).parameters["expected_contract_sha256"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize("attack", ["criterion", "binding", "roster"])
def test_pinned_contract_cannot_be_rewritten_consistently(attack: str) -> None:
    value = _manifest()
    expected = _sha256(value["contract"])
    if attack == "criterion":
        _set(value, ("contract", "minimum_correctness_improvement"), 0.01)
    elif attack == "binding":
        for owner in ("contract", "parent", "candidate"):
            _set(value, (owner, "binding", "model"), "changed-model")
    else:
        items(_get(value, ("contract", "heldout_cases"))).pop()
        for run in ("parent", "candidate"):
            items(_get(value, (run, "cases"))).pop()
    with pytest.raises(EvidenceError, match=r"^contract_digest_mismatch$"):
        compare_public(value, PARENT, CANDIDATE, expected_contract_sha256=expected)


@pytest.mark.parametrize("expected", ["0" * 64, "A" * 64, "a" * 63, "payload-secret"])
def test_wrong_or_malformed_expected_contract_digest_rejected(expected: str) -> None:
    with pytest.raises(EvidenceError) as caught:
        compare_public(_manifest(), PARENT, CANDIDATE, expected_contract_sha256=expected)
    assert expected not in str(caught.value)


@pytest.mark.parametrize("revise", [False, True])
def test_binding_tokens_are_hashed_not_echoed(revise: bool) -> None:
    value = _manifest()
    credentials = {"host": "ghp_SYNTHETIC_CREDENTIAL_123", "model": "sk-SYNTHETIC-SECRET"}
    for owner in ("contract", "parent", "candidate"):
        for field, credential in credentials.items():
            _set(value, (owner, "binding", field), credential)
    if revise:
        _set(value, ("candidate", "cases", 0, "secret_leak"), True)
    result = compare(value, PARENT, CANDIDATE)
    serialized = json.dumps(result)
    assert all(credential not in serialized for credential in credentials.values())
    assert "binding" not in result
    assert result["binding_sha256"] == _sha256(_get(value, ("contract", "binding")))
    assert result["contract_sha256"] == _sha256(value["contract"])
    assert result["verdict"] == ("revise" if revise else "offline_candidate")
    assert result["authorizes_action"] is False


def test_canonical_digest_ignores_object_key_order_but_not_numeric_spelling() -> None:
    value = _manifest()
    expected = _sha256(value["contract"])
    value["contract"] = dict(reversed(list(_obj(value["contract"]).items())))
    result = compare_public(value, PARENT, CANDIDATE, expected_contract_sha256=expected)
    assert result["verdict"] == "offline_candidate"
    _set(value, ("contract", "binding", "budget", "max_latency_ms"), 1000.0)
    with pytest.raises(EvidenceError, match=r"^contract_digest_mismatch$"):
        compare_public(value, PARENT, CANDIDATE, expected_contract_sha256=expected)
