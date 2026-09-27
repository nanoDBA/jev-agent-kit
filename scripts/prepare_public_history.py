"""Build a history-cleaned copy of one branch, ready to push to a NEW public repository.

Why a new repository: a force-push cannot purge a GitHub repo. Pull-request refs
(refs/pull/*) keep every commit ever proposed, and making the private repo public would
publish them. So the public release starts from a fresh repo, seeded with history that is
cleaned here before it ever leaves the machine.

What it does, on a fresh single-branch clone (the source repo is never modified):
1. drops the listed paths from EVERY commit;
2. replaces private strings in every file AND every commit message;
3. optionally rewrites author/committer identity with a mailmap;
4. renames the branch to `main` and removes all remotes;
5. scans the full rewritten history and messages, and fails if anything private remains.

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

    # 1. Fresh single-branch clone: filter-repo requires one, and the source stays untouched.
    run(["git", "clone", "--no-local", "--single-branch", "--branch", args.branch, source,
         str(out)])

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

    # 5. Verify the whole rewritten history, including commit messages and metadata.
    history = run(["git", "log", "--all", "-p", "--no-color"], cwd=out)
    messages = run(["git", "log", "--all", "--format=%B"], cwd=out)
    problems = [f"content: {pat}" for pat in forbidden if re.search(pat, history)]
    problems += [f"message: {pat}" for pat in forbidden if re.search(pat, messages)]
    names = run(["git", "log", "--all", "--name-only", "--format="], cwd=out).splitlines()
    problems += [f"path: {p}" for p in remove_paths if any(n.startswith(p) for n in names)]
    if old_email and not args.keep_email:
        identities = run(["git", "log", "--all", "--format=%ae%n%ce"], cwd=out)
        if old_email in identities:
            problems.append("old email still in commit metadata")
    if problems:
        sys.exit("verification FAILED, do not publish:\n  " + "\n  ".join(problems))

    count = run(["git", "rev-list", "--count", "main"], cwd=out).strip()
    print(f"ok: {out} has {count} commits on main, verified clean. Nothing was pushed.")


if __name__ == "__main__":
    main()
