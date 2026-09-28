"""Deterministic command properties: a command line -> keyed tri-state facts (ADR 0005).

COMMAND egress sends only the program name (finding H16), so the gate cannot tell `rm file`
from `rm -rf /`. This module computes a small, fixed set of facts about the command locally, in
code (non-negotiable 6), so the gate can send booleans instead of raw arguments.

Each property is True, False, or None:

- True: the command shows the property (under at least one plausible shell reading).
- False: the command was parsed confidently and does not show it.
- None: unknown. Never read None as False.

Rules that keep this conservative:

- Detection is biased toward True. When a token could be a flag or an option's argument, it is
  read as the flag; when a backslash could be a POSIX escape or a Windows path separator, both
  readings are checked and the results are OR-ed. A wrong True adds friction; a wrong False
  could hide danger.
- Anything this parser cannot read with confidence (compound or piped lines, quoting, variable
  expansion, grouping, cmd escapes, wrappers with options, commands that run other commands,
  unknown git subcommands, very long input) sets `parse_confident` to False. Then every
  property that was not positively detected is None, never False.
- Shell aliases and functions defined by the user cannot be seen. That is a documented residual
  risk (ADR 0005), not something this module guesses about.

The function is pure: no filesystem, environment, or process access. It never raises for any
string input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The property names, in the order they appear in state. State keys add the `cmd_` prefix.
PROPERTY_NAMES: tuple[str, ...] = (
    "recursive_delete",
    "force_flag",
    "targets_root_or_system_path",
    "targets_home_directory",
    "uses_elevation",
    "network_download",
    "pipes_to_shell",
    "modifies_permissions",
    "git_history_rewrite",
    "package_install",
)

STATE_PREFIX = "cmd_"
CONFIDENT_KEY = STATE_PREFIX + "parse_confident"

MAX_COMMAND_CHARS = 4096
MAX_TOKENS = 128

# Characters whose presence means the line is not a single simple command this parser can read:
# control operators and redirects, substitution, quoting, variable expansion (POSIX `$`, cmd
# `%` and delayed `!`), grouping and brace expansion, cmd's `^` escape, and cmd's `,` and `=`
# argument separator `,`. A word starting with `@` (PowerShell splatting) is checked per word.
_UNCONFIDENT_CHARS = frozenset(";&|<>`\"'$%!(){}^,")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SAFE_PROGRAM = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")
_GLOB = frozenset("*?[")

_ELEVATION = frozenset({"sudo", "doas", "su", "pkexec", "runas", "gsudo", "run0"})
# Wrappers that run the next word as the command. When their next word is an option we cannot
# know the option's arity, so the parse is not confident.
_PLAIN_WRAPPERS = frozenset({"sudo", "doas", "gsudo", "run0", "nohup", "time", "command",
                             "builtin", "exec", "nice", "env", "stdbuf", "ionice", "chronic"})
# Programs that run a command, script, or code string we cannot see from here.
_INDIRECT = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash", "busybox",
    "pwsh", "powershell", "cmd", "eval", "source", ".", "call", "start", "start-process",
    "saps", "iex", "invoke-expression", "invoke-command", "icm", "xargs", "parallel", "watch",
    "timeout", "ssh", "su", "pkexec", "runas", "wsl", "script", "expect", "osascript",
    "invoke-item", "ii", "schtasks", "at", "crontab", "flock", "setsid", "unbuffer", "strace",
    "sg", "chroot", "nsenter", "unshare", "systemd-run", "wscript", "cscript", "mshta",
    "rundll32", "regsvr32", "forfiles", "doskey",
})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash",
                     "pwsh", "powershell", "cmd", "iex", "invoke-expression", "python",
                     "python3", "perl", "ruby", "node"})

_POSIX_DELETE = frozenset({"rm"})
_PS_DELETE = frozenset({"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"})
_CMD_DELETE = frozenset({"del", "erase", "rd", "rmdir"})
_DOWNLOADERS = frozenset({
    "curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod",
    "start-bitstransfer", "bitsadmin", "aria2c", "ftp", "tftp", "lftp", "scp", "sftp",
    "rsync", "fetch", "http", "httpie", "xh",
})
_PERMISSIONS = frozenset({
    "chmod", "chown", "chgrp", "chattr", "setfacl", "icacls", "cacls", "xcacls", "takeown",
    "attrib", "set-acl", "usermod", "gpasswd", "useradd", "adduser", "passwd", "visudo",
    "add-localgroupmember", "set-localuser", "new-localuser", "grant-smbshareaccess",
    "setcap", "chcon", "setsebool",
})

# Git global options that take a separate argument word, and those that take none. Any other
# dashed word before the subcommand makes the parse unconfident.
_GIT_GLOBAL_WITH_ARG = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                  "--super-prefix", "--config-env", "--exec-path"})
_GIT_GLOBAL_NO_ARG = frozenset({"--no-pager", "-p", "--paginate", "-P", "--bare",
                                "--no-replace-objects", "--literal-pathspecs",
                                "--glob-pathspecs", "--noglob-pathspecs",
                                "--icase-pathspecs", "--no-optional-locks", "--version",
                                "--help", "--html-path", "--man-path", "--info-path",
                                "--no-lazy-fetch", "--no-advice"})
# Subcommands git ships. Anything else may be a user alias, which can run anything.
_GIT_KNOWN = frozenset({
    "add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle",
    "cat-file", "check-ignore", "checkout", "cherry", "cherry-pick", "citool", "clean",
    "clone", "commit", "config", "count-objects", "describe", "diff", "diff-files",
    "diff-index", "diff-tree", "difftool", "fetch", "filter-branch", "filter-repo",
    "for-each-ref", "format-patch", "fsck", "gc", "grep", "help", "init", "log", "ls-files",
    "ls-remote", "ls-tree", "maintenance", "merge", "merge-base", "mergetool", "mv",
    "name-rev", "notes", "prune", "pull", "push", "range-diff", "rebase", "reflog", "remote",
    "repack", "replace", "reset", "restore", "rev-list", "rev-parse", "revert", "rm",
    "shortlog", "show", "show-ref", "sparse-checkout", "stash", "status", "submodule",
    "switch", "symbolic-ref", "tag", "update-index", "update-ref", "version", "whatchanged",
    "worktree", "lfs", "var", "verify-commit", "verify-tag", "write-tree", "hash-object",
})
_GIT_DOWNLOAD = frozenset({"clone", "fetch", "pull"})

_SYSTEM_POSIX = frozenset({
    "bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "libx32", "proc", "root", "sbin",
    "sys", "usr", "var", "opt", "srv", "snap", "system", "library", "applications", "private",
    "cores", "volumes", "efi", "lost+found",
})
_SYSTEM_WINDOWS = frozenset({"windows", "program files", "program files (x86)", "programdata",
                             "system volume information", "boot", "recovery", "$recycle.bin"})
_HOME_BASES_POSIX = ("home", "users")
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class CommandProperties:
    """Tri-state properties of one command line. See the module docstring."""

    parse_confident: bool
    values: dict[str, bool | None]
    reason: str | None = None  # why the parse is not confident; local diagnostics only

    def as_state(self) -> dict[str, bool | None]:
        """The flat, keyed state fields a question set may declare with kind `flag`."""
        state: dict[str, bool | None] = {CONFIDENT_KEY: self.parse_confident}
        for name in PROPERTY_NAMES:
            state[STATE_PREFIX + name] = self.values.get(name)
        return state


class _Found:
    """Accumulates positive detections and the first reason for low confidence."""

    def __init__(self) -> None:
        self.true: set[str] = set()
        self.unconfident_reason: str | None = None

    def hit(self, name: str) -> None:
        self.true.add(name)

    def unsure(self, reason: str) -> None:
        if self.unconfident_reason is None:
            self.unconfident_reason = reason


def extract_command_properties(command: object) -> CommandProperties:
    """Compute the property set for one command line. Pure; never raises."""
    found = _Found()
    try:
        _analyze(command, found)
    except Exception:  # a bug here must mean unknown, never a crash or a False
        found.unsure("internal_error")
    if "package_install" in found.true:
        # Every package install is also a download (ADR 0005), and installs run package
        # scripts (postinstall, setup.py, maintainer scripts) this module cannot see.
        found.hit("network_download")
        found.unsure("package_scripts")
    confident = found.unconfident_reason is None
    values: dict[str, bool | None] = {}
    for name in PROPERTY_NAMES:
        if name in found.true:
            values[name] = True
        else:
            values[name] = False if confident else None
    return CommandProperties(confident, values, found.unconfident_reason)


# --------------------------------------------------------------------------- analysis


def _analyze(command: object, found: _Found) -> None:
    if not isinstance(command, str):
        found.unsure("not_text")
        return
    if len(command) > MAX_COMMAND_CHARS:
        found.unsure("too_long")
        _raw_scan(command[:MAX_COMMAND_CHARS], found)
        return
    if not command.strip():
        found.unsure("empty")
        return
    if _CONTROL.search(command):
        found.unsure("control_character")
    if not command.isascii():
        found.unsure("non_ascii")  # look-alike letters, zero-width or non-breaking spaces
    if any(ch in _UNCONFIDENT_CHARS for ch in command):
        found.unsure("shell_syntax")
    tokens = command.split()
    if any(tok.startswith("@") for tok in tokens):
        found.unsure("splatting")
    if len(tokens) > MAX_TOKENS:
        found.unsure("too_many_tokens")
        tokens = tokens[:MAX_TOKENS]
    _analyze_tokens(tokens, found, depth=0)
    if found.unconfident_reason is not None:
        # Pattern checks over the raw text find properties in lines we cannot parse. They only
        # add True; on a confident parse the word analysis above is authoritative.
        _raw_scan(command, found)


def _analyze_tokens(tokens: list[str], found: _Found, depth: int) -> None:
    if depth > 4:
        found.unsure("wrapper_depth")
        return
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGNMENT.match(tokens[idx]):
        # cmd.exe treats `=` as an argument separator, so `rd=/s` may run `rd`. An assignment
        # whose name is a program this module knows is therefore not trusted as an assignment.
        if tokens[idx].split("=", 1)[0].lower() in _KNOWN_PROGRAMS:
            found.unsure("ambiguous_assignment")
        idx += 1
    if idx >= len(tokens):
        found.unsure("no_command_word")
        return
    word = tokens[idx]
    if word.startswith(("./", ".\\", "../", "..\\")) or word.lower().endswith(_SCRIPT_SUFFIXES):
        found.unsure("runs_unseen_code")  # a local script: its contents are not inspected
    readings = _program_readings(word)
    if not readings:
        found.unsure("program_unreadable")
    # Every argument, under both a literal and a POSIX-unescaped reading.
    for tok in tokens[idx + 1:]:
        for variant in _variants(tok):
            _check_path_token(variant, found)
    for program, extra in readings:
        for tok in extra:
            _check_path_token(tok, found)
        _analyze_program(program, extra + tokens[idx + 1:], found, depth)


def _variants(token: str) -> set[str]:
    out = {token}
    if "\\" in token:
        out.add(token.replace("\\", ""))
    return out


def _program_name(candidate: str) -> str | None:
    name = candidate.rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".com", ".cmd", ".bat"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    if name in {".", ".."}:
        return "."
    return name if name and _SAFE_PROGRAM.match(name) else None


def _program_readings(word: str) -> list[tuple[str, list[str]]]:
    """The commands a word could run as, each with any arguments glued onto the word.

    Readings: the Windows one (backslash is a separator), the POSIX-unescaped one (backslash
    escapes the next character), and cmd.exe's `rd/s/q` form, where a slash right after a
    built-in name starts its switches. Every reading is analyzed and the results are OR-ed.
    """
    readings: list[tuple[str, list[str]]] = []
    if any(ch in word for ch in _GLOB):
        return readings  # a glob can expand to any program
    for candidate in (word.replace("\\", "/"), word.replace("\\", "")):
        name = _program_name(candidate)
        if name is not None and (name, []) not in readings:
            readings.append((name, []))
    if "/" in word and not word.startswith(("/", ".")) and not _DRIVE.match(word):
        head, _, tail = word.partition("/")
        if head.lower() in _CMD_BUILTINS:
            readings.append((head.lower(), ["/" + part for part in tail.split("/") if part]))
    return readings


def _analyze_program(program: str, args: list[str], found: _Found, depth: int) -> None:
    if program in _ELEVATION:
        found.hit("uses_elevation")
    if program in _PLAIN_WRAPPERS:
        rest = list(args)
        if program == "env":
            while rest and _ENV_ASSIGNMENT.match(rest[0]):
                rest.pop(0)
        if not rest:
            return
        if rest[0].startswith("-"):
            found.unsure("wrapper_option")
            return
        _analyze_tokens(rest, found, depth + 1)
        return
    if program in _INDIRECT:
        found.unsure("indirect_execution")
        return
    if program in _RUNNERS or program.startswith("python"):
        # Interpreters, build tools, test runners and package runners execute code (a script,
        # a module, a make target, a package script) that this module cannot see. Positive
        # detections below still count; nothing undetected may be reported as false.
        found.unsure("runs_unseen_code")
    if program == "find":
        _find(args, found)
        return
    if program == "git":
        _git(args, found)
        return

    # Delete commands: POSIX rm, PowerShell Remove-Item and its aliases, cmd del/rd.
    if program in _POSIX_DELETE or program in _PS_DELETE:
        for tok in _options(args):
            # POSIX short-option clusters apply to `rm` only; the other names are PowerShell
            # Remove-Item aliases, whose parameters match by prefix.
            posix = program in _POSIX_DELETE
            if (posix and _posix_cluster_has(tok, "rR")) or tok == "--recursive" or _ps_param(
                tok, "recurse", 1
            ):
                found.hit("recursive_delete")
            if (posix and _posix_cluster_has(tok, "fF")) or tok == "--force" or _ps_param(
                tok, "force", 2
            ):
                found.hit("force_flag")
    if program in _CMD_DELETE:
        for tok in args:
            # cmd accepts switches run together: `/S/Q`.
            switches = {"/" + part for part in tok.lower().split("/")[1:]} if tok.startswith(
                "/"
            ) else set()
            if "/s" in switches:
                found.hit("recursive_delete")
            if switches & {"/q", "/f"}:
                found.hit("force_flag")
    if program in {"cp", "mv", "ln"} or program in _PS_COPY:
        for tok in _options(args):
            if _posix_cluster_has(tok, "f") or tok == "--force" or _ps_param(tok, "force", 2):
                found.hit("force_flag")
    for tok in args:
        if tok.lower() in {"--force", "-force"}:
            found.hit("force_flag")

    if program in _DOWNLOADERS:
        found.hit("network_download")
    if program == "certutil" and any(t.lower() in {"-urlcache", "/urlcache"} for t in args):
        found.hit("network_download")
    if program == "npx" or program == "pnpx" or program == "bunx":
        found.hit("package_install")
        found.hit("network_download")

    if program in _PERMISSIONS:
        found.hit("modifies_permissions")
    if program == "net" and any(t.lower() in {"localgroup", "user", "group"} for t in args):
        found.hit("modifies_permissions")

    _package(program, args, found)


_PS_COPY = frozenset({"copy-item", "cpi", "copy", "move-item", "mi", "move"})


def _options(args: list[str]) -> list[str]:
    """Dashed words up to a `--` end-of-options marker (POSIX and PowerShell style)."""
    out: list[str] = []
    for tok in args:
        if tok == "--":
            break
        for variant in _variants(tok):
            if variant.startswith("-") and len(variant) > 1:
                out.append(variant)
    return out


def _posix_cluster_has(tok: str, letters: str) -> bool:
    if tok.startswith("--") or not tok.startswith("-"):
        return False
    return any(ch in tok[1:] for ch in letters)


def _ps_param(tok: str, name: str, min_prefix: int) -> bool:
    """True if `tok` is a PowerShell parameter prefix of `name` (case-insensitive)."""
    if tok.startswith("--") or not tok.startswith("-"):
        return False
    body = tok[1:].split(":", 1)[0].lower()
    return len(body) >= min_prefix and name.startswith(body)


def _find(args: list[str], found: _Found) -> None:
    lowered = [t.lower() for t in args]
    if any(t in {"-exec", "-execdir", "-ok", "-okdir"} for t in lowered):
        found.unsure("indirect_execution")
    if "-delete" in lowered:
        found.hit("recursive_delete")


def _git(args: list[str], found: _Found) -> None:
    idx = 0
    while idx < len(args):
        tok = args[idx]
        if not tok.startswith("-"):
            break
        name = tok.split("=", 1)[0]
        if name in {"-c", "--config-env", "--exec-path"}:
            # A config override can set an alias, pager, hook path or ssh command: any of them
            # runs arbitrary programs.
            found.unsure("git_config_override")
            return
        if name in _GIT_GLOBAL_WITH_ARG:
            idx += 1 if "=" in tok else 2
            continue
        if tok in _GIT_GLOBAL_NO_ARG:
            idx += 1
            continue
        found.unsure("git_unknown_global_option")
        return
    if idx >= len(args):
        return
    sub = args[idx].lower()
    rest = args[idx + 1:]
    if sub not in _GIT_KNOWN:
        found.unsure("git_unknown_subcommand")  # possibly a user alias
        return
    opts = _options(rest)
    words = {t.lower() for t in rest}
    if sub in _GIT_DOWNLOAD:
        found.hit("network_download")
    if sub == "push":
        if any(t.startswith("+") or t.startswith(":") for t in rest):
            found.hit("git_history_rewrite")
        for tok in opts:
            if (_posix_cluster_has(tok, "fd") or tok.startswith("--force")
                    or tok in {"--mirror", "--delete", "--prune"}):
                found.hit("git_history_rewrite")
            if _posix_cluster_has(tok, "f") or tok.startswith("--force"):
                found.hit("force_flag")
    elif sub == "reset":
        if "--hard" in words or "--merge" in words or "--keep" in words:
            found.hit("git_history_rewrite")
    elif sub in {"rebase", "filter-branch", "filter-repo", "replace"}:
        found.hit("git_history_rewrite")
    elif sub == "commit":
        if "--amend" in words:
            found.hit("git_history_rewrite")
    elif sub in {"branch", "tag"}:
        for tok in opts:
            if _posix_cluster_has(tok, "DdMCf") or tok in {"--delete", "--force"}:
                found.hit("git_history_rewrite")
    elif sub == "reflog":
        if words & {"expire", "delete"}:
            found.hit("git_history_rewrite")
    elif sub == "update-ref":
        found.hit("git_history_rewrite")
    elif sub == "stash":
        if words & {"drop", "clear"}:
            found.hit("git_history_rewrite")
    elif sub in {"gc", "prune"}:
        if any(t.startswith("--prune") or t == "--expire" for t in words) or sub == "prune":
            found.hit("git_history_rewrite")
    elif sub == "clean":
        for tok in opts:
            if _posix_cluster_has(tok, "d"):
                found.hit("recursive_delete")
            if _posix_cluster_has(tok, "f") or tok == "--force":
                found.hit("force_flag")
    elif sub == "checkout" or sub == "switch":
        for tok in opts:
            if _posix_cluster_has(tok, "fB") or tok in {"--force", "--discard-changes"}:
                found.hit("force_flag")
    if any(t in {"--force", "-f"} for t in opts) and sub in {"push", "clean", "checkout",
                                                          "switch", "branch", "tag", "rm",
                                                          "mv", "add", "worktree"}:
        found.hit("force_flag")
    if sub == "submodule" and words & {"update", "add"}:
        found.hit("network_download")
    if sub == "lfs" and words & {"pull", "fetch", "clone"}:
        found.hit("network_download")


# program -> subcommands that install packages
_PM_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset({"install", "i", "ci", "add", "isntall", "in", "update", "up", "exec", "x"}),
    "pnpm": frozenset({"install", "i", "add", "update", "up", "dlx", "exec"}),
    "yarn": frozenset({"install", "add", "up", "upgrade", "dlx"}),
    "bun": frozenset({"install", "i", "add", "update", "x"}),
    "pip": frozenset({"install", "download"}),
    "pip3": frozenset({"install", "download"}),
    "pipx": frozenset({"install", "run", "inject", "upgrade"}),
    "poetry": frozenset({"add", "install", "update"}),
    "pdm": frozenset({"add", "install", "update", "sync"}),
    "conda": frozenset({"install", "create", "update"}),
    "mamba": frozenset({"install", "create", "update"}),
    "micromamba": frozenset({"install", "create", "update"}),
    "gem": frozenset({"install", "update"}),
    "bundle": frozenset({"install", "add", "update"}),
    "cargo": frozenset({"install", "add", "binstall"}),
    "go": frozenset({"install", "get"}),
    "apt": frozenset({"install", "reinstall", "upgrade", "full-upgrade", "dist-upgrade"}),
    "apt-get": frozenset({"install", "reinstall", "upgrade", "dist-upgrade", "source"}),
    "dnf": frozenset({"install", "reinstall", "upgrade", "update", "groupinstall"}),
    "yum": frozenset({"install", "reinstall", "upgrade", "update", "groupinstall", "localinstall"}),
    "zypper": frozenset({"install", "in", "update", "up", "dist-upgrade", "dup"}),
    "apk": frozenset({"add", "upgrade"}),
    "brew": frozenset({"install", "reinstall", "upgrade", "tap"}),
    "port": frozenset({"install", "upgrade"}),
    "snap": frozenset({"install", "refresh"}),
    "flatpak": frozenset({"install", "update"}),
    "choco": frozenset({"install", "upgrade"}),
    "winget": frozenset({"install", "upgrade", "import"}),
    "scoop": frozenset({"install", "update"}),
    "composer": frozenset({"require", "install", "update", "create-project"}),
    "dotnet": frozenset({"add", "tool", "restore", "workload"}),
    "nuget": frozenset({"install", "restore"}),
    "helm": frozenset({"install", "upgrade"}),
    "mvn": frozenset({"install", "dependency:get"}),
    "deno": frozenset({"install", "add"}),
}
_PM_DIRECT = frozenset({"install-module", "install-package", "install-script", "update-module",
                        "add-appxpackage", "dpkg", "rpm", "msiexec", "easy_install",
                        "install-psresource"})


def _package(program: str, args: list[str], found: _Found) -> None:
    if program in _PM_DIRECT:
        if program in {"dpkg", "rpm"}:
            if any(_posix_cluster_has(t, "iU") or t in {"--install", "--upgrade"} for t in args):
                found.hit("package_install")
            return
        found.hit("package_install")
        return
    if program == "pacman":
        if any(_posix_cluster_has(t, "SU") and not t.startswith("--") for t in args):
            found.hit("package_install")
        return
    if program.startswith("python") or program in {"py", "uv", "uvx"}:
        _python_like(program, args, found)
        return
    subs = _PM_SUBCOMMANDS.get(program)
    if subs is None:
        return
    # Any matching word, not only the first: an option before the subcommand may take an
    # argument (`npm --prefix dir install`), and a false True only adds friction.
    words = [t.lower() for t in args if not t.startswith("-")]
    if any(w in subs for w in words) or (program == "yarn" and not words):
        # A bare `yarn` installs dependencies.
        found.hit("package_install")
        found.hit("network_download")


def _python_like(program: str, args: list[str], found: _Found) -> None:
    if program == "uvx":
        found.hit("package_install")
        found.hit("network_download")
        return
    if program == "uv":
        words = [t.lower() for t in args if not t.startswith("-")]
        if words[:1] in (["add"], ["sync"], ["run"]) or words[:2] in (
            ["pip", "install"], ["tool", "install"], ["tool", "run"], ["pip", "sync"]
        ):
            found.hit("package_install")
            found.hit("network_download")
        return
    # python -m pip install ...; python -c or a script runs code we cannot see.
    if not args:
        return
    if args[0] == "-m" and len(args) >= 2:
        module = args[1].lower()
        if module in {"pip", "pip3", "ensurepip", "pipx", "uv"}:
            if module == "ensurepip" or any(t.lower() == "install" for t in args[2:]):
                found.hit("package_install")
                found.hit("network_download")
            return
        return
    if args[0].startswith("-"):
        found.unsure("interpreter_option")


# --------------------------------------------------------------------------- paths


def _check_path_token(token: str, found: _Found) -> None:
    # `--opt=value` and `-Param:value`: check the value too.
    candidates = [token]
    for sep in ("=", ":"):
        if sep in token and token.startswith("-"):
            candidates.append(token.split(sep, 1)[1])
    for cand in candidates:
        if not cand:
            continue
        if _is_root_or_system(cand):
            found.hit("targets_root_or_system_path")
        if _is_home(cand):
            found.hit("targets_home_directory")
        if (cand.startswith("\\\\") or cand.startswith("//")) and _segments(cand):
            found.unsure("unc_or_network_path")
        if _absolute(cand) is None and _net_climb(cand) >= 3:
            # Without the working directory, a path this far up may be the home or root.
            found.unsure("relative_path_climbs")


def _net_climb(path: str) -> int:
    """How many levels above its start a relative path ends up (`a/../../..` climbs 2)."""
    depth = lowest = 0
    for seg in _segments(path):
        if seg == "..":
            depth -= 1
            lowest = min(lowest, depth)
        elif seg != ".":
            depth += 1
    return -lowest


def _segments(path: str) -> list[str]:
    return [s for s in path.replace("\\", "/").split("/") if s]


def _has_glob(text: str) -> bool:
    return any(ch in text for ch in _GLOB)


def _resolve(segs: list[str]) -> list[str]:
    """Lexically resolve `.` and `..`; `..` at the top stays at the top, as the OS does."""
    out: list[str] = []
    for seg in segs:
        if seg in {"", "."}:
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return out


def _absolute(token: str) -> tuple[str, list[str]] | None:
    """An absolute path as (flavor, resolved lower-case segments), or None if relative.

    Both separators are accepted. Flavors: "posix" for `/x` and a leading-backslash `\\x`,
    "windows" for a drive form (`C:`, `C:\\x`, `C:/x`). A tilde path is mapped onto the home
    base (`~` -> /home/~, `~user` -> /home/user) before resolving, so `~/project/../.ssh` and
    `~/..` are judged where they land. UNC paths are marked unknown by the caller.
    """
    low = token.lower().replace("\\", "/")
    if low.startswith("~"):
        first, _, rest = low.partition("/")
        return "posix", _resolve(["home", first, *rest.split("/")])
    if low.startswith("/"):
        return "posix", _resolve(low.split("/"))
    if _DRIVE.match(low):
        return "windows", _resolve(low[2:].split("/"))
    return None


def _is_root_or_system(token: str) -> bool:
    resolved = _absolute(token)
    if resolved is None:
        return "system32" in _segments(token.lower())
    flavor, segs = resolved
    if not segs:
        return True  # "/", "C:", "C:\", "\", "/usr/.."
    first = segs[0]
    if _has_glob(first) or "system32" in segs:
        return True  # "/*", "/e?c" and the like can match system directories
    if flavor == "posix" and first in _SYSTEM_POSIX:
        return True
    # A leading-backslash path is posix-flavored after the separator swap, so the Windows
    # names are checked for both flavors.
    return first in _SYSTEM_WINDOWS or first.startswith("progra")  # also 8.3 PROGRA~1


def _is_home(token: str) -> bool:
    """The home base, one home directory, a wildcard over it, or a hidden entry directly under
    it, judged after lexical `.`/`..` resolution. An ordinary project child is not home."""
    resolved = _absolute(token)
    if resolved is None:
        return False
    _, segs = resolved
    if not segs or segs[0] not in _HOME_BASES_POSIX:
        return False
    if len(segs) <= 2 or _has_glob(segs[1]):
        return True
    return len(segs) == 3 and (segs[2].startswith(".") or _has_glob(segs[2]))


# --------------------------------------------------------------------------- raw scan

_PIPE_TO_SHELL = re.compile(
    r"\|\s*(?:sudo\s+|doas\s+|&\s*)?(?:[\w./\\-]*[/\\])?(?:"
    + "|".join(sorted(re.escape(s) for s in _SHELLS))
    + r")(?:\.exe)?(?![\w.-])",
    re.IGNORECASE,
)
_PROCESS_SUBST_SHELL = re.compile(r"\b(?:ba|z|k|da)?sh\s+<\(", re.IGNORECASE)
_IEX_DOWNLOAD = re.compile(
    r"\b(?:iex|invoke-expression)\b.*\b(?:iwr|irm|invoke-webrequest|invoke-restmethod|"
    r"downloadstring|downloadfile|net\.webclient|curl|wget)\b"
    r"|\b(?:iwr|irm|invoke-webrequest|invoke-restmethod|downloadstring|net\.webclient|curl|"
    r"wget)\b.*\b(?:iex|invoke-expression)\b",
    re.IGNORECASE,
)
_HOME_VARS = re.compile(
    r"\$\{?home\}?(?![\w])|\$env:(?:userprofile|homepath|home)\b|%(?:userprofile|homepath|home)%"
    r"|\$home\b",
    re.IGNORECASE,
)
_SYSTEM_VARS = re.compile(
    r"\$env:(?:systemroot|windir|programfiles|programdata|systemdrive)\b"
    r"|%(?:systemroot|windir|programfiles|programdata|systemdrive)%",
    re.IGNORECASE,
)
_RAW_ELEVATION = re.compile(r"(?:^|[\s;&|(])(?:sudo|doas|gsudo|pkexec|run0)(?![\w-])"
                            r"|-verb\s+runas\b", re.IGNORECASE)
_RAW_DOWNLOAD = re.compile(
    r"(?:^|[\s;&|(])(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|aria2c|"
    r"start-bitstransfer|bitsadmin)(?:\.exe)?(?![\w-])|downloadstring|downloadfile|net\.webclient",
    re.IGNORECASE,
)
_RAW_RECURSIVE = re.compile(
    r"(?:^|[\s;&|(])(?:rm|remove-item|ri|del|erase|rd|rmdir)(?:\.exe)?\s[^;&|\n]*?"
    r"(?:\s-[a-zA-Z]*[rR][a-zA-Z]*\b|\s--recursive\b|\s-rec\w*|\s/s\b)",
    re.IGNORECASE,
)
_RAW_PERMISSIONS = re.compile(
    r"(?:^|[\s;&|(])(?:chmod|chown|chgrp|icacls|takeown|setfacl|set-acl)(?:\.exe)?(?![\w-])",
    re.IGNORECASE,
)
_RAW_GIT_REWRITE = re.compile(
    r"\bgit\b[^;&|\n]*\s(?:push\b[^;&|\n]*\s(?:--force|-f\b|--mirror|--delete|\+)|"
    r"reset\b[^;&|\n]*--hard|rebase\b|filter-branch\b|filter-repo\b)",
    re.IGNORECASE,
)


_SEGMENT_SPLIT = re.compile(r"[;&|\n]+|\$\(|`|<\(|[()]")
_STRIP_QUOTES = str.maketrans({'"': " ", "'": " "})


def _raw_scan(text: str, found: _Found) -> None:
    """Positive-only checks over a line we could not parse: they can set True, never False.

    Each piece between control operators is analyzed as its own simple command (with quotes
    blanked out), and a few patterns run over the whole line. Only the True results are kept.
    """
    for piece in _SEGMENT_SPLIT.split(text.translate(_STRIP_QUOTES)):
        tokens = piece.split()[:MAX_TOKENS]
        if tokens:
            sub = _Found()
            try:
                _analyze_tokens(tokens, sub, depth=0)
            except Exception:  # positives only: a failure here adds nothing and removes nothing
                continue
            found.true |= sub.true
    if _PIPE_TO_SHELL.search(text) or _PROCESS_SUBST_SHELL.search(text) or _IEX_DOWNLOAD.search(
        text
    ):
        found.hit("pipes_to_shell")
    if _HOME_VARS.search(text):
        found.hit("targets_home_directory")
    if _SYSTEM_VARS.search(text):
        found.hit("targets_root_or_system_path")
    if _RAW_ELEVATION.search(text):
        found.hit("uses_elevation")
    if _RAW_DOWNLOAD.search(text):
        found.hit("network_download")
    if _RAW_RECURSIVE.search(text):
        found.hit("recursive_delete")
    if _RAW_PERMISSIONS.search(text):
        found.hit("modifies_permissions")
    if _RAW_GIT_REWRITE.search(text):
        found.hit("git_history_rewrite")
    if re.search(r"(?:^|\s)(?:--force\b|-force\b)", text, re.IGNORECASE):
        found.hit("force_flag")


# cmd.exe built-ins that accept switches glued to the name (`rd/s/q`).
_CMD_BUILTINS = frozenset({"rd", "rmdir", "del", "erase", "copy", "move", "xcopy", "robocopy",
                           "attrib", "icacls", "takeown", "format", "start", "call", "cmd"})
_KNOWN_PROGRAMS = (
    _ELEVATION | _PLAIN_WRAPPERS | _INDIRECT | _POSIX_DELETE | _PS_DELETE | _CMD_DELETE
    | _DOWNLOADERS | _PERMISSIONS | _PS_COPY | _PM_DIRECT | _CMD_BUILTINS
    | frozenset(_PM_SUBCOMMANDS)
    | frozenset({"git", "find", "cp", "mv", "ln", "net", "certutil", "npx", "pnpx", "bunx",
                 "pacman", "uv", "uvx", "python", "python3", "py"})
)

_RUNNERS = frozenset({
    "py", "node", "nodejs", "deno", "bun", "bunx", "ruby", "perl", "php", "lua", "java",
    "dotnet", "go", "cargo", "rustc", "make", "gmake", "nmake", "just", "rake", "ant", "gradle",
    "gradlew", "mvn", "mvnw", "cmake", "ninja", "meson", "npm", "pnpm", "yarn", "npx", "pnpx",
    "uv", "uvx", "pipx", "poetry", "pdm", "hatch", "tox", "nox", "pytest", "jest", "vitest",
    "mocha", "docker", "podman", "kubectl", "helm", "terraform", "ansible", "ansible-playbook",
    "rscript", "julia", "swift", "kotlin", "scala", "groovy", "elixir", "mix", "erl", "tsx",
    "ts-node", "composer", "bundle", "gem", "conda", "mamba", "micromamba", "invoke", "task",
})
_SCRIPT_SUFFIXES = (".sh", ".bash", ".zsh", ".ps1", ".psm1", ".py", ".js", ".mjs", ".cjs",
                    ".ts", ".rb", ".pl", ".php", ".bat", ".cmd", ".vbs", ".jar", ".lua")
