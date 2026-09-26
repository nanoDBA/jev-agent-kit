"""Skill-library audit (P2-4, phase-2-plan.md D5 as amended).

Scans a skill directory (SKILL.md files and other skill text) for prompt-injection and
dangerous-command patterns, plus embedded secrets, and reports the hits as findings. This
module treats every byte it reads as untrusted data (CLAUDE.md non-negotiable 10): it never
executes, renders, imports, or follows anything it scans. A "dangerous command" match is a
string comparison against a regex, nothing more.

The injection and dangerous-command patterns below are authored fresh for this kit; they are
not copied from any other project (phase-2-plan.md D5 amendment: aleksvega is readme-only,
nothing to copy). Secret detection reuses `jev_kit.egress.scan_text` verbatim rather than
reimplementing it, so this module carries no Tier 1 detector logic of its own.

All regexes here are linear (no nested or overlapping quantifiers) and are run against
individual lines of bounded length, so a single match attempt cannot exhibit catastrophic
backtracking (ReDoS). `audit_path` additionally bounds the walk itself: files above
`MAX_FILE_BYTES` are skipped rather than read, and the walk stops after `MAX_FILES` files.
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
# Authored fresh for jev_agent_kit (phase-2-plan.md D5 amendment). Each entry is
# (rule_id, severity, pattern). Patterns are compiled once below. Every pattern is linear:
# alternation and bounded repetition only, no nested quantifiers, so matching a single line
# cannot blow up regardless of its content.

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

# category "dangerous_command": shell or code shapes that would be destructive, exfiltrate
# data, or hand off execution if a host ever ran them. Bounded to one recognizable shape each.
_DANGEROUS_RULES: list[_InjectionRule] = [
    (
        "dangerous_command.rm_rf",
        "high",
        re.compile(r"(?i)\brm\s+(?:-\w+\s+)*-[a-z]*r[a-z]*f[a-z]*\b"),
    ),
    (
        "dangerous_command.del_s",
        "high",
        re.compile(r"(?i)\bdel\s+/s\b"),
    ),
    (
        "dangerous_command.format_drive",
        "high",
        re.compile(r"(?i)\bformat\s+[a-z]:\b"),
    ),
    (
        "dangerous_command.curl_pipe_shell",
        "high",
        re.compile(r"(?i)\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b"),
    ),
    (
        "dangerous_command.invoke_expression",
        "high",
        re.compile(r"(?i)\b(?:invoke-expression|\biex)\b"),
    ),
    (
        "dangerous_command.base64_pipe_shell",
        "high",
        re.compile(r"(?i)\bbase64\s+-d\b[^\n|]{0,200}\|\s*(?:sh|bash)\b"),
    ),
    (
        "dangerous_command.chmod_777",
        "medium",
        re.compile(r"\bchmod\s+777\b"),
    ),
    (
        "dangerous_command.sudo",
        "medium",
        re.compile(r"(?<![\w-])sudo\s"),
    ),
    (
        "dangerous_command.overwrite_device",
        "high",
        re.compile(r">\s*/dev/sd[a-z]\b"),
    ),
    (
        "dangerous_command.fork_bomb",
        "high",
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    ),
]

# Combined so audit_text can iterate once; category is carried per rule tuple's rule_id prefix.
_TEXT_RULES: list[_InjectionRule] = [*_INJECTION_RULES, *_DANGEROUS_RULES]


def _category_for_rule_id(rule_id: str) -> str:
    return "injection" if rule_id.startswith("injection.") else "dangerous_command"


# --------------------------------------------------------------------------- scanning


def audit_text(text: str, path: str = "<text>") -> list[Finding]:
    """Scan a string for injection, dangerous-command, and secret patterns.

    The text is treated purely as data: it is matched against regexes line by line and never
    executed, imported, or rendered. Line numbers are 1-indexed.
    """
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule_id, severity, pattern in _TEXT_RULES:
            if pattern.search(line):
                findings.append(
                    Finding(
                        path=path,
                        line=line_no,
                        rule_id=rule_id,
                        severity=severity,
                        category=_category_for_rule_id(rule_id),
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
