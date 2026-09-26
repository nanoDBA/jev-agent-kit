"""Registry loader tests (spec stories 44 to 46, 71, 72, 79a).

Covers a valid registry with all three calibrated threshold shapes plus uncalibrated and
never-auto-accept entries, the missing-file contract, and every malformed-input rejection:
duplicate keys, non-finite numbers, bad fingerprints, unknown status, unsafe strings, a
calibrated entry missing its threshold, and overlapping score intervals surfaced through
the loader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jev_kit.errors import FailReason, ValidationError
from jev_kit.registry import load_registry, loads_registry, resolve_registry_path
from jev_kit.routing import ChoiceThreshold, NoulThreshold, ScoreThreshold, ThresholdStatus

FP_NOUL = "a" * 64
FP_CHOICE = "b" * 64
FP_SCORE = "c" * 64
FP_UNCAL = "d" * 64
FP_NEVER = "e" * 64


def _valid_registry() -> dict[str, object]:
    return {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev-2026-09-01",
                "date": "2026-09-01",
                "type": "noul",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
            },
            FP_CHOICE: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev-2026-09-02",
                "date": "2026-09-02",
                "type": "choice",
                "threshold": {"min_confidence": 0.7, "min_margin": 0.2},
            },
            FP_SCORE: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev-2026-09-03",
                "date": "2026-09-03",
                "type": "score",
                "threshold": {
                    "min_confidence": 0.6,
                    "intervals": [
                        {"lower": 0.0, "upper": 0.5, "label": "safe"},
                        {"lower": 1.5, "upper": 2.0, "label": "unsafe"},
                    ],
                },
            },
            FP_UNCAL: {
                "status": "uncalibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev-2026-09-04",
            },
            FP_NEVER: {
                "status": "never_auto_accept",
                "escalation_target": "human-review",
                "evidence_ref": "ev-2026-09-05",
            },
        },
    }


def test_valid_registry_all_shapes(tmp_path: Path) -> None:
    entries = loads_registry(json.dumps(_valid_registry()))
    assert set(entries.keys()) == {FP_NOUL, FP_CHOICE, FP_SCORE, FP_UNCAL, FP_NEVER}

    noul = entries[FP_NOUL]
    assert noul.status is ThresholdStatus.CALIBRATED
    assert isinstance(noul.threshold, NoulThreshold)
    assert noul.threshold.yes_bound == 0.9
    assert noul.threshold.no_bound == 0.1

    choice = entries[FP_CHOICE]
    assert isinstance(choice.threshold, ChoiceThreshold)
    assert choice.threshold.min_confidence == 0.7

    score = entries[FP_SCORE]
    assert isinstance(score.threshold, ScoreThreshold)
    assert len(score.threshold.intervals) == 2
    assert score.threshold.intervals[0].label == "safe"

    uncal = entries[FP_UNCAL]
    assert uncal.status is ThresholdStatus.UNCALIBRATED
    assert uncal.threshold is None

    never = entries[FP_NEVER]
    assert never.status is ThresholdStatus.NEVER_AUTO_ACCEPT
    assert never.threshold is None

    # load_registry reads the same content from a file.
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(_valid_registry()), encoding="utf-8")
    from_file = load_registry(path)
    assert set(from_file.keys()) == set(entries.keys())


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    assert load_registry(missing) == {}


def test_missing_file_by_string_path_returns_empty(tmp_path: Path) -> None:
    missing = str(tmp_path / "also_missing.json")
    assert load_registry(missing) == {}


def test_directory_path_is_config_failure(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_registry(tmp_path)
    assert exc_info.value.reason is FailReason.CONFIG


def test_duplicate_keys_rejected() -> None:
    text = f"""
    {{
        "schema_version": 1,
        "entries": {{
            "{FP_UNCAL}": {{"status": "uncalibrated", "escalation_target": "t"}},
            "{FP_UNCAL}": {{"status": "uncalibrated", "escalation_target": "t2"}}
        }}
    }}
    """
    # Also cover a duplicate key within one object.
    dup_within = f"""
    {{
        "schema_version": 1,
        "entries": {{
            "{FP_UNCAL}": {{
                "status": "uncalibrated",
                "escalation_target": "t",
                "escalation_target": "t2",
                "evidence_ref": "ev"
            }}
        }}
    }}
    """
    for bad in (text, dup_within):
        with pytest.raises(ValidationError) as exc_info:
            loads_registry(bad)
        assert exc_info.value.reason is FailReason.CONFIG


def test_non_finite_number_rejected() -> None:
    registry = _valid_registry()
    entries = registry["entries"]
    assert isinstance(entries, dict)
    # Inject a raw NaN token by hand-building JSON text (json.dumps cannot emit it safely).
    bad_text = json.dumps(registry).replace('"yes_bound": 0.9', '"yes_bound": NaN')
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(bad_text)
    assert exc_info.value.reason is FailReason.CONFIG


def test_bad_fingerprint_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            "not-a-fingerprint": {
                "status": "uncalibrated",
                "escalation_target": "t",
                "evidence_ref": "ev",
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unknown_status_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_UNCAL: {
                "status": "maybe_calibrated",
                "escalation_target": "t",
                "evidence_ref": "ev",
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unsafe_escalation_target_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_UNCAL: {
                "status": "uncalibrated",
                "escalation_target": "human; rm -rf /",
                "evidence_ref": "ev",
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unsafe_evidence_ref_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_UNCAL: {
                "status": "uncalibrated",
                "escalation_target": "human-review",
                "evidence_ref": "<script>",
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_calibrated_missing_threshold_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "type": "noul",
                # threshold omitted
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_calibrated_missing_type_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
                # type omitted
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_overlapping_score_intervals_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_SCORE: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "type": "score",
                "threshold": {
                    "min_confidence": 0.6,
                    "intervals": [
                        {"lower": 0.0, "upper": 1.2, "label": "a"},
                        {"lower": 1.0, "upper": 2.0, "label": "b"},
                    ],
                },
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unknown_schema_version_rejected() -> None:
    registry = {"schema_version": 2, "entries": {}}
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_schema_version_rejects_bool() -> None:
    registry = {"schema_version": True, "entries": {}}
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_not_json_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        loads_registry("not json at all")
    assert exc_info.value.reason is FailReason.CONFIG


def test_root_not_object_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        loads_registry("[]")
    assert exc_info.value.reason is FailReason.CONFIG


def test_bound_rejects_bool() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "type": "noul",
                "threshold": {"yes_bound": True, "no_bound": 0.1},
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unknown_top_level_field_rejected() -> None:
    registry = {"schema_version": 1, "entries": {}, "extra": "nope"}
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unknown_entry_field_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_UNCAL: {
                "status": "uncalibrated",
                "escalation_target": "t",
                "evidence_ref": "ev",
                "extra": "nope",
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_unknown_threshold_type_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "type": "not_a_type",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_calibrated_entry_without_date_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "type": "noul",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
                # date omitted
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_calibrated_entry_with_valid_date_accepted() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "date": "2026-09-25",
                "type": "noul",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
            }
        },
    }
    entries = loads_registry(json.dumps(registry))
    assert entries[FP_NOUL].status is ThresholdStatus.CALIBRATED


def test_calibrated_entry_bad_date_format_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NOUL: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "date": "09/25/2026",
                "type": "noul",
                "threshold": {"yes_bound": 0.9, "no_bound": 0.1},
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_uncalibrated_entry_without_date_accepted() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_UNCAL: {
                "status": "uncalibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                # no date: not required for an uncalibrated entry
            }
        },
    }
    entries = loads_registry(json.dumps(registry))
    assert entries[FP_UNCAL].status is ThresholdStatus.UNCALIBRATED


def test_never_auto_accept_entry_without_date_accepted() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_NEVER: {
                "status": "never_auto_accept",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
            }
        },
    }
    entries = loads_registry(json.dumps(registry))
    assert entries[FP_NEVER].status is ThresholdStatus.NEVER_AUTO_ACCEPT


def test_score_interval_label_with_newline_rejected() -> None:
    registry = {
        "schema_version": 1,
        "entries": {
            FP_SCORE: {
                "status": "calibrated",
                "escalation_target": "human-review",
                "evidence_ref": "ev",
                "date": "2026-09-25",
                "type": "score",
                "threshold": {
                    "min_confidence": 0.6,
                    "intervals": [
                        {"lower": 0.0, "upper": 1.0, "label": "safe\nunsafe"},
                    ],
                },
            }
        },
    }
    with pytest.raises(ValidationError) as exc_info:
        loads_registry(json.dumps(registry))
    assert exc_info.value.reason is FailReason.CONFIG


def test_resolve_registry_path_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_REGISTRY", "/env/registry.json")
    assert resolve_registry_path("/explicit/registry.json") == "/explicit/registry.json"


def test_resolve_registry_path_uses_env_when_no_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_KIT_REGISTRY", "/env/registry.json")
    assert resolve_registry_path(None) == "/env/registry.json"


def test_resolve_registry_path_none_when_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_KIT_REGISTRY", raising=False)
    assert resolve_registry_path(None) is None


def test_resolve_registry_path_ignores_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_KIT_REGISTRY", "")
    assert resolve_registry_path(None) is None


def test_score_interval_label_secret_shaped_rejected_h6() -> None:
    # A charset-valid but secret-shaped Score interval label (an AWS key) is copied verbatim into
    # records and receipts, so it is refused at load rather than persisted (finding H6/C08).
    registry: Any = _valid_registry()
    registry["entries"][FP_SCORE]["threshold"]["intervals"][1]["label"] = "AKIA" + "Z" * 16
    with pytest.raises(ValidationError) as exc:
        loads_registry(json.dumps(registry))
    assert exc.value.reason is FailReason.CONFIG


def test_escalation_target_secret_shaped_rejected_h6() -> None:
    registry: Any = _valid_registry()
    registry["entries"][FP_NOUL]["escalation_target"] = "ghp_" + "a" * 36
    with pytest.raises(ValidationError) as exc:
        loads_registry(json.dumps(registry))
    assert exc.value.reason is FailReason.CONFIG
