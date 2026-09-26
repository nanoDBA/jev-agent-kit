"""Skill installer: link the canonical jev-runtime skill tree into each host's skill dir.

Links (or, where the platform refuses a symlink, copies) the whole `skills/jev-runtime/`
tree (`SKILL.md` plus `questions/`) into each host's skill directory, per
`docs/specs/phase-2-plan.md` (decision D4) and the corrected host paths in
`docs/research/06-reverified-facts.md` section 9. Dry-run by default; `apply=True` is the
only mode that writes. Never follows or writes outside the given target paths: each target's
own parent directories are created, and nothing else on disk is touched or traversed.

Two host families read one repo-level directory: `.agents/skills` serves both Codex and
Hermes, so a repo-scoped install writes it once, not twice, per host.

Idempotent by content: a target that already holds a byte-identical copy of the source tree
is reported as `skip_same` and left untouched, so a repeated `install(..., apply=True)` never
rewrites unchanged files.

No network. Standard library only (ADR 0003).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Minimum Python runtime the kit supports (Hermes compatibility; see ADR 0003).
MIN_PYTHON = (3, 11)

#: Name of the canonical skill directory, appended to every host skill root.
SKILL_DIR_NAME = "jev-runtime"

#: Per-host skill subdirectory, relative to a home directory (scope "user") or the repo
#: root (scope "repo"). The repo-level ".agents/skills" is shared by Codex and Hermes, so it
#: appears once, not once per host, in both scopes.
_HOST_SUBDIRS: tuple[Path, ...] = (
    Path(".claude") / "skills",
    Path(".agents") / "skills",
    Path(".hermes") / "skills",
)

OpName = Literal["link", "copy", "skip_same", "conflict", "would_overwrite"]

__all__ = [
    "MIN_PYTHON",
    "SKILL_DIR_NAME",
    "Action",
    "OpName",
    "default_targets",
    "install",
    "plan_install",
    "python_ok",
]


@dataclass(frozen=True)
class Action:
    """One planned or performed step of an install, for one target directory."""

    target: Path
    op: OpName
    reason: str


def python_ok() -> bool:
    """Return whether the running interpreter meets the kit's minimum (3.11 or later)."""
    return sys.version_info[:2] >= MIN_PYTHON


def default_targets(scope: str, repo_root: Path | None = None) -> list[Path]:
    """Return the canonical target directories for a scope.

    scope "user" returns the per-user host directories under the caller's home directory.
    scope "repo" returns the repo-level host directories under `repo_root` (the current
    working directory if not given). Each returned path ends in `/jev-runtime`.
    """
    if scope == "user":
        base = Path.home()
    elif scope == "repo":
        base = repo_root if repo_root is not None else Path.cwd()
    else:
        raise ValueError(f"unknown scope: {scope!r} (expected 'user' or 'repo')")
    return [base / subdir / SKILL_DIR_NAME for subdir in _HOST_SUBDIRS]


def plan_install(targets: list[Path], source: Path) -> list[Action]:
    """Compute what `install(targets, source, apply=True)` would do, without writing."""
    return _evaluate(targets, source, force=False)


def install(
    targets: list[Path],
    source: Path,
    *,
    apply: bool = False,
    force: bool = False,
) -> list[Action]:
    """Install the skill tree at `source` into each of `targets`.

    Dry-run by default (`apply=False`): writes nothing and returns the planned actions,
    reporting a forceable conflict as `would_overwrite` rather than `conflict` so a preview
    with `force=True` shows the real outcome.

    With `apply=True`: creates each target's parent directory, then tries `os.symlink` of
    the whole source tree; if that raises `OSError` (for example Windows without Developer
    Mode), falls back to a recursive copy. A target that already holds a byte-identical copy
    of `source` is left untouched (`skip_same`). A target that exists and differs is left
    untouched and reported as `conflict` unless `force=True`, in which case it is removed and
    replaced.
    """
    planned = _evaluate(targets, source, force=force)
    if not apply:
        return planned
    return [_apply_one(action, source) for action in planned]


def _evaluate(targets: list[Path], source: Path, force: bool) -> list[Action]:
    source_hash: str | None = None
    can_symlink = _probe_symlink_support()
    op_if_writing: OpName = "link" if can_symlink else "copy"
    actions: list[Action] = []
    for target in targets:
        if not _exists(target):
            actions.append(
                Action(target, op_if_writing, f"target does not exist; would {op_if_writing}")
            )
            continue
        if source_hash is None:
            source_hash = _tree_hash(source)
        if _matches(target, source_hash):
            actions.append(Action(target, "skip_same", "target already matches source"))
        elif force:
            actions.append(
                Action(
                    target,
                    "would_overwrite",
                    f"target differs from source; would overwrite via {op_if_writing}",
                )
            )
        else:
            actions.append(
                Action(
                    target,
                    "conflict",
                    "target exists and differs from source; rerun with force to overwrite",
                )
            )
    return actions


def _apply_one(action: Action, source: Path) -> Action:
    target = action.target
    if action.op in ("skip_same", "conflict"):
        return action
    # op is "link", "copy" (target absent) or "would_overwrite" (force path, target present).
    if action.op == "would_overwrite":
        _remove_existing(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Resolve the source to an absolute path: a relative link is resolved against the
        # link's own directory, not the cwd, so it would dangle (finding Phase2 MAJOR-1).
        os.symlink(source.resolve(), target, target_is_directory=True)
        return Action(target, "link", "linked the skill tree")
    except OSError:
        _copy_tree(source, target)
        return Action(target, "copy", "symlink unavailable; copied the skill tree")


def _exists(target: Path) -> bool:
    return target.is_symlink() or target.exists()


def _remove_existing(target: Path) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target)


def _matches(target: Path, source_hash: str) -> bool:
    try:
        return _tree_hash(target) == source_hash
    except OSError:
        return False


def _tree_hash(root: Path) -> str:
    """Hash the relative file set (names and bytes) under `root`, order-independent."""
    hasher = hashlib.sha256()
    for rel_path in sorted(_relative_files(root)):
        hasher.update(rel_path.as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / rel_path).read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _relative_files(root: Path) -> list[Path]:
    return [p.relative_to(root) for p in root.rglob("*") if p.is_file()]


def _probe_symlink_support() -> bool:
    """Try a throwaway symlink in a private temp directory to predict `os.symlink` success.

    Informational only, used to make the dry-run report the likely op. The real install
    always attempts `os.symlink` first and falls back to a copy on `OSError`, regardless of
    what this probe predicts.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe_source = Path(tmp) / "source"
            probe_source.mkdir()
            probe_link = Path(tmp) / "link"
            os.symlink(probe_source, probe_link, target_is_directory=True)
        return True
    except OSError:
        return False
