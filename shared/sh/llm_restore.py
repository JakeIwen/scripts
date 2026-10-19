#!/usr/bin/python3
"""Reopen the Claude Code and Codex sessions that were open before a reboot.

`--snapshot` records the interactive sessions open now, per boot, and rewrites
an executable restore script (~/.local/state/llm-restore/restore) listing the
command that resumes each one. Running the tool with no flags executes that
script, which opens each session in a new terminal (tabs of one iTerm window
on macOS, a tmux session named `llmrestore` elsewhere) unless it is already
open. On the Mac, `--pi` reopens the Pi's sessions instead, as iTerm tabs
that SSH to the Pi: the Pi decides what to open, the Mac opens the tabs.

A boot's snapshot keeps each session for 24 hours after it was last seen
open, and a restore reopens those open or closed within 30 minutes
(`-m/--minutes` or `-H/--hours`) of the last snapshot, so the terminals
closing during shutdown (or by hand beforehand) don't empty the list. The restore list is the current boot's snapshot plus the previous
boot's last one. The previous boot's sessions are dropped from the list once a
restore has run, so sessions closed on purpose afterwards stay closed.

A managed crontab block snapshots every minute; it is tracked in
macbook/crontab and pi/crontab_pi, so reinstalling either keeps it.
`--cron off|on` pauses and resumes it, and reinstalls the block if a crontab
lost it; `--cron` alone prints the status. Standard library only; runs on the
MacBook (/usr/bin/python3 3.9) and the Pi.
"""
# codeshape: allow-size one file because the Pi runs it with `python3 -I` from
# ~/scripts, where a sibling module isn't importable; Jacob approved up to 1000 lines.

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
import fcntl
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("LLM_RESTORE_STATE", HOME / ".local/state/llm-restore"))
BOOTS = STATE / "boots"
RESTORE_SCRIPT = STATE / "restore"
CONSUMED = STATE / "consumed.json"
LAUNCHED = STATE / "launched.json"
LOCK = STATE / "lock"
CRON_LOG = STATE / "cron.log"
# --cron off pauses the crontab line rather than deleting it: the line is also
# in the tracked crontabs, so reinstalling one must not flip the switch.
CRON_OFF = STATE / "cron-off"
CLAUDE_SESSIONS = HOME / ".claude/sessions"
CLAUDE_PROJECTS = HOME / ".claude/projects"
CODEX_HOME = HOME / ".codex"
SWITCHBOARD_SESSIONS = HOME / ".config/llm-switchboard/sessions.json"
SWITCHBOARD_ENV = "LLM_SWITCHBOARD_SESSION"
TMUX_SESSION = "llmrestore"
# ssh's config picks the LAN address when the Pi answers there, else Tailscale.
PI_HOST = os.environ.get("LLM_RESTORE_PI_HOST", "vanpi")
PI_RESTORE_SCRIPT = "~/.local/state/llm-restore/restore"  # expanded by the Pi's shell
CRON_BEGIN = "# BEGIN managed llm-restore"
CRON_END = "# END managed llm-restore"
KEEP_BOOTS = 5
# A launched session normally registers within seconds; until then a second
# run must not open it again.
LAUNCH_GRACE_S = 120
# A restore also reopens sessions closed this long before the last snapshot
# (--minutes overrides it). Shutdown closes the terminals before cron stops,
# and sessions are often closed by hand before a reboot; without this, those
# snapshots would empty the list.
DEFAULT_MINUTES = 30
# Snapshots keep closed sessions this long, the furthest --minutes can reach.
RETAIN_S = 24 * 3600
ROLLOUT_RE = re.compile(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
IS_MAC = sys.platform == "darwin"
CLAUDE, CODEX = "claude", "codex"


@dataclass(frozen=True)
class Session:
    """One resumable session: the record in snapshots and the restore script."""

    tool: str  # CLAUDE or CODEX
    id: str
    cwd: str
    title: str
    command: str  # shell line that resumes it from any shell
    managed: str | None = None  # owner that relaunches it itself (llm-switchboard)
    last_seen: float = 0.0  # time of the last snapshot that found it open

    @classmethod
    def from_json(cls, data: dict) -> Session:
        return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})

    @property
    def key(self) -> str:
        return f"{self.tool}:{self.id}"

    @property
    def label(self) -> str:
        return f"{self.tool} {self.title or self.id}"


