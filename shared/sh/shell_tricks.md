# Shell tricks

13 tools plus `tricks` on each host. Mac = m4mac/Zsh; Pi = vanpi/Bash.
`tricks -l` lists the combined catalog, including tools on the other host.
All tools are local unless noted. `aftercmd` sends a small phone notification;
commands you ask it to run can of course use the network. Reading a mounted
share uses the LAN. Nothing installs software or runs commands when sourced.

## tricks
Hosts: Mac, Pi
Summary: Read this guide, list tools, or explain one tool.

Usage: `tricks`, `tricks -l`, `tricks NAME`

`tricks` prints this entire file. On a terminal it uses `less -FRX` if available:
F quits for a one-screen document, R permits color escapes, X leaves the output
on screen. Press `q` to leave the pager. Pipes and redirections always get plain
text. `tricks cshare` prints only that section, including host differences.
Unknown names print the valid names to stderr and return status 2.

The Mac reads `~/dev/scripts/shared/sh/shell_tricks.md`; the Pi reads
`~/scripts/shell_tricks.md`. Both use the same help file and
`shell_tricks_common.sh`; deploy all three Pi files together.

## croot
Hosts: Mac, Pi
Summary: Change to the current Git worktree's top-level directory.

Usage: `croot`

Uses `git rev-parse --show-toplevel`, then changes this shell's directory. In a
linked worktree it goes to that worktree, not the original checkout. Outside a
working tree it fails and leaves you where you were. No fetch or checkout.

## cproj
Hosts: Mac, Pi
Summary: Jump to a named directory directly under ~/dev.

Usage: `cproj [PROJECT]`

`cproj scripts` goes to `~/dev/scripts`; `cproj` goes to `~/dev`. Quote names with
spaces. Only one directory name is accepted, not a path, glob, or fuzzy match.
It does not clone a missing project, create a directory, or start an agent.

## cshare
Hosts: Mac, Pi
Summary: Change to the Mac/Pi agent file-exchange inbox.

Usage: `cshare`

Mac: goes to `/Volumes/pihome/Documents/agent-shared` only if pihome is an actual
SMB mount. A leftover directory under `/Volumes` does not count. It never mounts
anything or falls back to SSH/Tailscale. Connect the pihome share on the van LAN
first; away from the LAN this command intentionally fails.

Pi: goes to `~/Documents/agent-shared`, which is local there. On either host,
`cd from-agents` after `cshare` reaches Pi agents' outgoing files. No folders are
created, and a failed check/cd leaves your current directory unchanged.

## ctmp
Hosts: Mac, Pi
Summary: Create a private scratch directory and enter it.

Usage: `ctmp`

Runs `mktemp -d` under `$TMPDIR` (or `/tmp`), changes directory and prints its
path. Handy for a disposable Git reproduction or unpacking a trusted fixture
without cluttering a project. Names are unique and directories are owner-only.
It does not automatically delete anything. Keep work you need elsewhere; the
OS may clear temporary storage. To remove an empty scratch directory, first
leave it and use `rmdir` on its exact printed path.

## gsummary
Hosts: Mac, Pi
Summary: Show branch/status plus separate staged and unstaged diff sizes.

Usage: `gsummary`

Combines `git status --short --branch`, `git diff --stat`, and
`git diff --cached --stat` without a pager. In status's two columns, the left
column describes the index (staged) and the right the working tree (unstaged).
Untracked files appear as `??` but have no diff statistics. This is an overview,
not a replacement for reading the patch before a commit. No add/commit/push.

## gworktrees
Hosts: Mac, Pi
Summary: List this repository's worktree paths and checked-out branches.

Usage: `gworktrees`

Runs `git worktree list`. Useful when agent sessions work in sibling checkouts.
It lists registered worktrees, not which terminal/session owns them. It never
prunes, removes, or changes a worktree, even if one looks stale.

## gpickaxe
Hosts: Mac, Pi
Summary: Find commits that added or removed a literal string.

Usage: `gpickaxe LITERAL [PATHSPEC ...]`

Examples: `gpickaxe 'old_setting_name'`, `gpickaxe 'retry_count' '*.py'`

Uses Git's pickaxe, `git log --all -S...`, displaying one line per matching
commit. The search string is literal, not a regex. `-S` finds a change in the
number of occurrences: moving the same text within a file may not match.
`--all` searches all locally available refs, including existing remote-tracking
refs, without fetching. Optional arguments are Git pathspecs (quote globs).
Inspect a result with `git show COMMIT`. Large histories may take time.

## gignored
Hosts: Mac, Pi
Summary: Explain which Git ignore rule hides each supplied path.

Usage: `gignored PATH ...`

Example: `gignored build/output.log .agent/`

Runs `git check-ignore -v`, showing the ignore file, rule line, pattern and
path. Paths beginning with a dash are safe. No output and exit 1 means no
supplied untracked path matched; tracked files are not hidden by ignore rules.
A negated rule (`!pattern`) explains an explicit inclusion rather than an
ignored file. This does not edit any ignore file or untrack anything.

