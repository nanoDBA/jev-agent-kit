"""Installer tests (P2-3, docs/specs/phase-2-plan.md decision D4).

Uses a small fake source tree (SKILL.md plus questions/x.json) built under tmp_path rather
than the real repo skill, so these tests are hermetic and never touch the developer's real
host skill directories. No network; no git.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from jev_kit.install import (
    Action,
    default_targets,
    install,
    plan_install,
    python_ok,
)


def _make_source(root: Path) -> Path:
    source = root / "source" / "jev-runtime"
    questions = source / "questions"
    questions.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: jev-runtime\n---\n# jev-runtime\n", encoding="utf-8"
    )
    (questions / "x.json").write_text('{"a": 1}\n', encoding="utf-8")
    return source


def _relative_contents(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_plan_install_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    actions = plan_install([target], source)

    assert not target.exists()
    assert len(actions) == 1
    assert actions[0].target == target
    assert actions[0].op in ("link", "copy")


def test_install_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    actions = install([target], source)

    assert not target.exists()
    assert actions[0].op in ("link", "copy")


def test_apply_into_empty_target_creates_tree(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    actions = install([target], source, apply=True)

    assert len(actions) == 1
    assert actions[0].op in ("link", "copy")
    assert (target / "SKILL.md").is_file()
    assert (target / "questions" / "x.json").is_file()
    assert _relative_contents(target) == _relative_contents(source)


def test_second_apply_is_idempotent(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    first = install([target], source, apply=True)
    before = _relative_contents(target)
    second = install([target], source, apply=True)
    after = _relative_contents(target)

    assert first[0].op in ("link", "copy")
    assert second[0].op == "skip_same"
    assert before == after == _relative_contents(source)


def test_default_targets_dry_run_plan_matches_install(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    assert plan_install([target], source) == install([target], source)


def test_differing_target_is_conflict_without_force(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("something else entirely\n", encoding="utf-8")

    actions = install([target], source, apply=True)

    assert actions[0].op == "conflict"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "something else entirely\n"
    assert not (target / "questions").exists()


def test_dry_run_with_force_reports_would_overwrite(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("something else entirely\n", encoding="utf-8")

    actions = install([target], source, apply=False, force=True)

    assert actions[0].op == "would_overwrite"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "something else entirely\n"


def test_differing_target_with_force_is_overwritten(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("something else entirely\n", encoding="utf-8")
    (target / "stale.txt").write_text("leftover file\n", encoding="utf-8")

    actions = install([target], source, apply=True, force=True)

    assert actions[0].op in ("link", "copy")
    assert _relative_contents(target) == _relative_contents(source)
    assert not (target / "stale.txt").exists()


def test_symlink_failure_falls_back_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    target = tmp_path / "target" / "jev-runtime"

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("symlink privilege not held")

    monkeypatch.setattr(os, "symlink", _raise_oserror)

    actions = install([target], source, apply=True)

    assert actions[0].op == "copy"
    assert not target.is_symlink()
    assert _relative_contents(target) == _relative_contents(source)


def test_default_targets_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    targets = default_targets("user")

    assert targets == [
        tmp_path / ".claude" / "skills" / "jev-runtime",
        tmp_path / ".agents" / "skills" / "jev-runtime",
        tmp_path / ".hermes" / "skills" / "jev-runtime",
    ]


def test_default_targets_repo(tmp_path: Path) -> None:
    targets = default_targets("repo", repo_root=tmp_path)

    assert targets == [
        tmp_path / ".claude" / "skills" / "jev-runtime",
        tmp_path / ".agents" / "skills" / "jev-runtime",
        tmp_path / ".hermes" / "skills" / "jev-runtime",
    ]


def test_default_targets_unknown_scope_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        default_targets("bogus", repo_root=tmp_path)


def test_python_ok_reflects_running_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 11, 0, "final", 0))
    assert python_ok() is True

    monkeypatch.setattr(sys, "version_info", (3, 10, 9, "final", 0))
    assert python_ok() is False


def test_action_is_frozen(tmp_path: Path) -> None:
    action = Action(target=tmp_path, op="skip_same", reason="test")
    with pytest.raises(AttributeError):
        action.op = "conflict"  # type: ignore[misc]


def test_apply_symlink_resolves_relative_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Phase2 MAJOR-1: a relative source must be resolved so the installed link is not dangling.
    src = tmp_path / "skills" / "jev-runtime"
    (src / "questions").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: jev-runtime\n---\n", encoding="utf-8")
    (src / "questions" / "x.json").write_text("{}", encoding="utf-8")
    target_root = tmp_path / "dest"
    target = target_root / "jev-runtime"
    monkeypatch.chdir(tmp_path)  # so a relative source path is realistic
    from jev_kit import install as install_mod

    actions = install_mod.install([target], Path("skills/jev-runtime"), apply=True)
    assert actions[0].op in ("link", "copy")
    # The installed skill must be reachable through the target, however it was placed.
    assert (target / "SKILL.md").is_file()