@dataclass(frozen=True)
class PlanItem:
    """A restore-list entry: the session and how long before its boot's last
    snapshot it was closed (0 if it was still open)."""

    session: Session
    closed_for: float

    @classmethod
    def from_json(cls, data: dict) -> PlanItem:
        return cls(Session.from_json(data["session"]), data["closed_for"])

    def describe(self) -> str:
        if not self.closed_for:
            return f"open at {_clock(self.session.last_seen)}"
        return f"closed {self.closed_for / 60:.0f}m before the last snapshot, last seen {_clock(self.session.last_seen)}"


@dataclass(frozen=True)
class RestoreOptions:
    dry_run: bool = False
    force: bool = False
    minutes: int = DEFAULT_MINUTES

    def flags(self) -> list[str]:
        """The same options as command-line flags, for the generated script."""
        return ((["--dry-run"] if self.dry_run else []) + (["--force"] if self.force else [])
                + ["--minutes", str(self.minutes)])


@dataclass
class Proc:
    pid: int
    argv: list[str]
    cwd: str | None = None
    env: dict = field(default_factory=dict)  # Linux only; macOS hides it
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CodexTui:
    pid: int
    cwd: str
    options: list[str]
    requested: str | None  # id named on `codex resume|fork <id>`


# ---------------------------------------------------------------- arguments

# Option tables: flag -> how many following words it takes.
FLAG, VALUE, MULTI, OPTIONAL = "flag", "value", "multi", "optional"


def _arity(arity: str, names: set) -> dict:
    return dict.fromkeys(names, arity)


# Claude Code options carried into the resume command. Every other option (the
# original --resume/--continue/--session-id, worktree/cloud modes) is dropped;
# its value, read as a positional word, goes with the discarded initial prompt.
CLAUDE_KEPT = {
    **_arity(VALUE, {
        "--permission-mode", "--model", "--settings", "--agent", "--agents", "--effort",
        "--fallback-model", "--setting-sources", "--append-system-prompt", "--system-prompt",
        "--autocompact", "--permission-prompt-tool", "--system-prompt-snapshot", "--plugin-dir",
        "--plugin-url", "--remote-control-session-name-prefix"}),
    **_arity(MULTI, {
        "--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools",
        "--disallowed-tools", "--tools", "--betas", "--mcp-config"}),
    **_arity(OPTIONAL, {"--remote-control"}),
    **_arity(FLAG, {
        "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--chrome",
        "--no-chrome", "--ide", "--verbose", "--strict-mcp-config", "--bare", "--safe-mode",
        "--restricted", "--brief", "--disable-slash-commands", "--ax-screen-reader"}),
}
# Codex keeps unknown options (they are boolean). The resume command runs in
# the thread's folder and names the thread, so the dropped ones would conflict.
CODEX_KEPT = _arity(VALUE, {
    "-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
    "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox",
    "-a", "--ask-for-approval", "--add-dir"})
CODEX_DROPPED = {
    **_arity(VALUE, {"-C", "--cd", "-i", "--image"}),
    **_arity(FLAG, {"--worktree", "--last", "--all", "--include-non-interactive"}),
}
CODEX_SESSION_SUBCOMMANDS = {"resume", "fork"}
CODEX_OTHER_SUBCOMMANDS = {
    "agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
    "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply",
    "a", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud",
    "exec-server", "features", "help", "mcp-server",
}


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def split_options(argv: list[str], kept: dict, dropped: dict, keep_unknown: bool) -> tuple[list[str], list[str]]:
    """Split argv[1:] into the options worth keeping and the positional words."""
    options, words, i = [], [], 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if not _is_flag(tok):
            words.append(tok)
            continue
        name, attached, _ = tok.partition("=")
        arity = FLAG if attached else kept.get(name) or dropped.get(name) or FLAG
        limit = {FLAG: 0, VALUE: 1, OPTIONAL: 1, MULTI: len(argv)}[arity]
        take = 0
        while take < limit and i + take < len(argv) and (arity == VALUE or not _is_flag(argv[i + take])):
            take += 1
        if name in kept or (keep_unknown and name not in dropped):
            options += argv[i - 1:i + take]
        i += take
    return options, words


