"""Structural documentation checks, not evidence-quality or engine-safety verification.

Bind the on-demand task index to the shipped JSON fields, question IDs/types, and consequence
classes. Semantic sufficiency, model accuracy, and host authorization still need separate
evaluation; finding a sentence in a Markdown file cannot establish those behaviors.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from jev_kit.install import install

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "jev-runtime"
REFERENCE = SKILL_DIR / "references" / "evidence-contracts.md"
SET_FILES = sorted((SKILL_DIR / "questions").glob("*.json"))


def _index_rows() -> list[list[str]]:
    text = REFERENCE.read_text(encoding="utf-8")
    index = text.split("## Task index\n", 1)[1].split("\n## ", 1)[0]
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in index.splitlines()
        if line.startswith("| [")
    ]


def test_runtime_skill_identity_and_reference_link() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3 and not parts[0]
    frontmatter = dict(line.split(": ", 1) for line in parts[1].strip().splitlines())
    assert frontmatter["name"] == "jev-runtime"
    assert frontmatter["version"] == "2"
    assert frontmatter["description"]
    assert "(references/evidence-contracts.md)" in parts[2]
    assert REFERENCE.is_file()


def test_index_covers_each_shipped_set_once() -> None:
    assert len(SET_FILES) == 4
    rows = _index_rows()
    assert len(rows) == len(SET_FILES)
    assert all(len(row) == 5 for row in rows)
    links = [re.fullmatch(r"\[([^\]]+)\]\(\.\./questions/([^)]+)\)", row[1]) for row in rows]
    assert all(link is not None for link in links)
    filenames = [link.group(2) for link in links if link is not None]
    assert sorted(filenames) == sorted(path.name for path in SET_FILES)
    assert all(link.group(1) == link.group(2) for link in links if link is not None)


@pytest.mark.parametrize("path", SET_FILES, ids=lambda p: p.stem)
def test_index_matches_shipped_contract(path: Path) -> None:
    qset = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in _index_rows() if f"(../questions/{path.name})" in row[1]]
    assert len(rows) == 1
    task, _, fields, questions, consequence = rows[0]
    assert f"(#{path.stem})" in task
    assert qset["id"] == path.stem

    field_names = re.findall(r"`([^`]+)`", fields)
    assert sorted(field_names) == sorted(qset["state_schema"])
    documented_questions = re.findall(r"`([^`]+)` \((choice|noul|score)\)", questions)
    assert sorted(documented_questions) == sorted(
        (qid, question["type"]) for qid, question in qset["questions"].items()
    )
    assert {question["kit"]["consequence"] for question in qset["questions"].values()} == {
        consequence
    }

    reference = REFERENCE.read_text(encoding="utf-8")
    section = reference.split(f"## {path.stem}\n", 1)[1].split("\n## ", 1)[0]
    assert "Required observations:" in section
    assert "Known limitations:" in section


@pytest.mark.parametrize("path", [SKILL_DIR / "SKILL.md", REFERENCE], ids=lambda p: p.name)
def test_runtime_documents_use_portable_local_links_and_no_em_dash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "\u2014" not in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith("#"):
            assert f"## {target[1:]}\n" in text
        else:
            resolved = (path.parent / target).resolve()
            assert resolved.is_relative_to(SKILL_DIR.resolve())
            assert resolved.is_file()


@pytest.mark.parametrize("force_copy", [False, True])
def test_installed_tree_keeps_required_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_copy: bool,
) -> None:
    # Exercise only a private temporary target, never the user's live host directories.
    if force_copy:
        def unavailable(*args: object, **kwargs: object) -> None:
            raise OSError("synthetic symlink failure")

        monkeypatch.setattr(os, "symlink", unavailable)
    target = tmp_path / "installed" / "jev-runtime"
    actions = install([target], SKILL_DIR, apply=True)
    assert actions[0].op in ({"copy"} if force_copy else {"link", "copy"})
    assert (target / "references" / REFERENCE.name).read_bytes() == REFERENCE.read_bytes()
    for source in SET_FILES:
        assert (target / "questions" / source.name).read_bytes() == source.read_bytes()
