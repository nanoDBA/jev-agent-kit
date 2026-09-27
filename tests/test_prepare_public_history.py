"""scripts/prepare_public_history.py must never report "clean" while a private value survives.

The verifier is exercised directly on disposable repositories (no git-filter-repo needed),
including the two cases a patch-view scan missed: a value inside a binary file, and a value in
an annotated tag message (finding D11). The end-to-end test runs the whole script when
git-filter-repo is importable (set JEV_KIT_FILTER_REPO_PATH to its install directory).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
MARKER = "SYNTH_PRIVATE_TEST_TOKEN_5150"
FORBIDDEN = [MARKER]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "prepare_public_history", REPO / "scripts" / "prepare_public_history.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00", "GIT_COMMITTER_DATE":
           "2026-01-01T00:00:00"}
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path, branch: str = "main") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _verify(repo: Path, remove: list[str] | None = None, email: str = "") -> list[str]:
    result: list[str] = _script().verify_repository(repo, FORBIDDEN, remove or [], email)
    return result


def test_clean_repository_passes(tmp_path: Path) -> None:
    assert _verify(_repo(tmp_path)) == []


def test_marker_in_binary_file_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01binary\x00" + MARKER.encode() + b"\x00")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add binary")
    problems = _verify(repo)
    # Binary content cannot be verified as text, so it fails closed on its own.
    assert any(p.startswith("blob blob.bin") and "binary" in p for p in problems)


def test_marker_in_annotated_tag_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "tag", "-a", "v1", "-m", f"release notes {MARKER}")
    problems = _verify(repo)
    assert any(p.startswith("refs:") for p in problems)  # a tag is not allowed at all
    assert any(p.startswith("tag ") and MARKER in p for p in problems)


def test_marker_in_commit_message_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", f"note {MARKER}")
    assert any(p.startswith("commit ") for p in _verify(repo))


def test_marker_in_old_history_fails(tmp_path: Path) -> None:
    # A value deleted from the tree is still published if an earlier commit contains it.
    repo = _repo(tmp_path)
    (repo / "secret.txt").write_text(MARKER, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add")
    _git(repo, "rm", "-q", "secret.txt")
    _git(repo, "commit", "-q", "-m", "remove")
    assert any(p.startswith("blob ") for p in _verify(repo))


def test_extra_branch_or_remote_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "branch", "other")
    _git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    problems = _verify(repo)
    assert any(p.startswith("refs:") for p in problems)
    assert any(p.startswith("remotes:") for p in problems)


def test_removed_path_and_old_email_fail(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "vendor-docs").mkdir()
    (repo / "vendor-docs" / "page.md").write_text("copied\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "vendor")
    problems = _verify(repo, remove=["vendor-docs/"], email="test@example.invalid")
    assert any(p.startswith("path:") for p in problems)
    assert any(p.startswith("identity:") for p in problems)


def _filter_repo_path() -> str | None:
    path = os.environ.get("JEV_KIT_FILTER_REPO_PATH", "")
    probe = subprocess.run(
        [sys.executable, "-c", "import git_filter_repo"],
        env={**os.environ, "PYTHONPATH": path}, capture_output=True,
    )
    return path if probe.returncode == 0 else None


@pytest.mark.skipif(_filter_repo_path() is None, reason="git-filter-repo not importable")
def test_end_to_end_cleans_text_drops_tags_and_fails_on_binary(tmp_path: Path) -> None:
    repo = _repo(tmp_path, branch="dev")
    (repo / "notes.md").write_text(f"server {MARKER}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"notes mention {MARKER}")
    _git(repo, "tag", "-a", "v1", "-m", f"tag {MARKER}")
    values = tmp_path / "values.json"
    values.write_text(
        '{"replacements": [["' + MARKER + '", "<removed>"]], "forbidden_patterns": ["'
        + MARKER + '"]}', encoding="utf-8",
    )

    def run_script(out: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "prepare_public_history.py"),
             "--source", str(repo), "--branch", "dev", "--out", str(out),
             "--private-values", str(values), "--filter-repo-path", _filter_repo_path() or ""],
            capture_output=True, text=True,
        )

    clean = run_script(tmp_path / "out-clean")
    assert clean.returncode == 0, clean.stderr
    assert _git(tmp_path / "out-clean", "for-each-ref", "--format=%(refname)").split() == [
        "refs/heads/main"
    ]

    # A value inside a binary file cannot be rewritten; the run must fail, not pass.
    (repo / "blob.bin").write_bytes(b"\x00" + MARKER.encode() + b"\x00")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "binary")
    failed = run_script(tmp_path / "out-binary")
    assert failed.returncode != 0
    assert "verification FAILED" in failed.stderr


def test_value_spanning_nested_path_components_fails(tmp_path: Path) -> None:
    # Git stores "private/customer-alice" as two tree entries in two objects, so no single
    # object contains the full path; the verifier must scan full paths too (D11 residual).
    repo = _repo(tmp_path)
    (repo / "private").mkdir()
    (repo / "private" / "customer-alice").write_text("harmless content\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "nested")
    problems = _script().verify_repository(repo, ["private/customer-alice"], [], "")
    assert any(p.startswith("path ") for p in problems)


def test_unicode_pattern_semantics_are_preserved(tmp_path: Path) -> None:
    # A byte-compiled regex changes what \b means next to non-ASCII letters, so "José\b"
    # missed a retained "José" (D11 residual). Patterns are matched as written.
    repo = _repo(tmp_path)
    (repo / "people.md").write_text("owner: José\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "people")
    problems = _script().verify_repository(repo, [r"José\b"], [], "")
    assert any("people" in p or p.startswith("blob ") for p in problems)


def test_path_and_unicode_controls_stay_clean(tmp_path: Path) -> None:
    # Controls: an unrelated nested path and a longer name that "José\b" must not match.
    repo = _repo(tmp_path)
    (repo / "private").mkdir()
    (repo / "private" / "customer-bob").write_text("owner: Josélito\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "controls")
    assert _script().verify_repository(repo, ["private/customer-alice", r"José\b"], [], "") == []



def test_object_shared_by_two_paths_is_checked_under_both(tmp_path: Path) -> None:
    # rev-list --objects names a shared object by only one path; every path of every commit
    # must be checked, so a forbidden name on the second path is still caught.
    repo = _repo(tmp_path)
    (repo / "public.txt").write_text("same bytes\n", encoding="utf-8")
    (repo / "customer-alice.txt").write_text("same bytes\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "shared")
    problems = _script().verify_repository(repo, ["customer-alice"], [], "")
    assert any(p.startswith("path customer-alice.txt") for p in problems)


def test_gitlink_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fake = "1" * 40
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{fake},vendor/sub")
    _git(repo, "commit", "-q", "-m", "gitlink")
    assert any(p.startswith("gitlink vendor/sub") for p in _verify(repo))


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "latin-1"])
def test_non_utf8_text_fails_closed(tmp_path: Path, encoding: str) -> None:
    # Text in another encoding could carry a value the scan cannot see, so it is refused.
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_bytes(f"owner: José {MARKER}\n".encode(encoding))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "encoded")
    problems = _verify(repo)
    assert any(p.startswith("blob notes.txt") and "cannot verify" in p for p in problems)


def test_commit_with_non_utf8_message_encoding_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "-c", "i18n.commitEncoding=ISO-8859-1", "commit", "-q", "--allow-empty",
         "-m", "latin-1 message")
    assert any("declares encoding" in p or "not UTF-8" in p for p in _verify(repo))


def test_non_utf8_filename_fails_closed(tmp_path: Path) -> None:
    # A Latin-1 filename ("Jos\xe9.txt") is built with git plumbing, since not every
    # filesystem can hold it. Like non-UTF-8 content, it cannot be checked: fail closed.
    repo = _repo(tmp_path)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=b"harmless\n",
        capture_output=True, check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "mktree"], cwd=repo, input=b"100644 blob " + blob + b"\tJos\xe9.txt\n",
        capture_output=True, check=True,
    ).stdout.decode().strip()
    head = _git(repo, "rev-parse", "HEAD").strip()
    commit = _git(repo, "commit-tree", tree, "-p", head, "-m", "latin-1 name").strip()
    _git(repo, "update-ref", "refs/heads/main", commit)
    problems = _script().verify_repository(repo, [r"José\b"], [], "")
    assert any("filename is not UTF-8" in p for p in problems)