def claude_options(argv: list[str]) -> list[str]:
    """Options from a running claude's argv worth keeping on resume."""
    return split_options(argv, CLAUDE_KEPT, {}, keep_unknown=False)[0]


def codex_tui(proc: Proc) -> CodexTui | None:
    """The interactive view of a codex process, or None for daemons and exec."""
    options, words = split_options(proc.argv, CODEX_KEPT, CODEX_DROPPED, keep_unknown=True)
    if (words and words[0] in CODEX_OTHER_SUBCOMMANDS) or not proc.cwd:
        return None
    requested = words[1] if len(words) > 1 and words[0] in CODEX_SESSION_SUBCOMMANDS else None
    return CodexTui(proc.pid, proc.cwd, options, requested)


def shell_command(cwd: str, words: list[str]) -> str:
    # `command` bypasses shell wrappers (the zsh `codex` function adds a sandbox).
    return f"cd -- {shlex.quote(cwd)} && command " + " ".join(shlex.quote(w) for w in words)


# ---------------------------------------------------------------- processes

def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _mac_argv(pid: int) -> list[str] | None:
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libc.dylib")
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:]
    rest = rest[rest.index(b"\0"):].lstrip(b"\0")  # skip the exec path
    return [a.decode(errors="replace") for a in rest.split(b"\0")[:argc]]


def _mac_processes() -> list[Proc]:
    procs = {}
    for line in _run(["/bin/ps", "-axo", "pid=,comm="]).stdout.splitlines():
        pid, _, comm = line.strip().partition(" ")
        if os.path.basename(comm.strip()) in (CLAUDE, CODEX):
            argv = _mac_argv(int(pid))
            if argv:
                procs[int(pid)] = Proc(int(pid), argv)
    if not procs:
        return []
    out = _run(["/usr/sbin/lsof", "-n", "-P", "-w", "-Fpfn", "-p", ",".join(map(str, procs))], check=False).stdout
    proc = fd = None
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            proc = procs.get(int(val))
        elif tag == "f":
            fd = val
        elif tag == "n" and proc:
            if fd == "cwd":
                proc.cwd = val
            else:
                proc.files.append(val)
    return list(procs.values())


def _linux_processes() -> list[Proc]:
    procs = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() not in (CLAUDE, CODEX):
                continue
            argv = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
            environ = (entry / "environ").read_bytes().decode(errors="replace").split("\0")
            proc = Proc(int(entry.name), argv, os.readlink(entry / "cwd"),
                        dict(kv.split("=", 1) for kv in environ if "=" in kv))
            for fd in (entry / "fd").iterdir():
                try:
                    proc.files.append(os.readlink(fd))
                except OSError:
                    pass
        except OSError:
            continue  # exited, or not ours
        procs.append(proc)
    return procs


def _tool(proc: Proc) -> str:
    return os.path.basename(proc.argv[0])


# ---------------------------------------------------------------- sessions

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def claude_sessions(procs: list[Proc]) -> list[Session]:
    found = []
    for p in procs:
        if _tool(p) != CLAUDE:
            continue
        meta = _read_json(CLAUDE_SESSIONS / f"{p.pid}.json", {})
        if meta.get("kind") != "interactive" or meta.get("entrypoint", "cli") != "cli":
            continue  # print mode, SDK and IDE sessions restore themselves
        sid, cwd = meta.get("sessionId"), meta.get("cwd") or p.cwd
        if not sid or not cwd:
            continue
        found.append(Session(
            CLAUDE, sid, cwd, meta.get("name") or "",
            shell_command(cwd, [CLAUDE, *claude_options(p.argv), "--resume", sid]),
            "llm-switchboard" if p.env.get(SWITCHBOARD_ENV) else None,
        ))
    return found


