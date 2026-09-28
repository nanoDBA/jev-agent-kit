# ADR 0005: Command properties computed in code

Status: proposed

## Context

COMMAND egress sends only the program name (ADR 0002, finding H16). Without a per-command
option grammar, a dashed word cannot be told apart from an option's argument
(`git -C --PRIVATE_CUSTOMER` passes that word to `-C` as a directory), so every argument is
dropped. That is safe but gives the gate little to work with: it sees `rm` for both `rm file`
and `rm -rf /`, and `git` for both `git status` and `git push --force`.

Non-negotiable 6 says to keep facts that code can compute in code and put the result in state.
Whether a command deletes recursively, runs elevated, or rewrites git history is such a fact
for common commands.

## Decision

The host hook core (`src/jev_kit/hooks/core.py`) runs a pure function,
`jev_kit.command_properties.extract_command_properties`, over the full command line before any
request is built. It returns a fixed set of tri-state properties. The hook puts them into state
as flat, keyed fields with a `cmd_` prefix. A question set sends them only if it declares them
with the new content kind `flag`. Jev judges these properties together with the program name
and never sees the raw arguments.

### Property set

| State field | True when (heuristic) |
| --- | --- |
| `cmd_parse_confident` | The line is a single simple command this parser can read. See "Unknown" below. |
| `cmd_recursive_delete` | `rm` with `-r`, `-R` or `--recursive`; `Remove-Item` or an alias (`rm`, `ri`, `del`, `erase`, `rd`, `rmdir`) with a prefix of `-Recurse`; cmd `del`, `erase`, `rd`, `rmdir` with `/s` (also `/S/Q` and `rd/s/q`); `find -delete`; `git clean -d`. |
| `cmd_force_flag` | `rm -f`; a prefix of `-Force` from `-fo` on for the delete, copy and move cmdlets; cmd `/q` or `/f` on the delete built-ins; `cp`, `mv`, `ln` with `-f`; any `--force` or `-Force` word; `git push -f` or `--force*`; `git clean -f`; `git checkout -f`. |
| `cmd_targets_root_or_system_path` | Any argument (including `--opt=value` and `-Param:value` values) is `/`, a glob at the top level (`/*`, `/e?c`), or under a system directory (`/etc`, `/usr`, `/var`, `/bin`, `/boot`, `/dev`, `/proc`, `/sys`, `/root`, `/opt`, macOS `/System`, `/Library` and others) after lexical `..` resolution; or a drive root (`C:`, `C:\`, `\`), `Windows`, `Program Files` (and 8.3 `PROGRA~1`), `ProgramData`, or any `System32` segment. In an unparsed line, `$env:SystemRoot`, `%windir%` and similar also count. |
| `cmd_targets_home_directory` | An argument is a home directory itself, a wildcard over it, or a hidden entry directly under it: `~`, `~/`, `~/*`, `~/.ssh`, `~user`, `/home/<user>`, `/Users/<user>/*`, `C:\Users\<user>`. In an unparsed line, `$HOME`, `${HOME}`, `$env:USERPROFILE`, `%USERPROFILE%` also count. |
| `cmd_uses_elevation` | The program, or a wrapper in front of it, is `sudo`, `doas`, `su`, `pkexec`, `runas`, `gsudo`, `run0`; or an unparsed line has `-Verb RunAs`. |
| `cmd_network_download` | `curl`, `wget`, `Invoke-WebRequest`/`iwr`, `Invoke-RestMethod`/`irm`, `Start-BitsTransfer`, `bitsadmin`, `aria2c`, `scp`, `rsync`, `sftp` and similar; `certutil -urlcache`; `git clone`, `fetch`, `pull`; any package install below. |
| `cmd_pipes_to_shell` | In a line that is already unparsed: a pipe into a shell or interpreter (`| sh`, `| sudo bash`, `| iex`, `| python3`), `bash <(...)`, or `iex` combined with a downloader (`DownloadString`, `iwr`). |
| `cmd_modifies_permissions` | `chmod`, `chown`, `chgrp`, `chattr`, `setfacl`, `icacls`, `cacls`, `takeown`, `attrib`, `Set-Acl`, `usermod`, `passwd`, `visudo`, `setcap`, `Add-LocalGroupMember`, `net localgroup`/`net user`, and similar. |
| `cmd_git_history_rewrite` | `git push` with a force option, `--mirror`, `--delete`, `-d`, `+refspec` or `:ref`; `reset --hard`/`--merge`/`--keep`; `rebase`; `filter-branch`, `filter-repo`, `replace`; `commit --amend`; `branch` or `tag` with `-D`, `-d`, `-M`, `-C`, `-f`; `reflog expire|delete`; `update-ref`; `stash drop|clear`; `gc --prune`, `prune`. |
| `cmd_package_install` | Install subcommands of npm, pnpm, yarn, bun, pip, pipx, uv, poetry, pdm, conda, gem, bundle, cargo, go, apt, apt-get, dnf, yum, zypper, apk, brew, snap, flatpak, choco, winget, scoop, composer, dotnet, nuget, helm, deno; `python -m pip install`; `npx`, `uvx`, `pnpx`, `bunx`; `pacman -S`/`-U`; `dpkg -i`; `rpm -i`/`-U`; `Install-Module`, `Install-Package`, `msiexec`. |

`writes_outside_workdir` was considered and left out: it needs the working directory, which the
hooks deliberately never send or use (folder names often name a client).

### How values are computed

- Each property is `true`, `false`, or `null` (unknown).
- Paths are judged where they land lexically: `.` and `..` are resolved for POSIX, tilde and
  drive-letter forms with either separator, so `/home/alice/project/..`, `~/project/../.ssh`
  and `C:\Users\Alice\project\..` count as home, and `/usr/../etc` and
  `C:\Windows\..\Windows` count as system paths. A tilde path is judged as a path under the
  home base. Symlinks are not resolved (no filesystem access). A relative path whose net climb
  is three or more levels, and a UNC path, make the parse not confident.
- WSL (`/mnt/c/...`), Cygwin (`/cygdrive/c/...`) and Git Bash (`/c/...`) spellings are read
  as the Windows drive, so `/mnt/c/Users/x` is home and `/c/Windows` is a system path.
- Every package install also sets `cmd_network_download`.
- Detection is biased toward `true`. Where a word could be a flag or an option's argument, it is
  read as the flag. That is exactly the ambiguity that forced H16, but here it is harmless: a
  wrong `true` adds friction, and only a wrong `false` could hide danger. Per-command knowledge
  is used only to read a few well-known flags of a few programs, never to decide that an
  argument is safe.
- A backslash can be a Windows path separator or a POSIX escape. Both readings are checked and
  the results are OR-ed: `r\m -rf x` counts as `rm`, `/e\tc` counts as `/etc`. The same goes
  for cmd's `rd/s/q` form, where the switches are glued to the name.
- Program names are case-folded and lose `.exe`, `.cmd`, `.bat`, `.com` and any directory, so
  `C:\tools\RM.EXE` and `/bin/rm` both read as `rm`.
- Wrappers that simply run the next word (`sudo`, `doas`, `env`, `nice`, `nohup`, `time`,
  `command`, `exec` and a few others) are looked through, up to a small depth. If the wrapper's
  next word is an option, its arity is unknown and the parse is not confident.
- Git global options are read with a closed list. `-c`, `--config-env` and `--exec-path` can
  define aliases, pagers, hooks or ssh commands that run anything, so they make the parse not
  confident. A git subcommand that git does not ship may be a user alias and is also not
  confident.

### Unknown

`cmd_parse_confident` is `false`, and every property not positively detected is `null`, when
the line has any of: control or redirect characters (`; & | < >`, backtick, newline), `$( )`,
quotes, variable expansion (`$`, `%`, `!`), grouping or braces, cmd's `^` escape and `,`
separator, a word starting with `@` (PowerShell splatting), non-ASCII characters (look-alike
letters, zero-width or non-breaking spaces), a glob in the program name, a UNC path, a relative
path that climbs three or more levels, more than 4096 characters or 128 words, an empty line;
or when the program runs something this parser cannot see (`bash`, `sh`, `pwsh`, `cmd`,
`eval`, `source`, `xargs`, `find -exec`, `ssh`, `timeout`, `Start-Process`, `iex`,
`Invoke-Command`, `python -c` and similar), or an assignment that cmd could read as a command
(`rd=/s`). An internal error in the extractor also gives "not confident", never `false`.

**The one rule: confident is an allowlist.** The parse is confident only when every program
in it is on the modeled allowlist and none of the conditions above applies. The allowlist is:
the delete, copy and move commands in the table (`rm`, `rmdir`, `unlink`, `Remove-Item` and
its aliases, `del`, `erase`, `rd`, `cp`, `mv`, `ln`, `Copy-Item`, `Move-Item`), the permission
commands in the table, the plain downloaders (`curl`, `wget`, `iwr`, `irm`), read-only
listing and text tools (`ls`, `dir`, `cat`, `type`, `echo`, `pwd`, `head`, `tail`, `wc`,
`grep`, `mkdir`, `touch`, `find` without `-exec`, `which`, `whoami`, `date`, `sort`, `uniq`,
`stat`, `tree`, `du`, `df`, `Get-ChildItem`, `Get-Content`, `Get-Location`, `Write-Output`,
`Select-String`), the wrappers `sudo`, `doas`, `gsudo`, `run0`, `env`, `nice`, `nohup`,
`time`, `command`, `builtin` around a modeled program, and git with a read-only or index-only
subcommand that runs no hook, updates no ref and changes no config (`status`, `log`, `diff`,
`show`, `rev-parse`, `ls-files`, `ls-tree`, `ls-remote`, `blame`, `grep`, `describe`,
`shortlog`, `show-ref`, `cat-file`, `for-each-ref`, `add`, `rm`, `mv`, `clean` and similar
inspection commands). Everything else is not confident, keeping any positive detections. In
particular: any program not listed (`evil`, `net`, `certutil`, `pip list`); a path-qualified
command word (`/tmp/evil`, `tools/evil`, `~/bin/evil`, `C:\tmp\evil.exe`, and even
`/bin/rm`, since the path may not hold what the name suggests); `exec`, `eval`, `source`,
`.` and `Import-Module`; git subcommands that run repository hooks or change refs or config
(`commit`, `checkout`, `switch`, `merge`, `pull`, `push`, `rebase`, `am`, `reset`, `branch`,
`tag`, `fetch`, `clone`, `stash`, `config`); other version control tools (`hg`, `svn`,
`fossil`, `bzr`), whose clone, checkout, pull and update also set `cmd_network_download`.

Examples of code this module cannot see, all not confident: interpreters and their script or
module operands (`python x.py`, `python -m mod`, `node`, `deno`, `bun`, `ruby`, `perl`, `php`,
`java`, `dotnet`), build tools and task runners (`make`, `just`, `cargo`, `go`, `gradle`,
`mvn`, `rake`), test runners (`pytest`, `jest`, `tox`), package managers and package runners
(`npm`, `pnpm`, `yarn`, `npx`, `uvx`, `pipx`, `uv`, `poetry`), container and cluster tools
(`docker`, `podman`, `kubectl`, `helm`, `terraform`), a local script (`./x`, `..\x`, or a
command word ending in `.sh`, `.ps1`, `.py`, `.js`, `.bat`, `.cmd` and similar), and every
package install, since installs run package scripts (postinstall, setup.py, maintainer
scripts). Positive detections for these still count.

For an unconfident line, pattern checks over the raw text and a per-piece analysis of the text
between control operators can still set properties to `true`; they never set `false`.

### Authority

- The properties are evidence. Nothing in this change can make a call ALLOW.
- In enforce, when `cmd_parse_confident` is `false`, the hook core returns ASK whatever the gate
  answered (reason `command_parse_unconfident`, unless the engine already failed with its own
  reason). The engine still runs so the decision leaves its receipt.
- In shadow, behavior is unchanged: the hook never changes host behavior.
- No rule forces ASK on a positive property in this change. The gate questions weigh them; a
  deterministic "ask on any of these" policy is an open question below.

### Egress

A new content kind `flag` is added to `src/jev_kit/egress.py`. A flag field passes a JSON
boolean or `null` unchanged and refuses anything else (`flag_not_boolean`): strings, numbers,
lists and objects block the whole request, so a flag field can never carry text even if a
caller puts text in it. `flag` is not a personal kind. The Tier 1 scan still runs over the
exact outgoing bytes.

The shipped `tool-call-gate` question set moves to version 2 and declares the eleven `cmd_*`
fields as `flag`. Its instructions tell Jev what the fields mean and that `null` may be true.
The `command` field still sends only the program name. A question set that does not declare the
fields drops them as undeclared, as for any other field.

Why booleans are safe egress: each field carries one bit about the shape of the command, chosen
by code from a fixed vocabulary. It carries no argument text, path, host, branch name or file
name. Eleven bits per call cannot carry a secret the way a raw argument can.

## Fingerprint impact

- `egress.py` changed, so `EGRESS_TRANSFORM_DIGEST` changed and every fingerprint in the kit
  moved, with or without a producer. The pinned value in
  `tests/test_engine.py::test_producer_free_fingerprints_match_the_previous_release` was
  updated on purpose. No calibrated thresholds exist yet, so nothing is lost; any made before
  this change would be invalidated.
- The hook producer identity now hashes `command_properties.py` along with the shim and the
  core, so any change to the extractor changes every gate fingerprint.
- The question-set version, schema and instructions changed, which changes gate fingerprints
  again.
- Open PR #16 (log templates) does not change `egress.py`, so this is the only pending change
  that moves the pinned producer-free fingerprint; merge order does not force a second re-pin.

## Consequences

- The gate can tell `rm file` from `rm -rf /`, and `git status` from `git push --force`,
  without any raw argument leaving the machine.
- More lines are "not confident" than are blocked by egress. In enforce those always ask. That
  is more friction for commands with variables or quoting, by design.
- The extractor is code that must be kept honest. Its table test lists positives and clean
  controls for each property on POSIX, PowerShell and cmd.

## Limits and residual risks

- Shell parsing here is heuristic. It is not a POSIX, PowerShell or cmd parser. Lines it
  cannot read are marked unknown rather than guessed, but a line it reads wrongly with
  confidence could produce a wrong `false`. The known shapes of that (escapes, glued switches,
  cmd separators, wrappers, aliases in git) are covered; others may exist.
- User-defined shell aliases and functions, `PATH` shadowing (a script named `ls` that deletes),
  git aliases in config files, and PowerShell profile functions are invisible to a pure
  function over the command text. `ll` could be anything.
- Relative paths are judged without the working directory. `rm -rf ..` is not flagged as the
  home directory even when the working directory is directly under home.
- A program outside the modeled allowlist is never confident, so a destructive tool the lists
  do not know (`shred`, `dd`, `mkfs`, `format`, a database CLI) gets `null` properties and, in
  enforce, always asks. The cost is friction: in enforce every unmodeled command asks,
  including host tool names such as `Edit` that a shim sends when a call has no command text.
- Modeled read-only git subcommands can still run programs through local repository config
  (`core.fsmonitor`, external diff drivers). Such config is not cloned, but a local attacker
  who can write `.git/config` is out of scope.
- A lone `/c` is not read as a Git Bash drive root, because it looks the same as a cmd switch.
- One bit per property per call is still data about the command. A compromised agent could in
  principle signal a few bits through its choice of flags; it could do far more by running the
  command.
- Compound, piped, quoted and escaped lines are still blocked by COMMAND egress (H16), so for
  those the properties never leave the machine; they only drive the local enforce ASK.

## Rejected alternatives

- **Per-command option grammars in egress** (keep known flags in the `command` string). Every
  program needs a correct arity table, a mistake leaks an argument value verbatim, and the
  table has to be reviewed as egress policy. Booleans computed from the same knowledge carry
  the signal without any chance of leaking a value.
- **Sending the raw command to Jev.** Violates ADR 0002 and non-negotiable 7.
- **Letting Jev count or parse flags.** Non-negotiable 6: parsing and literal matching are
  known weak spots; code does them.
- **Guessing `false` when unsure.** Absence is never approval; unknown is `null` and enforce
  asks.

## Open questions for the owner

1. Should enforce ASK deterministically when certain properties are true (for example
   `recursive_delete` with `targets_root_or_system_path`, or `pipes_to_shell`), regardless of
   the gate answer?
2. Is asking on every unconfident parse in enforce too much friction for commands that only use
   `$VAR` or quotes? A narrower rule is possible but would need its own review.
3. Should the property list grow (disk and filesystem tools such as `dd`, `mkfs`, `format`;
   database CLIs through an optional domain pack)?
4. Should `writes_outside_workdir` be added if a host can supply the working directory to the
   local extractor without sending it?
