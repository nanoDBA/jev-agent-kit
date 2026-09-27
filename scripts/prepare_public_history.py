"""Build a history-cleaned copy of one branch, ready to push to a NEW public repository.

Why a new repository: a force-push cannot purge a GitHub repo. Pull-request refs
(refs/pull/*) keep every commit ever proposed, and making the private repo public would
publish them. So the public release starts from a fresh repo, seeded with history that is
cleaned here before it ever leaves the machine.

What it does, on a fresh single-branch clone without tags (the source repo is never modified):
1. drops the listed paths from EVERY commit;
2. replaces private strings in every file AND every commit message;
3. optionally rewrites author/committer identity with a mailmap;
4. renames the branch to `main` and removes all remotes;
5. verifies every reachable object (commits, trees, blobs, tags) as raw bytes plus the refs,
   and fails if anything private remains, including content it could not rewrite.

The private values themselves (server addresses, file ids, local paths, emails) are not in
this file, or committing it would reintroduce them. They live in a local, gitignored JSON
file passed with --private-values:

    {
      "remove_paths": ["path/removed/from/every/commit/", "..."],
      "replacements": [["literal to find", "replacement"], "..."],
      "mailmap": "New Name <new@email> <old@email>",
      "old_email": "old@email",
      "forbidden_patterns": ["regex that must not survive", "..."]
    }

List replacements longest first. Requires git-filter-repo
(https://github.com/newren/git-filter-repo); pass its install directory with
--filter-repo-path if it is not importable.

Usage:
    python scripts/prepare_public_history.py --source . --branch phase-0 \\
        --out ../jev-public --private-values .public-release.local.json

It never pushes. Pushing the result to a new repository is the owner's decision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def load_values(path: str) -> dict[str, Any]:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        sys.exit("--private-values must contain a JSON object")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a history-cleaned public copy.")
    parser.add_argument("--source", required=True, help="path or URL of the private repo")
    parser.add_argument("--branch", default="phase-0", help="branch to publish")
    parser.add_argument("--out", required=True, help="new directory for the cleaned clone")
    parser.add_argument("--private-values", required=True,
                        help="local JSON file with the private values (never commit it)")
    parser.add_argument("--filter-repo-path", default="", help="dir containing git_filter_repo")
    parser.add_argument("--keep-email", action="store_true", help="skip the mailmap rewrite")
    args = parser.parse_args()

    values = load_values(args.private_values)
    remove_paths: list[str] = values.get("remove_paths", [])
    replacements: list[list[str]] = values.get("replacements", [])
    forbidden: list[str] = values.get("forbidden_patterns", [])
    mailmap: str = values.get("mailmap", "")
    old_email: str = values.get("old_email", "")

    out = Path(args.out).resolve()
    if out.exists():
        sys.exit(f"refusing to overwrite existing directory: {out}")
    source = args.source
    if Path(source).exists():
        source = Path(source).resolve().as_uri()

    # 1. Fresh single-branch clone without tags: filter-repo requires a fresh clone, the source
    # stays untouched, and tags (whose messages are never rewritten) are not published at all.
    run(["git", "clone", "--no-local", "--single-branch", "--no-tags", "--branch", args.branch,
         source, str(out)])

    env = dict(os.environ)
    if args.filter_repo_path:
        env["PYTHONPATH"] = args.filter_repo_path + os.pathsep + env.get("PYTHONPATH", "")
    work = out / ".git" / "public-history-inputs"
    work.mkdir()
    rules = work / "replacements.txt"
    rules.write_text(
        "".join(f"literal:{old}==>{new}\n" for old, new in replacements), encoding="utf-8"
    )
    cmd = [sys.executable, "-m", "git_filter_repo", "--force", "--invert-paths"]
    for path in remove_paths:
        cmd += ["--path", path]
    # Commit messages can quote paths and command output just like files do.
    cmd += ["--replace-text", str(rules), "--replace-message", str(rules)]
    if mailmap and not args.keep_email:
        (work / "mailmap").write_text(mailmap + "\n", encoding="utf-8")
        cmd += ["--mailmap", str(work / "mailmap")]
    run(cmd, cwd=out, env=env)
    shutil.rmtree(work)

    # 4. Publish as main; drop remotes so nothing can be pushed back by accident.
    run(["git", "branch", "-m", args.branch, "main"], cwd=out)
    for remote in run(["git", "remote"], cwd=out).split():
        run(["git", "remote", "remove", remote], cwd=out)
    run(["git", "gc", "--prune=now", "--aggressive", "--quiet"], cwd=out)

    # 5. Verify what would actually be published.
    problems = verify_repository(
        out, forbidden, remove_paths, old_email if not args.keep_email else ""
    )
    if problems:
        sys.exit("verification FAILED, do not publish:\n  " + "\n  ".join(problems))

    count = run(["git", "rev-list", "--count", "main"], cwd=out).strip()
    print(f"ok: {out} has {count} commits on main, verified clean. Nothing was pushed.")


def verify_repository(
    repo: Path, forbidden: list[str], remove_paths: list[str], old_email: str
) -> list[str]:
    """Return every reason the repository is NOT safe to publish (empty means clean).

    It checks what a push would actually publish, not a rendered view of it:
    - refs: exactly one, refs/heads/main, with no tags, notes, stashes, or remotes;
    - every object reachable from any ref (commits, trees, blobs, tags), read as raw bytes
      with `git cat-file`, so binary files and tag messages are covered too;
    - every path in every commit, against the removed paths;
    - author and committer identities in every commit.
    It fails closed: an object that cannot be read is a problem, not a skip. Content that
    git-filter-repo cannot rewrite (for example a secret inside a binary file) therefore makes
    the run fail instead of passing unnoticed.
    """
    problems: list[str] = []
    refs = run(["git", "for-each-ref", "--format=%(refname)"], cwd=repo).split()
    if refs != ["refs/heads/main"]:
        problems.append(f"refs: expected only refs/heads/main, found {refs}")
    if run(["git", "remote"], cwd=repo).split():
        problems.append("remotes: a remote is still configured")

    listing = run(["git", "rev-list", "--objects", "--all"], cwd=repo).splitlines()
    object_ids = [line.split(" ", 1)[0] for line in listing if line]
    patterns = [re.compile(p.encode("utf-8")) for p in forbidden]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=repo, input="\n".join(object_ids).encode() + b"\n",
        capture_output=True,
    )
    if batch.returncode != 0:
        return [*problems, "objects: git cat-file failed, cannot verify"]
    data, pos, seen = batch.stdout, 0, 0
    while pos < len(data):
        header_end = data.index(b"\n", pos)
        header = data[pos:header_end].split()
        if len(header) != 3:
            problems.append(f"objects: unreadable object {header[0].decode()}")
            pos = header_end + 1
            continue
        oid, kind, size = header[0].decode(), header[1].decode(), int(header[2])
        body = data[header_end + 1 : header_end + 1 + size]
        pos = header_end + 1 + size + 1  # object content is followed by a newline
        seen += 1
        for pattern in patterns:
            if pattern.search(body):
                problems.append(f"{kind} {oid[:12]}: matches {pattern.pattern.decode()}")
    if seen != len(object_ids):
        problems.append(f"objects: read {seen} of {len(object_ids)} reachable objects")

    names = run(["git", "log", "--all", "--name-only", "--format="], cwd=repo).splitlines()
    problems += [f"path: {p}" for p in remove_paths if any(n.startswith(p) for n in names)]
    if old_email:
        identities = run(["git", "log", "--all", "--format=%ae%n%ce"], cwd=repo)
        if old_email in identities:
            problems.append("identity: old email still in commit metadata")
    return problems


if __name__ == "__main__":
    main()