def _codex_threads() -> dict[str, dict]:
    """Thread id -> {cwd, title, updated} from Codex's state database."""
    dbs = sorted(CODEX_HOME.glob("state_*.sqlite"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
    if not dbs:
        return {}
    # mode=ro sees committed WAL pages; immutable is the fallback where the
    # -shm file can't be created (sandboxed shells).
    for uri in (f"file:{dbs[-1]}?mode=ro", f"file:{dbs[-1]}?immutable=1"):
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
                rows = db.execute("select id, cwd, coalesce(name, title, ''), updated_at from threads").fetchall()
            return {r[0]: {"cwd": r[1], "title": r[2], "updated": r[3] or 0} for r in rows}
        except sqlite3.Error:
            continue
    return {}


def assign_codex_threads(tuis: list[CodexTui], held: dict[int, set], threads: dict[str, dict]) -> dict[int, str]:
    """Map each TUI pid to the thread it shows.

    An embedded TUI holds its own rollout open. A TUI attached to the shared
    app-server daemon doesn't, so it is matched to a daemon-held thread by the
    id on its command line, then by folder, then (when exactly one of each is
    left) by elimination. The most recently updated thread wins a folder tie.
    """
    def recency(tid):
        return threads.get(tid, {}).get("updated", 0)

    def by_request(t, tid):
        return t.requested == tid

    def by_folder(t, tid):
        return threads.get(tid, {}).get("cwd") == t.cwd

    tui_pids = {t.pid for t in tuis}
    result, pool = {}, set()
    for pid, ids in held.items():
        if pid in tui_pids and ids:
            result[pid] = max(ids, key=recency)
        elif pid not in tui_pids:
            pool |= ids
    pool -= set(result.values())
    pending = [t for t in tuis if t.pid not in result]
    for matches in (by_request, by_folder):
        for t in list(pending):
            hits = [tid for tid in pool if matches(t, tid)]
            if hits:
                result[t.pid] = max(hits, key=recency)
                pool.discard(result[t.pid])
                pending.remove(t)
    if len(pending) == 1 and len(pool) == 1:
        result[pending[0].pid] = pool.pop()
    return result


def codex_sessions(procs: list[Proc]) -> tuple[list[Session], list[str]]:
    tuis, held = [], {}
    for p in procs:
        if _tool(p) != CODEX:
            continue
        for f in p.files:
            m = ROLLOUT_RE.search(f)
            if m and f.startswith(str(CODEX_HOME / "sessions")):
                held.setdefault(p.pid, set()).add(m.group(1))
        tui = codex_tui(p)
        if tui:
            tuis.append(tui)
    if not tuis:
        return [], []
    threads = _codex_threads()
    mapping = assign_codex_threads(tuis, held, threads)
    found, warnings = [], []
    for t in tuis:
        tid = mapping.get(t.pid)
        if not tid:
            warnings.append(f"codex pid {t.pid} in {t.cwd}: could not tell which session it shows")
            continue
        info = threads.get(tid, {})
        cwd = info.get("cwd") or t.cwd
        found.append(Session(CODEX, tid, cwd, info.get("title") or "",
                             shell_command(cwd, [CODEX, *t.options, "resume", tid])))
    return found, warnings


def live_sessions() -> tuple[list[Session], list[str]]:
    procs = _mac_processes() if IS_MAC else _linux_processes()
    codex, warnings = codex_sessions(procs)
    return claude_sessions(procs) + codex, warnings


# ---------------------------------------------------------------- state

def boot_id() -> str:
    if IS_MAC:
        out = _run(["/usr/sbin/sysctl", "-n", "kern.boottime"]).stdout
        return "mac-" + re.search(r"sec = (\d+)", out).group(1)
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


@contextmanager
def state_lock(wait: bool = True):
    """Serialize snapshots and restores; yields False if busy and not waiting."""
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def boot_snapshots() -> list[dict]:
    """Saved snapshots, newest first, with Session records."""
    snaps = []
    for path in BOOTS.glob("*.json"):
        snap = _read_json(path, None)
        if snap:
            snaps.append({**snap, "sessions": [Session.from_json(s) for s in snap["sessions"]]})
    return sorted(snaps, key=lambda s: s["taken_at"], reverse=True)


def restore_plan(current_boot: str, snapshots: list[dict], consumed: list[str]) -> tuple[list[PlanItem], list[str]]:
    """Sessions to restore (any age; a restore filters by --minutes) and the
    boots they came from."""
    sources = [s for s in snapshots if s["boot"] == current_boot][:1]
    earlier = [s for s in snapshots if s["boot"] != current_boot]
    if earlier and earlier[0]["boot"] not in consumed:
        sources.append(earlier[0])
    plan, seen = [], set()
    for snap in sources:
        for s in snap["sessions"]:
            if s.key not in seen and not s.managed:
                seen.add(s.key)
                plan.append(PlanItem(s, snap["taken_at"] - s.last_seen))
    return plan, [s["boot"] for s in sources]


def write_restore_script(plan: list[PlanItem], sources: list[str], warnings: list[str]) -> None:
    lines = [
        "#!/bin/sh",
        f"# Generated by {Path(__file__).resolve()} on {os.uname().nodename}, {time.strftime('%Y-%m-%d %H:%M:%S')}.",
        f"# Reopens these sessions if open, or closed under -m/-H (default {DEFAULT_MINUTES} minutes)",
        "# before the last snapshot; any that are already open are skipped. Flags:",
        "# --dry-run shows what would happen, --force ignores the 2-minute guard",
        "# against reopening a session that was just launched.",
    ]
    lines += [f"#   {i.session.tool:6} {i.session.title or i.session.id}  ({i.describe()})\n#     {i.session.command}"
              for i in plan]
    lines += [f"# warning: {w}" for w in warnings]
    if not plan:
        lines.append("#   (no sessions recorded)")
    lines += [
        f"exec /usr/bin/python3 -I -B {shlex.quote(str(Path(__file__).resolve()))} "
        f"--apply {shlex.quote(','.join(sources) or '-')} \"$@\" <<'SESSIONS'",
        *(json.dumps(asdict(i), sort_keys=True) for i in plan),
        "SESSIONS",
        "",
    ]
    _write_private(RESTORE_SCRIPT, "\n".join(lines), 0o700)


def merge_seen(previous: list[Session], live: list[Session], now: float) -> list[Session]:
    """This boot's sessions: open ones plus those closed under RETAIN_S ago."""
    merged = {s.key: s for s in previous if now - s.last_seen < RETAIN_S}
    merged.update((s.key, replace(s, last_seen=now)) for s in live)
    return list(merged.values())


def _clock(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def regenerate() -> list[PlanItem]:
    """Rewrite the restore script from the saved snapshots (lock held)."""
    current, snaps = boot_id(), boot_snapshots()
    plan, sources = restore_plan(current, snaps, _read_json(CONSUMED, []))
    warnings = next((s.get("warnings", []) for s in snaps if s["boot"] == current), [])
    write_restore_script(plan, sources, warnings)
    return plan


def snapshot(quiet: bool) -> int:
    with state_lock(wait=False) as locked:
        if not locked:
            return 0  # a restore is running; the next minute catches up
        live, warnings = live_sessions()
        boot, now = boot_id(), time.time()
        previous = next((s["sessions"] for s in boot_snapshots() if s["boot"] == boot), [])
        sessions = merge_seen(previous, live, now)
        _write_private(BOOTS / f"{boot}.json", json.dumps({
            "boot": boot, "taken_at": now,
            "sessions": [asdict(s) for s in sessions], "warnings": warnings}, indent=1))
        for old in sorted(BOOTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_BOOTS:]:
            old.unlink()
        plan = regenerate()
    if not quiet:
        recent = sum(i.closed_for <= DEFAULT_MINUTES * 60 for i in plan)
        print(f"Recorded {len(live)} open session(s); {RESTORE_SCRIPT} lists {recent} to restore "
              f"(+{len(plan) - recent} closed earlier, reachable with -m/-H)")
        for w in warnings:
            print(f"warning: {w}")
    return 0


# ---------------------------------------------------------------- restore

def _session_exists(s: Session) -> bool:
    if s.tool == CLAUDE:
        return any(CLAUDE_PROJECTS.glob(f"*/{s.id}.jsonl"))
    return any((CODEX_HOME / "sessions").glob(f"*/*/*/rollout-*-{s.id}.jsonl"))


def _switchboard_kept() -> set:
    entries = _read_json(SWITCHBOARD_SESSIONS, {}).get("sessions", {})
    return {sid for sid, e in entries.items() if isinstance(e, dict) and e.get("enabled")}


def choose(plan: list[Session], live: list[Session], launched: dict, force: bool,
           now: float) -> tuple[list[Session], list[str]]:
    """Split the plan into sessions to open and reasons for skipping the rest."""
    open_now = {s.key for s in live}
    kept = _switchboard_kept()
    todo, skipped = [], []
    for s in plan:
        if s.key in open_now:
            skipped.append(f"{s.label}: already open")
        elif s.id in kept:
            skipped.append(f"{s.label}: kept running by llm-switchboard")
        elif not force and now - launched.get(s.key, 0) < LAUNCH_GRACE_S:
            skipped.append(f"{s.label}: launched under {LAUNCH_GRACE_S}s ago (--force to reopen)")
        elif not _session_exists(s):
            skipped.append(f"{s.label}: transcript not found")
        else:
            todo.append(s)
    return todo, skipped


ITERM_SCRIPT = """
on run argv
  tell application "iTerm"
    activate
    set w to (create window with default profile)
    set isFirst to true
    repeat with c in argv
      if isFirst then
        set isFirst to false
        set s to current session of w
      else
        tell w to set t to (create tab with default profile)
        set s to current session of t
      end if
      tell s to write text (c as text)
    end repeat
  end tell
end run
"""


def open_iterm_tabs(commands: list[str]) -> str:
    _run(["/usr/bin/osascript", "-e", ITERM_SCRIPT, *commands])
    return "Opened as tabs of a new iTerm window."


def open_terminals(sessions: list[Session]) -> str:
    """Start each session's command in its own tab or window; returns a hint."""
    if IS_MAC:
        return open_iterm_tabs([s.command for s in sessions])
    for s in sessions:
        exists = _run(["tmux", "has-session", "-t", f"={TMUX_SESSION}"], check=False).returncode == 0
        where = ["new-window", "-d", "-t", f"={TMUX_SESSION}:"] if exists else ["new-session", "-d", "-s", TMUX_SESSION]
        name = (s.title or s.id[:8])[:24]
        pane = _run(["tmux", *where, "-n", name, "-c", s.cwd, "-P", "-F", "#{pane_id}"]).stdout.strip()
        _run(["tmux", "send-keys", "-t", pane, "-l", s.command])
        _run(["tmux", "send-keys", "-t", pane, "Enter"])
    return f"Attach with: tmux attach -t {TMUX_SESSION}"


def apply(sources: str, opts: RestoreOptions, emit: bool) -> int:
    """Reopen the sessions the generated restore script feeds on stdin.

    With `emit` (used by the Mac's --pi over SSH) the sessions to open are
    printed as JSON lines instead, and the report goes to stderr.
    """
    items = [PlanItem.from_json(json.loads(line)) for line in sys.stdin if line.strip()]
    report = sys.stderr if emit else sys.stdout
    in_window = {i.session.key: i for i in items if i.closed_for <= opts.minutes * 60}
    plan = [i.session for i in in_window.values()]
    with state_lock():
        live, _ = live_sessions()
        now = time.time()
        launched = {k: t for k, t in _read_json(LAUNCHED, {}).items() if now - t < LAUNCH_GRACE_S}
        todo, skipped = choose(plan, live, launched, opts.force, now)
        for line in skipped:
            print(f"skip  {line}", file=report)
        for s in todo:
            print(f"{'would open' if opts.dry_run else 'open'}  {s.label}  ({in_window[s.key].describe()})\n"
                  f"      {s.command}", file=report)
        if len(items) > len(plan):
            print(f"({len(items) - len(plan)} closed more than {opts.minutes} minutes before the last snapshot; "
                  f"widen with -m or -H)", file=report)
        if opts.dry_run:
            return 0
        if todo:
            # Recorded first, so a launch that fails part-way can't be doubled
            # by an immediate rerun before its sessions register.
            _write_private(LAUNCHED, json.dumps({**launched, **{s.key: now for s in todo}}))
            if emit:
                print("\n".join(json.dumps(asdict(s)) for s in todo))
            else:
                print(open_terminals(todo))
        else:
            print("Nothing to reopen." if items else "No sessions recorded. Turn on recording with: llmrestore --cron on",
                  file=report)
        # The previous boot's list has been restored: drop it from later runs.
        consumed = _read_json(CONSUMED, [])
        consumed += [b for b in sources.split(",") if b not in ("-", boot_id(), *consumed)]
        _write_private(CONSUMED, json.dumps(consumed[-20:]))
        regenerate()
    return 0


def pi_tab_command(command: str) -> str:
    """Local shell line that runs a Pi session's command over SSH, leaving a
    Pi shell open afterwards like the SSH tab it originally ran in."""
    remote = f"bash -lic {shlex.quote(command + '; exec bash -il')}"
    return f"ssh -t {shlex.quote(PI_HOST)} {shlex.quote(remote)}"


def restore_pi(opts: RestoreOptions) -> int:
    """Ask the Pi which of its sessions to reopen, then open them as iTerm tabs."""
    if not IS_MAC:
        raise SystemExit("--pi opens iTerm tabs on the Mac; on the Pi, run llmrestore without it")
    flags = ["--emit", *opts.flags()]
    # stderr (the Pi's report) passes straight through to this terminal.
    res = subprocess.run(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", PI_HOST,
                          " ".join([PI_RESTORE_SCRIPT, *flags])], stdout=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"Pi restore failed (ssh {PI_HOST} exited {res.returncode}); nothing opened", file=sys.stderr)
        return res.returncode
    sessions = [Session.from_json(json.loads(line)) for line in res.stdout.splitlines() if line.strip()]
    if sessions:
        print(open_iterm_tabs([pi_tab_command(s.command) for s in sessions]) + f" (ssh {PI_HOST})")
    return 0


def run_restore(opts: RestoreOptions) -> int:
    if not RESTORE_SCRIPT.exists():
        with state_lock():
            regenerate()
    os.execv(str(RESTORE_SCRIPT), [str(RESTORE_SCRIPT), *opts.flags()])


# ---------------------------------------------------------------- cron

def _crontab() -> list[str]:
    res = _run(["crontab", "-l"], check=False)
    if res.returncode != 0:
        if "no crontab" in res.stderr.lower():
            return []
        raise SystemExit(f"crontab -l failed: {res.stderr.strip()}")
    return res.stdout.splitlines()


def cron_block(tool: str = str(Path(__file__).resolve()), log: str = str(CRON_LOG)) -> list[str]:
    """The managed block, also copied verbatim into macbook/crontab and pi/crontab_pi.

    It creates the state directory itself: on a fresh install the log redirect
    would otherwise fail before the tool ever ran.
    """
    state = shlex.quote(str(Path(log).parent))
    return [
        CRON_BEGIN,
        f"* * * * * /bin/mkdir -p -m 700 {state} && "
        f"/usr/bin/python3 -I -B {shlex.quote(tool)} --snapshot --scheduled >> {shlex.quote(log)} 2>&1",
        CRON_END,
    ]


def cron(state: str | None, minutes: int) -> int:
    lines = _crontab()
    if CRON_BEGIN in lines and CRON_END in lines:
        start, end = lines.index(CRON_BEGIN), lines.index(CRON_END) + 1
    else:
        start = end = len(lines)
    installed = lines[start:end]
    if state == "off":
        _write_private(CRON_OFF, "")
    elif state == "on":
        if installed != cron_block():
            installed = cron_block()  # in place, so the live file keeps the tracked order
            subprocess.run(["crontab", "-"], input="\n".join(lines[:start] + installed + lines[end:]) + "\n",
                           text=True, check=True)
        CRON_OFF.unlink(missing_ok=True)
        snapshot(quiet=True)
    print(f"Snapshot cron: {'OFF (paused; --cron on resumes it)' if CRON_OFF.exists() else 'ON (every minute)'}")
    if installed != cron_block():
        print(f"  (the crontab's llm-restore line is {'out of date' if installed else 'missing'}; --cron on fixes it)")
    snaps = boot_snapshots()
    if snaps:
        last = snaps[0]
        still_open = sum(s.last_seen == last["taken_at"] for s in last["sessions"])
        print(f"Last snapshot: {_clock(last['taken_at'])}, {still_open} session(s) open")
    plan, _ = restore_plan(boot_id(), snaps, _read_json(CONSUMED, []))
    recent = sum(i.closed_for <= minutes * 60 for i in plan)
    print(f"Restore list: {recent} session(s) open or closed within {minutes} minutes of the last snapshot "
          f"({len(plan) - recent} older, up to {RETAIN_S // 3600}h) in {RESTORE_SCRIPT}")
    if CRON_LOG.exists() and CRON_LOG.stat().st_size:
        print(f"Errors were logged to {CRON_LOG}")
    return 0


def _window(minutes_per_unit: int):
    """argparse type: a --minutes/--hours value, as whole minutes."""
    def parse(text: str) -> int:
        value = round(float(text) * minutes_per_unit)
        if not 0 <= value <= RETAIN_S // 60:
            raise argparse.ArgumentTypeError(f"must be within {RETAIN_S // 3600}h (how long snapshots keep closed sessions)")
        return value
    return parse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cron", nargs="?", const="status", choices=["on", "off", "status"],
                    help="turn the every-minute snapshot on/off; alone, print its status")
    ap.add_argument("--snapshot", action="store_true", help="record the sessions open now")
    ap.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)  # the crontab line
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what a restore would open")
    ap.add_argument("--force", action="store_true", help="reopen sessions launched in the last 2 minutes")
    window = ap.add_mutually_exclusive_group()
    window.add_argument("-m", "--minutes", type=_window(1), default=DEFAULT_MINUTES,
                        help=f"also reopen sessions closed up to this long before the last snapshot "
                             f"(default {DEFAULT_MINUTES}, max {RETAIN_S // 60})")
    window.add_argument("-H", "--hours", dest="minutes", type=_window(60), metavar="HOURS",
                        help=f"the same, in hours (max {RETAIN_S // 3600})")
    ap.add_argument("--pi", action="store_true", help="on the Mac: reopen the Pi's sessions as iTerm tabs over SSH")
    ap.add_argument("--apply", metavar="BOOTS", help=argparse.SUPPRESS)  # used by the generated script
    ap.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)  # used by --pi over SSH
    args = ap.parse_args()
    if args.cron:
        return cron(None if args.cron == "status" else args.cron, args.minutes)
    if args.snapshot:
        if args.scheduled and CRON_OFF.exists():
            return 0
        return snapshot(quiet=args.scheduled)
    opts = RestoreOptions(args.dry_run, args.force, args.minutes)
    if args.apply is not None:
        return apply(args.apply, opts, args.emit)
    if args.pi:
        return restore_pi(opts)
    return run_restore(opts)


if __name__ == "__main__":
    sys.exit(main())