## aftercmd
Hosts: Mac, Pi
Summary: Run a command, send its exit status to your phone, and preserve it.

Usage: `aftercmd COMMAND [ARG ...]`

Examples: `aftercmd npm test`, `aftercmd python3 run_tests.py`

Runs the command in a subshell, then uses the existing phone-notification
script. Output stays in your terminal; the phone gets only `Shell task` and
`Finished (exit N).`, not arguments, paths, secrets, or captured output. Exit 0
means success. A failed notification warns locally but does not change the
wrapped command's status. Ctrl+C or a command that exits/execs the wrapping
shell may prevent the notification. This is not a background-job supervisor.

Arguments are passed literally, never evaluated as shell text. Executables and
functions work; aliases are not expanded after `aftercmd`. For a deliberate
pipeline, use `aftercmd bash -o pipefail -c 'producer | consumer'`. Directory
and variable changes inside the command do not affect your parent shell.

Network: sends a small alert, potentially over a metered connection. On the Mac,
the existing sender tries Pi LAN SSH first, then the auto-routed Pi SSH alias;
on the Pi it uses the installed ntfy sender. Delivery can wait for timeouts if
the Pi/network is offline. Nothing is sent merely by sourcing these tools.
Requires the existing `claude_ntfy_notify.py` installation and Python 3.

## pathlines
Hosts: Mac, Pi
Summary: Print PATH in search order, one directory per numbered line.

Usage: `pathlines`

Useful for nvm/Homebrew/system-tool confusion. The first matching executable
wins (shell aliases/functions can take precedence). Empty components are
labelled `. (current directory)`, which can unexpectedly let a local executable
shadow a system command. No PATH entries are added, removed, or deduplicated.
Use `whence -a NAME` on Zsh or `type -a NAME` on Bash to inspect one command.

## clipfile
Hosts: Mac
Summary: Copy a text file's exact contents to the macOS clipboard.

Usage: `clipfile READABLE_TEXT_FILE`

Example: `clipfile '/tmp/clip/message.txt'`

Equivalent to `pbcopy < file`, without a pipeline or command substitution that
might trim newlines or interpret quotes. Replaces the clipboard only when the
file can be opened. Text files only; this does not turn a file into a Finder
copy operation or copy an image as image data. Requires a macOS clipboard
session. No network.

## copypath
Hosts: Mac
Summary: Copy an existing path's absolute, symlink-resolved spelling.

Usage: `copypath [EXISTING_PATH]`

`copypath` copies the current directory; `copypath 'notes with spaces.txt'`
copies that file's canonical path, with no added newline. Zsh's `:A` modifier
makes the path absolute and resolves symlinks. Missing paths (including broken
symlinks) fail without replacing the clipboard. The result is plain text, not
shell-escaped text: quote it when inserting it into a shell command.

## reveal
Hosts: Mac
Summary: Select a file or directory in Finder without opening the file.

Usage: `reveal [EXISTING_PATH]`

`reveal` selects the current directory; `reveal report.pdf` selects that file.
Uses `open -R` with an absolute path, not Quick Look or a file's default app.
Requires a macOS desktop session. No automatic mount or network connection.

## junit
Hosts: Pi
Summary: Show a unit's recent journal from this boot, optionally following it.

Usage: `junit UNIT [LINES|follow]`

Examples: `junit cron.service`, `junit video-library.service 200`,
`junit van-dashboard.service follow`

Defaults to 100 lines; numeric limits are 1–10000. `follow` shows 100 then
streams new entries until Ctrl+C. Uses `journalctl --boot --unit=... --no-pager`,
so old-boot failures are not mixed with current ones. Specify the full unit
name; quote patterns if you intentionally want journalctl's unit globbing.
It does not run sudo or restart anything. Output is limited to journals your
user can read; lack of visible messages does not prove a service is healthy.
For per-user units use `journalctl --user -u UNIT -b` directly.

## failedunits
Hosts: Pi
Summary: List failed system units, or failed units in your user manager.

Usage: `failedunits [--user]`

`failedunits` runs `systemctl --failed --no-pager --full`; `failedunits --user`
checks the user manager (useful for user-owned agent services). Nothing is
restarted or reset. A stale failed state can survive the original incident;
inspect the unit's status/log before deciding what to do. A successful command
means the listing succeeded, not that the list is empty.

## pitherm
Hosts: Pi
Summary: Print the Pi's temperature and raw current/historical throttling mask.

Usage: `pitherm`

Calls `vcgencmd measure_temp` and `vcgencmd get_throttled`, read-only. A mask of
`0x0` means none of the reported flags are set. Decode a nonzero mask by OR-ing:

| Condition | Happening now | Happened since boot |
|---|---|---|
| Undervoltage | `0x1` | `0x10000` |
| ARM frequency capped | `0x2` | `0x20000` |
| Throttled | `0x4` | `0x40000` |
| Soft temperature limit | `0x8` | `0x80000` |

For example, `0x50000` records past undervoltage and throttling, not a current
fault. Historical bits are sticky until reboot; this command never clears them
or changes clocks, fans, power, or voltages. Requires the Pi's installed
`vcgencmd` firmware utility.
