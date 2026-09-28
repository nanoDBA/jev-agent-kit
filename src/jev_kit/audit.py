"""Skill-library audit (P2-4, phase-2-plan.md D5 as amended).

Scans a skill directory (SKILL.md files and other skill text) for prompt-injection and
dangerous-command patterns, plus embedded secrets, and reports the hits as findings. This
module treats every byte it reads as untrusted data (CLAUDE.md non-negotiable 10): it never
executes, renders, imports, or follows anything it scans. A "dangerous command" match is a
string/token comparison, nothing more.

The injection and dangerous-command patterns below are authored fresh for this kit; they are
not copied from any other project (phase-2-plan.md D5 amendment: aleksvega is readme-only,
nothing to copy). Secret detection reuses `jev_kit.egress.scan_text` verbatim rather than
reimplementing it, so this module carries no Tier 1 detector logic of its own.

The "injection" rules are plain bounded regexes (no nested or overlapping quantifiers), so a
single match attempt against one line cannot exhibit catastrophic backtracking (ReDoS). The
"dangerous_command" rules are matched with linear whitespace/token scanning instead of regex
alternation over unbounded attacker-controlled runs: each line is split into tokens once, and
every rule is a fixed-length prefix/equality check on individual tokens (H10). This keeps
scanning a single line O(len(line)) regardless of how an attacker shapes it, for example a
single ~100 KB flag string glued onto an `rm` invocation. `audit_path` additionally bounds the
walk itself: files above `MAX_FILE_BYTES` are skipped rather than read, and the walk stops after
`MAX_FILES` files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jev_kit.egress import scan_text

# --------------------------------------------------------------------------- bounds

MAX_FILE_BYTES = 512 * 1024  # 512 KiB per file
MAX_FILES = 2000  # total files scanned per audit_path call
_SCAN_SUFFIXES = frozenset({".md", ".txt", ".json"})


# --------------------------------------------------------------------------- findings


@dataclass(frozen=True)
class Finding:
    """One rule hit at one location. Data only: never executed, rendered, or followed."""

    path: str
    line: int
    rule_id: str
    severity: str
    category: str


def has_high_severity(findings: list[Finding]) -> bool:
    """True if any finding is high severity."""
    return any(finding.severity == "high" for finding in findings)


# --------------------------------------------------------------------------- rules
#
# Authored fresh for jev-agent-kit (phase-2-plan.md D5 amendment). Each injection-rule entry is
# (rule_id, severity, pattern), compiled once below. Every injection pattern is linear:
# alternation and bounded repetition only, no nested quantifiers, so matching a single line
# cannot blow up regardless of its content. The dangerous-command rules below are matched by
# token, not regex; see `_find_dangerous_commands`.

_InjectionRule = tuple[str, str, re.Pattern[str]]

# category "injection": phrases that try to override a host agent's instructions, extract its
# system prompt, or reframe its role. Kept small and documented; each bounded to one phrase
# shape so it cannot backtrack pathologically.
_INJECTION_RULES: list[_InjectionRule] = [
    (
        "injection.ignore_previous",
        "high",
        re.compile(r"(?i)\bignore\s+(?:all\s+)?previous\s+instructions\b"),
    ),
    (
        "injection.disregard_above",
        "high",
        re.compile(r"(?i)\bdisregard\s+the\s+above\b"),
    ),
    (
        "injection.role_takeover",
        "medium",
        re.compile(r"(?i)\byou\s+are\s+now\b"),
    ),
    (
        "injection.system_prompt_label",
        "medium",
        re.compile(r"(?i)\bsystem\s+prompt\s*:"),
    ),
    (
        "injection.reveal_prompt",
        "high",
        re.compile(r"(?i)\breveal\s+your\s+(?:system\s+)?prompt\b"),
    ),
    (
        "injection.exfiltrate",
        "high",
        re.compile(r"(?i)\bexfiltrate\b"),
    ),
    (
        "injection.override_guardrails",
        "high",
        re.compile(r"(?i)\boverride\s+your\s+(?:rules|guardrails)\b"),
    ),
    (
        "injection.act_as_system",
        "medium",
        re.compile(r"(?i)\bact\s+as\s+(?:the\s+)?system\b"),
    ),
]

# injection.credential_exfiltration (jak-aj7): an instruction to move a credential location
# off the machine. The literal-word rule above misses "send the contents of ~/.ssh to
# https://paste.example", so this rule needs, on the same line, BOTH a credential location AND
# an outbound action. The outbound action is either a network transfer tool (curl, scp, nc and
# similar), or a transfer verb (send, upload, post, copy, pipe, ...) together with a remote
# destination (a URL, webhook, paste site, or remote host). Requiring both halves keeps
# ordinary text clean: "rsync the build folder to the server" names no credential, and
# "configure your ~/.ssh/config Host alias" names no outbound action. Each pattern is a flat
# alternation with bounded, non-nested repetition, searched independently, so the pair stays
# linear in the line length. Pattern matching cannot catch every phrasing; this is a floor.
_CREDENTIAL_EXFIL_RULE_ID = "injection.credential_exfiltration"
_CREDENTIAL_EXFIL_SEVERITY = "high"

_CREDENTIAL_LOCATION_RE = re.compile(
    r"(?i)"
    # ~/.ssh and .env paths are handled by _SSH_PATH_RE and _ENV_FILE_RE below, which need
    # a per-path check (public keys and template env files are excluded).
    r"\bid_(?:rsa|dsa|ecdsa|ed25519)\b(?!\.pub)"
    r"|(?<![\w.])\.aws\b"
    r"|\baws_secret_access_key\b"
    r"|(?<![\w.])\.netrc\b"
    r"|(?<![\w.])\.git-credentials\b"
    r"|(?<![\w.])\.pgpass\b"
    r"|(?<![\w.])\.docker[/\\]config\.json\b"
    r"|(?<![\w.])\.kube[/\\]config\b"
    r"|\bgnupg\b"
    r"|\bkeychains?\b"
    r"|\bsecurity\s{1,8}find-(?:generic|internet)-password\b"
    r"|\bcredential\s{1,8}manager\b|\bcmdkey\b|\bvaultcmd\b"
    r"|\blogin\s{1,8}data\b|\bcookies\.sqlite\b|\blogins\.json\b|\bkey4\.db\b"
    r"|\bbrowser\s{1,8}(?:cookies|passwords|saved\s{1,8}passwords)\b"
)

_NETWORK_TOOL_RE = re.compile(
    r"(?i)(?<![\w-])(?:curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm|nc|ncat|netcat"
    r"|scp|sftp|rsync|ftp)(?![\w-])"
)

_TRANSFER_VERB_RE = re.compile(
    r"(?i)\b(?:send|sends|sending|upload|uploads|uploading|post|posts|posting|copy|copies"
    r"|copying|pipe|pipes|piping|forward|forwarding|transmit|transmitting|submit|submitting"
    r"|share|sharing|leak|leaking|email|mail|exfil\w{0,8})\b"
)

_REMOTE_DESTINATION_RE = re.compile(
    r"(?i)\bhttps?://"
    r"|\bwebhooks?\b"
    r"|\bpaste(?:bin|\.\w)"
    r"|\bgist\b"
    r"|\btransfer\.sh\b"
    r"|\bremote\s{1,8}(?:server|host|endpoint|machine|url)\b"
    r"|\b\w{1,32}@[\w.-]{1,253}:"
)


# A ~/.ssh path with up to 8 bounded segments (nested dirs and globs such as ~/.ssh/*.pub or
# ~/.ssh/work/id_ed25519.pub). The whole path is captured so its last segment can be checked.
_SSH_PATH_RE = re.compile(r"(?i)(?<![\w.])\.ssh((?:[/\\][\w.*?-]{1,64}){0,8})(?![\w-])")
_SSH_NON_SECRET_NAMES = frozenset({"config", "known_hosts", "known_hosts.old", "authorized_keys"})

# A .env file with any chain of up to 8 ".word" suffixes (.env.production.local). The chain is
# captured so template names can be excluded wherever they appear in it.
_ENV_FILE_RE = re.compile(r"(?i)(?<![\w.])\.env((?:\.[\w-]{1,32}){0,8})(?![\w-])")
_ENV_TEMPLATE_SEGMENTS = frozenset(
    {"example", "examples", "sample", "samples", "template", "tmpl", "dist", "defaults"}
)


def _ssh_path_is_secret(tail: str) -> bool:
    """True unless the ~/.ssh path names only a public key, config, or known_hosts file."""
    if not tail:
        return True  # the whole ~/.ssh directory
    last = tail.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return not (last.endswith(".pub") or last in _SSH_NON_SECRET_NAMES)


def _env_file_is_secret(chain: str) -> bool:
    """True unless a .env suffix chain names a template (.env.example, .env.local.sample)."""
    segments = {segment.lower() for segment in chain.split(".") if segment}
    return not segments & _ENV_TEMPLATE_SEGMENTS


def _has_credential_location(line: str) -> bool:
    """True if *line* names at least one private credential location."""
    if _CREDENTIAL_LOCATION_RE.search(line):
        return True
    if any(_ssh_path_is_secret(m.group(1)) for m in _SSH_PATH_RE.finditer(line)):
        return True
    return any(_env_file_is_secret(m.group(1)) for m in _ENV_FILE_RE.finditer(line))


def _is_credential_exfiltration(line: str) -> bool:
    """True if *line* names a credential location AND an outbound transfer to a remote place."""
    if not _has_credential_location(line):
        return False
    if _NETWORK_TOOL_RE.search(line):
        return True
    return bool(_TRANSFER_VERB_RE.search(line) and _REMOTE_DESTINATION_RE.search(line))


# category "dangerous_command": shell or code shapes that would be destructive, exfiltrate
# data, or hand off execution if a host ever ran them.
#
# H10: these used to be regexes with overlapping/nested repetition (for example
# `\brm\s+(?:-\w+\s+)*-[a-z]*r[a-z]*f[a-z]*\b`), which a crafted ~100 KB flag string could drive
# into quadratic scanning time. They are now matched by `_find_dangerous_commands` below, which
# tokenizes a line once (splitting on whitespace, with "|" and ">" pulled out as their own
# tokens) and matches fixed tokens or bounded prefixes -- equality and length-bounded prefix
# checks only, never a quantifier over an attacker-controlled run. Rule ids and severities are
# unchanged from the old regex table; kept here as the single source of truth for both.
_DANGEROUS_RULE_SEVERITIES: dict[str, str] = {
    "dangerous_command.rm_rf": "high",
    "dangerous_command.del_s": "high",
    "dangerous_command.format_drive": "high",
    "dangerous_command.curl_pipe_shell": "high",
    "dangerous_command.invoke_expression": "high",
    "dangerous_command.base64_pipe_shell": "high",
    "dangerous_command.chmod_777": "medium",
    "dangerous_command.sudo": "medium",
    "dangerous_command.overwrite_device": "high",
    "dangerous_command.fork_bomb": "high",
}

_SHELL_NAMES = ("sh", "bash", "zsh")

# The fork-bomb shape is a fixed sequence of literal characters separated by `\s*`. Every gap is
# bounded by the very next literal (none of `(`, `)`, `{`, `}`, `:`, `|`, `&`, `;` is whitespace),
# so there is no ambiguity between adjacent `\s*` groups for a backtracker to explore: this is a
# plain linear scan wearing a regex, not overlapping repetition, so it is kept as-is.
_FORK_BOMB_RE = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")


def _token_matches_word(token: str, word: str) -> bool:
    """True if *token* starts with *word* (case-insensitively) as a whole leading word.

    Bounded to `len(word)` characters (a small constant per call site) no matter how long
    *token* is: slicing `token[:len(word)]` copies only that prefix, so this is O(len(word)),
    never O(len(token)). Mirrors a regex `\\bword` match: the token boundary itself (whitespace,
    or the synthetic "|"/">" tokens from `_tokenize`) already stands in for the leading `\\b`, and
    the trailing character check stands in for the trailing `\\b` (a following letter/digit means
    it is a longer word, not this one).
    """
    prefix = token[: len(word)]
    if prefix.lower() != word:
        return False
    return len(token) == len(word) or not token[len(word)].isalnum()


def _token_is_rm_dangerous_flag(token: str) -> bool:
    """True if *token* is an `rm` short-option cluster naming both recurse and force.

    Covers "-rf", "-fr", and any combined cluster like "-arf" (letters only after a single
    leading "-", in either order), matching dangerous_command.rm_rf. Trailing punctuation (for
    example "-rf;" or "-rf)") is stripped first. Every step is bounded by *token*'s own length,
    so a single giant flag token costs O(len(token)) once, not more.
    """
    if len(token) < 2 or token[0] != "-" or token[1] == "-":
        return False
    body = token[1:]
    end = len(body)
    while end > 0 and not body[end - 1].isalpha():
        end -= 1
    body = body[:end]
    if not body or not body.isalpha():
        return False
    lowered = body.lower()
    return "r" in lowered and "f" in lowered


def _is_drive_letter_token(token: str) -> bool:
    """True if *token* names a Windows drive, e.g. "c:" or "C:\\Users" (format_drive rule)."""
    return len(token) >= 2 and token[0].isalpha() and token[1] == ":"


def _is_dev_sd_token(token: str) -> bool:
    """True if *token* is (or starts with) a `/dev/sd<letter>` block-device path."""
    prefix = "/dev/sd"
    return (
        len(token) > len(prefix)
        and token[: len(prefix)].lower() == prefix
        and token[len(prefix)].isalpha()
    )


def _shell_follows_pipe(tokens: list[str], pipe_index: int) -> bool:
    """True if the token(s) after the "|" at *pipe_index* name a shell, optionally via sudo."""
    j = pipe_index + 1
    if j < len(tokens) and _token_matches_word(tokens[j], "sudo"):
        j += 1
    if j >= len(tokens):
        return False
    return any(_token_matches_word(tokens[j], name) for name in _SHELL_NAMES)


def _tokenize(line: str) -> list[str]:
    """Split *line* into tokens, treating shell and structured-text punctuation as separators.

    A dangerous command embedded in Markdown or JSON (`` `rm -rf /` ``, `{"command":"rm -rf /"}`)
    must still tokenize to `rm`, `-rf`, `/` so the linear scan detects it (finding H10). "|" and
    ">" are kept as their own tokens for pipe/redirect rules. All work is str.translate plus
    split, each O(len(line)), so nothing can backtrack however the line is shaped.
    """
    spaced = line.replace("|", " | ").replace(">", " > ")
    # Surrounding punctuation from Markdown/JSON/quoting becomes whitespace so it cannot hide a
    # command token. Characters that are part of a command (- / . : _ = \) are preserved.
    spaced = spaced.translate({ord(ch): " " for ch in "`\"'{}[](),;"})
    return spaced.split()


def _find_dangerous_commands(line: str) -> set[str]:
    """Return the dangerous_command rule ids that match *line*, in O(len(line)) time.

    A single forward pass over the line's tokens: each rule keeps at most a boolean "have we
    seen the command word yet" flag and checks the *current* token against a fixed word or
    bounded prefix. There is no nested loop over the remaining tokens and no regex applied to
    the whole line, so repeating a token (many "rm"s) or inflating a single token (a 100 KB run
    of "-") each cost only what their own length adds to this one pass (H10).
    """
    tokens = _tokenize(line)
    n = len(tokens)
    hits: set[str] = set()

    seen_rm = False
    seen_del = False
    seen_format = False
    seen_curl_or_wget = False
    seen_base64_d = False

    for i, token in enumerate(tokens):
        if _token_matches_word(token, "rm"):
            seen_rm = True
        elif seen_rm and _token_is_rm_dangerous_flag(token):
            hits.add("dangerous_command.rm_rf")

        if _token_matches_word(token, "del"):
            seen_del = True
        elif seen_del and _token_matches_word(token, "/s"):
            hits.add("dangerous_command.del_s")

        if _token_matches_word(token, "format"):
            seen_format = True
        elif seen_format and _is_drive_letter_token(token):
            hits.add("dangerous_command.format_drive")

        if _token_matches_word(token, "chmod") and i + 1 < n and tokens[i + 1] == "777":
            hits.add("dangerous_command.chmod_777")

        if _token_matches_word(token, "sudo"):
            hits.add("dangerous_command.sudo")

        if _token_matches_word(token, "curl") or _token_matches_word(token, "wget"):
            seen_curl_or_wget = True

        if (
            _token_matches_word(token, "base64")
            and i + 1 < n
            and _token_matches_word(tokens[i + 1], "-d")
        ):
            seen_base64_d = True

        if _token_matches_word(token, "iex") or _token_matches_word(token, "invoke-expression"):
            hits.add("dangerous_command.invoke_expression")

        if token == ">" and i + 1 < n and _is_dev_sd_token(tokens[i + 1]):
            hits.add("dangerous_command.overwrite_device")

        if token == "|":
            if seen_curl_or_wget and _shell_follows_pipe(tokens, i):
                hits.add("dangerous_command.curl_pipe_shell")
            if seen_base64_d and _shell_follows_pipe(tokens, i):
                hits.add("dangerous_command.base64_pipe_shell")

    if _FORK_BOMB_RE.search(line):
        hits.add("dangerous_command.fork_bomb")

    return hits


# --------------------------------------------------------------------------- scanning


def audit_text(text: str, path: str = "<text>") -> list[Finding]:
    """Scan a string for injection, dangerous-command, and secret patterns.

    The text is treated purely as data: it is matched against regexes line by line and never
    executed, imported, or rendered. Line numbers are 1-indexed.
    """
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule_id, severity, pattern in _INJECTION_RULES:
            if pattern.search(line):
                findings.append(
                    Finding(
                        path=path,
                        line=line_no,
                        rule_id=rule_id,
                        severity=severity,
                        category="injection",
                    )
                )
        if _is_credential_exfiltration(line):
            findings.append(
                Finding(
                    path=path,
                    line=line_no,
                    rule_id=_CREDENTIAL_EXFIL_RULE_ID,
                    severity=_CREDENTIAL_EXFIL_SEVERITY,
                    category="injection",
                )
            )
        dangerous_hits = _find_dangerous_commands(line)
        for rule_id, severity in _DANGEROUS_RULE_SEVERITIES.items():
            if rule_id in dangerous_hits:
                findings.append(
                    Finding(
                        path=path,
                        line=line_no,
                        rule_id=rule_id,
                        severity=severity,
                        category="dangerous_command",
                    )
                )
        secret_rule_id = scan_text(line)
        if secret_rule_id is not None:
            findings.append(
                Finding(
                    path=path,
                    line=line_no,
                    rule_id=secret_rule_id,
                    severity="high",
                    category="secret",
                )
            )
    return findings


def audit_path(root: Path) -> list[Finding]:
    """Walk *.md, *.txt, and *.json files under root and scan each as untrusted data.

    Bounded: files larger than MAX_FILE_BYTES are skipped without being read into memory, and
    the walk stops after MAX_FILES files have been scanned. Nothing under root is executed,
    imported, or rendered; each file is read as text and matched against the same rules as
    audit_text.
    """
    findings: list[Finding] = []
    scanned = 0
    for candidate in sorted(root.rglob("*")):
        if scanned >= MAX_FILES:
            break
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        try:
            if candidate.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        rel_path = candidate.relative_to(root).as_posix()
        findings.extend(audit_text(text, path=rel_path))
    return findings
