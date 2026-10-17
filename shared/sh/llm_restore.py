#!/usr/bin/python3
"""Reopen the Claude Code and Codex sessions that were open before a reboot.

`--snapshot` records the interactive sessions open now, per boot, and rewrites
an executable restore script (~/.local/state/llm-restore/restore) listing the
command that resumes each one. Running the tool with no flags executes that
script, which opens each session in a new terminal (iTerm tabs on macOS, a
tmux session named `llmrestore` elsewhere) unless it is already open.

The restore list is the current boot's latest snapshot plus the previous
boot's last one. The previous boot's sessions are dropped from the list once a
restore has run, so sessions closed on purpose afterwards stay closed.

`--cron on|off` installs or removes a crontab block that snapshots every
minute; `--cron` alone prints the status. Standard library only; runs on the
MacBook (/usr/bin/python3 3.9) and the Pi.
"""

from __future__ import annotations

import argparse
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
CLAUDE_SESSIONS = HOME / ".claude/sessions"
CLAUDE_PROJECTS = HOME / ".claude/projects"
CODEX_HOME = HOME / ".codex"
SWITCHBOARD_SESSIONS = HOME / ".config/llm-switchboard/sessions.json"
SWITCHBOARD_ENV = "LLM_SWITCHBOARD_SESSION"
TMUX_SESSION = "llmrestore"
CRON_BEGIN = "# BEGIN managed llm-restore"
CRON_END = "# END managed llm-restore"
KEEP_BOOTS = 5
# A launched session normally registers within seconds; until then a second
# run must not open it again.
LAUNCH_GRACE_S = 120
ROLLOUT_RE = re.compile(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------- arguments

# Claude Code flags carried into the resume command. Anything else (the
# original --resume/--continue/--session-id, worktree/cloud modes, an initial
# prompt) is dropped, since the resume command supplies the session itself.
CLAUDE_KEEP_VALUE = {
    "--permission-mode", "--model", "--settings", "--agent", "--agents", "--effort",
    "--fallback-model", "--setting-sources", "--append-system-prompt", "--system-prompt",
    "--autocompact", "--permission-prompt-tool", "--system-prompt-snapshot", "--plugin-dir",
    "--plugin-url", "--remote-control-session-name-prefix",
}
CLAUDE_KEEP_MULTI = {
    "--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools",
    "--disallowed-tools", "--tools", "--betas", "--mcp-config",
}
CLAUDE_KEEP_OPTIONAL = {"--remote-control"}
CLAUDE_KEEP_FLAG = {
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--chrome",
    "--no-chrome", "--ide", "--verbose", "--strict-mcp-config", "--bare", "--safe-mode",
    "--restricted", "--brief", "--disable-slash-commands", "--ax-screen-reader",
}
CLAUDE_DROP_VALUE = {
    "--session-id", "-n", "--name", "--debug-file", "--input-format", "--output-format",
    "--json-schema", "--max-budget-usd", "--permission-prompts", "--environment",
}
CLAUDE_DROP_MULTI = {"--file"}
CLAUDE_DROP_OPTIONAL = {
    "-r", "--resume", "-d", "--debug", "--from-pr", "--teleport", "--cloud", "-w",
    "--worktree", "--prompt-suggestions",
}

CODEX_VALUE = {
    "-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
    "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox",
    "-a", "--ask-for-approval", "--add-dir", "-C", "--cd", "-i", "--image",
}
# The resume command runs from the thread's folder and names the thread, so
# these would conflict with it.
CODEX_DROP = {"-C", "--cd", "-i", "--image", "--worktree", "--last", "--all", "--include-non-interactive"}
CODEX_SESSION_SUBCOMMANDS = {"resume", "fork"}
CODEX_OTHER_SUBCOMMANDS = {
    "agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
    "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply",
    "a", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud",
    "exec-server", "features", "help", "mcp-server",
}


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def claude_options(argv: list[str]) -> list[str]:
    """Options from a running claude's argv worth keeping on resume."""
    kept, i = [], 1
    while i < len(argv):
        tok = argv[i]
        i += 1
        if not _is_flag(tok):
            continue  # an initial prompt: never replay it
        name, eq, _ = tok.partition("=")
        if eq:
            if name in CLAUDE_KEEP_VALUE | CLAUDE_KEEP_MULTI | CLAUDE_KEEP_OPTIONAL:
                kept.append(tok)
            continue
        if tok in CLAUDE_KEEP_FLAG:
            kept.append(tok)
        elif tok in CLAUDE_KEEP_VALUE | CLAUDE_DROP_VALUE:
            if tok in CLAUDE_KEEP_VALUE and i < len(argv):
                kept += [tok, argv[i]]
            i += 1
        elif tok in CLAUDE_KEEP_MULTI | CLAUDE_DROP_MULTI:
            values = []
            while i < len(argv) and not _is_flag(argv[i]):
                values.append(argv[i])
                i += 1
            if tok in CLAUDE_KEEP_MULTI:
                kept += [tok, *values]
        elif tok in CLAUDE_KEEP_OPTIONAL | CLAUDE_DROP_OPTIONAL:
            value = []
            if i < len(argv) and not _is_flag(argv[i]):
                value = [argv[i]]
                i += 1
            if tok in CLAUDE_KEEP_OPTIONAL:
                kept += [tok, *value]
        # other flags (-c, --fork-session, --tmux, -p, ...) are dropped
    return kept


def codex_options(argv: list[str]) -> list[str] | None:
    """Global options from a codex TUI's argv, or None if it isn't a TUI."""
    kept, i, seen_command = [], 1, False
    while i < len(argv):
        tok = argv[i]
        i += 1
        if not _is_flag(tok):
            if not seen_command:
                seen_command = True
                if tok in CODEX_OTHER_SUBCOMMANDS:
                    return None
            continue  # subcommand, session id or prompt
        name, eq, _ = tok.partition("=")
        if eq:
            if name not in CODEX_DROP:
                kept.append(tok)
            continue
        if tok in CODEX_VALUE:
            if tok not in CODEX_DROP and i < len(argv):
                kept += [tok, argv[i]]
            i += 1
        elif tok not in CODEX_DROP:
            kept.append(tok)
    return kept


def codex_requested_id(argv: list[str]) -> str | None:
    """The session id named on a `codex resume|fork <id>` command line."""
    for i, tok in enumerate(argv[1:-1], 1):
        if tok in CODEX_SESSION_SUBCOMMANDS:
            nxt = argv[i + 1]
            return None if _is_flag(nxt) else nxt
    return None


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


def list_processes() -> list[dict]:
    """claude/codex processes: pid, argv, cwd, env (Linux only), open files."""
    procs = []
    if IS_MAC:
        out = _run(["/bin/ps", "-axo", "pid=,comm="]).stdout
        for line in out.splitlines():
            pid, _, comm = line.strip().partition(" ")
            if os.path.basename(comm.strip()) in ("claude", "codex"):
                argv = _mac_argv(int(pid))
                if argv:
                    procs.append({"pid": int(pid), "argv": argv, "env": {}, "cwd": None, "files": []})
        if procs:
            _mac_files(procs)
        return procs
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() not in ("claude", "codex"):
                continue
            argv = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
            env = dict(
                kv.split("=", 1) for kv in (entry / "environ").read_bytes().decode(errors="replace").split("\0") if "=" in kv
            )
            cwd = os.readlink(entry / "cwd")
            files = []
            for fd in (entry / "fd").iterdir():
                try:
                    files.append(os.readlink(fd))
                except OSError:
                    pass
        except OSError:
            continue  # exited, or not ours
        procs.append({"pid": int(entry.name), "argv": argv, "env": env, "cwd": cwd, "files": files})
    return procs


def _mac_files(procs: list[dict]) -> None:
    by_pid = {p["pid"]: p for p in procs}
    out = _run(["/usr/sbin/lsof", "-n", "-P", "-w", "-Fpfn", "-p", ",".join(map(str, by_pid))], check=False).stdout
    pid = fd = None
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid = int(val)
        elif tag == "f":
            fd = val
        elif tag == "n" and pid in by_pid:
            if fd == "cwd":
                by_pid[pid]["cwd"] = val
            else:
                by_pid[pid]["files"].append(val)


# ---------------------------------------------------------------- sessions

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def claude_sessions(procs: list[dict]) -> list[dict]:
    found = []
    for p in procs:
        if os.path.basename(p["argv"][0]) != "claude":
            continue
        meta = _read_json(CLAUDE_SESSIONS / f"{p['pid']}.json", {})
        if meta.get("kind") != "interactive" or meta.get("entrypoint", "cli") != "cli":
            continue  # print mode, SDK and IDE sessions restore themselves
        sid, cwd = meta.get("sessionId"), meta.get("cwd") or p["cwd"]
        if not sid or not cwd:
            continue
        found.append({
            "tool": "claude",
            "id": sid,
            "cwd": cwd,
            "title": meta.get("name") or "",
            "pid": p["pid"],
            "managed": "llm-switchboard" if p["env"].get(SWITCHBOARD_ENV) else None,
            "command": shell_command(cwd, ["claude", *claude_options(p["argv"]), "--resume", sid]),
        })
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
            with sqlite3.connect(uri, uri=True, timeout=2) as db:
                rows = db.execute("select id, cwd, coalesce(name, title, ''), updated_at from threads").fetchall()
            return {r[0]: {"cwd": r[1], "title": r[2], "updated": r[3] or 0} for r in rows}
        except sqlite3.Error:
            continue
    return {}


def _rollout_cwd(path: str) -> str | None:
    try:
        with open(path) as f:
            return json.loads(f.readline()).get("payload", {}).get("cwd")
    except (OSError, ValueError):
        return None


def assign_codex_threads(tuis: list[dict], held: dict[int, set], threads: dict[str, dict]) -> dict[int, str]:
    """Map each TUI pid to the thread it shows.

    An embedded TUI holds its own rollout open. A TUI attached to the shared
    app-server daemon doesn't, so it is matched to a daemon-held thread by the
    id on its command line, then by folder, then (when exactly one of each is
    left) by elimination. The most recently updated thread wins a folder tie.
    """
    tui_pids = {t["pid"] for t in tuis}
    result, pool = {}, set()
    for pid, ids in held.items():
        if pid in tui_pids and ids:
            result[pid] = max(ids, key=lambda i: threads.get(i, {}).get("updated", 0))
        elif pid not in tui_pids:
            pool |= ids
    pool -= set(result.values())
    pending = [t for t in tuis if t["pid"] not in result]
    for match in (
        lambda t, tid: codex_requested_id(t["argv"]) == tid,
        lambda t, tid: threads.get(tid, {}).get("cwd") == t["cwd"],
    ):
        for t in list(pending):
            hits = sorted((tid for tid in pool if match(t, tid)), key=lambda i: -threads.get(i, {}).get("updated", 0))
            if hits:
                result[t["pid"]] = hits[0]
                pool.discard(hits[0])
                pending.remove(t)
    if len(pending) == 1 and len(pool) == 1:
        result[pending[0]["pid"]] = pool.pop()
    return result


def codex_sessions(procs: list[dict]) -> tuple[list[dict], list[str]]:
    tuis, held, rollouts = [], {}, {}
    for p in procs:
        if os.path.basename(p["argv"][0]) != "codex":
            continue
        for f in p["files"]:
            m = ROLLOUT_RE.search(f)
            if m and f.startswith(str(CODEX_HOME / "sessions")):
                held.setdefault(p["pid"], set()).add(m.group(1))
                rollouts[m.group(1)] = f
        options = codex_options(p["argv"])
        if options is not None and p["cwd"]:
            tuis.append({**p, "options": options})
    if not tuis:
        return [], []
    threads = _codex_threads()
    mapping = assign_codex_threads(tuis, held, threads)
    found, warnings = [], []
    for t in tuis:
        tid = mapping.get(t["pid"])
        if not tid:
            warnings.append(f"codex pid {t['pid']} in {t['cwd']}: could not tell which session it shows")
            continue
        info = threads.get(tid, {})
        cwd = info.get("cwd") or _rollout_cwd(rollouts.get(tid, "")) or t["cwd"]
        found.append({
            "tool": "codex",
            "id": tid,
            "cwd": cwd,
            "title": info.get("title") or "",
            "pid": t["pid"],
            "managed": None,
            "command": shell_command(cwd, ["codex", *t["options"], "resume", tid]),
        })
    return found, warnings


def live_sessions() -> tuple[list[dict], list[str]]:
    procs = list_processes()
    codex, warnings = codex_sessions(procs)
    return claude_sessions(procs) + codex, warnings


# ---------------------------------------------------------------- state

def boot_id() -> str:
    if IS_MAC:
        out = _run(["/usr/sbin/sysctl", "-n", "kern.boottime"]).stdout
        return "mac-" + re.search(r"sec = (\d+)", out).group(1)
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _write_private(path: Path, text: str, mode: int = 0o600) -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class StateLock:
    def __init__(self, wait: bool = True):
        self.wait = wait

    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if self.wait else fcntl.LOCK_NB))
        except BlockingIOError:
            os.close(self.fd)
            return None
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def boot_snapshots() -> list[dict]:
    """Saved snapshots, newest first."""
    snaps = [_read_json(p, None) for p in BOOTS.glob("*.json")]
    return sorted((s for s in snaps if s), key=lambda s: s["taken_at"], reverse=True)


def restore_plan(current_boot: str, snapshots: list[dict], consumed: list[str]) -> tuple[list[dict], list[str]]:
    """Sessions to restore and the boots they came from."""
    sources = [s for s in snapshots if s["boot"] == current_boot][:1]
    earlier = [s for s in snapshots if s["boot"] != current_boot]
    if earlier and earlier[0]["boot"] not in consumed:
        sources.append(earlier[0])
    plan, seen = [], set()
    for snap in sources:
        for s in snap["sessions"]:
            key = (s["tool"], s["id"])
            if key not in seen and not s.get("managed"):
                seen.add(key)
                plan.append(s)
    return plan, [s["boot"] for s in sources]


def write_restore_script(plan: list[dict], sources: list[str], warnings: list[str]) -> None:
    host = os.uname().nodename
    lines = [
        "#!/bin/sh",
        f"# Generated by {Path(__file__).resolve()} on {host}, {time.strftime('%Y-%m-%d %H:%M:%S')}.",
        "# Reopens these sessions; any that are already open are skipped. Flags:",
        "# --dry-run shows what would happen, --force ignores the 2-minute guard",
        "# against reopening a session that was just launched.",
    ]
    lines += [f"#   {s['tool']:6} {s['title'] or s['id']}\n#     {s['command']}" for s in plan]
    lines += [f"# warning: {w}" for w in warnings]
    if not plan:
        lines.append("#   (no sessions recorded)")
    lines += [
        f"exec /usr/bin/python3 -I -B {shlex.quote(str(Path(__file__).resolve()))} "
        f"--apply {shlex.quote(','.join(sources) or '-')} \"$@\" <<'SESSIONS'",
        *(json.dumps(s, sort_keys=True) for s in plan),
        "SESSIONS",
        "",
    ]
    _write_private(RESTORE_SCRIPT, "\n".join(lines), 0o700)


def regenerate(warnings: list[str] | None = None) -> list[dict]:
    plan, sources = restore_plan(boot_id(), boot_snapshots(), _read_json(CONSUMED, []))
    if warnings is None:
        warnings = next((s.get("warnings", []) for s in boot_snapshots() if s["boot"] == boot_id()), [])
    write_restore_script(plan, sources, warnings)
    return plan


def snapshot(quiet: bool) -> int:
    with StateLock(wait=False) as held:
        if held is None:
            return 0  # a restore is running; the next minute catches up
        sessions, warnings = live_sessions()
        boot = boot_id()
        _write_private(BOOTS / f"{boot}.json", json.dumps(
            {"boot": boot, "taken_at": time.time(), "sessions": sessions, "warnings": warnings}, indent=1))
        for old in sorted(BOOTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_BOOTS:]:
            old.unlink()
        plan = regenerate(warnings)
    if not quiet:
        print(f"Recorded {len(sessions)} open session(s); {len(plan)} in {RESTORE_SCRIPT}")
        for w in warnings:
            print(f"warning: {w}")
    return 0


# ---------------------------------------------------------------- restore

def _session_exists(s: dict) -> bool:
    if s["tool"] == "claude":
        return any(CLAUDE_PROJECTS.glob(f"*/{s['id']}.jsonl"))
    return any((CODEX_HOME / "sessions").glob(f"*/*/*/rollout-*-{s['id']}.jsonl"))


def _switchboard_kept() -> set:
    entries = _read_json(SWITCHBOARD_SESSIONS, {}).get("sessions", {})
    return {sid for sid, e in entries.items() if isinstance(e, dict) and e.get("enabled")}


def choose(plan: list[dict], live: list[dict], launched: dict, force: bool, now: float) -> tuple[list[dict], list[str]]:
    """Split the plan into sessions to open and reasons for skipping the rest."""
    open_now = {(s["tool"], s["id"]) for s in live}
    kept = _switchboard_kept()
    todo, skipped = [], []
    for s in plan:
        key, label = (s["tool"], s["id"]), f"{s['tool']} {s['title'] or s['id']}"
        if key in open_now:
            skipped.append(f"{label}: already open")
        elif s["id"] in kept:
            skipped.append(f"{label}: kept running by llm-switchboard")
        elif not force and now - launched.get(f"{s['tool']}:{s['id']}", 0) < LAUNCH_GRACE_S:
            skipped.append(f"{label}: launched under {LAUNCH_GRACE_S}s ago (--force to reopen)")
        elif not _session_exists(s):
            skipped.append(f"{label}: transcript not found")
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


def open_terminals(sessions: list[dict]) -> str:
    """Start each session's command in its own terminal; returns a hint."""
    if IS_MAC:
        _run(["/usr/bin/osascript", "-e", ITERM_SCRIPT, *(s["command"] for s in sessions)])
        return "Opened in a new iTerm window."
    have = _run(["tmux", "has-session", "-t", f"={TMUX_SESSION}"], check=False).returncode == 0
    for s in sessions:
        name = (s["title"] or s["id"][:8])[:24]
        if have:
            cmd = ["tmux", "new-window", "-d", "-t", f"={TMUX_SESSION}:", "-n", name, "-c", s["cwd"], "-P", "-F", "#{pane_id}"]
        else:
            cmd = ["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-n", name, "-c", s["cwd"], "-P", "-F", "#{pane_id}"]
            have = True
        pane = _run(cmd).stdout.strip()
        _run(["tmux", "send-keys", "-t", pane, "-l", s["command"]])
        _run(["tmux", "send-keys", "-t", pane, "Enter"])
    return f"Attach with: tmux attach -t {TMUX_SESSION}"


def apply(sources: str, dry_run: bool, force: bool) -> int:
    plan = [json.loads(line) for line in sys.stdin if line.strip()]
    with StateLock():
        live, _ = live_sessions()
        now = time.time()
        launched = {k: t for k, t in _read_json(LAUNCHED, {}).items() if now - t < LAUNCH_GRACE_S}
        todo, skipped = choose(plan, live, launched, force, now)
        for line in skipped:
            print(f"skip  {line}")
        for s in todo:
            print(f"{'would open' if dry_run else 'open'}  {s['tool']} {s['title'] or s['id']}\n      {s['command']}")
        if dry_run:
            return 0
        if todo:
            hint = open_terminals(todo)
            launched.update({f"{s['tool']}:{s['id']}": now for s in todo})
            print(hint)
        elif not plan:
            print("No sessions recorded. Turn on recording with: llmrestore --cron on")
        else:
            print("Nothing to reopen.")
        _write_private(LAUNCHED, json.dumps(launched))
        # The previous boot's list has been restored: drop it from later runs.
        current = boot_id()
        consumed = _read_json(CONSUMED, [])
        consumed += [b for b in sources.split(",") if b not in ("-", current) and b not in consumed]
        _write_private(CONSUMED, json.dumps(consumed[-20:]))
        regenerate()
    return 0


def run_restore(dry_run: bool, force: bool) -> int:
    if not RESTORE_SCRIPT.exists():
        with StateLock():
            regenerate([])
    args = [str(RESTORE_SCRIPT)] + (["--dry-run"] if dry_run else []) + (["--force"] if force else [])
    os.execv(str(RESTORE_SCRIPT), args)


# ---------------------------------------------------------------- cron

def _crontab() -> list[str]:
    res = _run(["crontab", "-l"], check=False)
    if res.returncode != 0:
        if "no crontab" in res.stderr.lower():
            return []
        raise SystemExit(f"crontab -l failed: {res.stderr.strip()}")
    return res.stdout.splitlines()


def cron_block() -> list[str]:
    me = shlex.quote(str(Path(__file__).resolve()))
    return [
        CRON_BEGIN,
        f"* * * * * /usr/bin/python3 -I -B {me} --snapshot --quiet >> {shlex.quote(str(CRON_LOG))} 2>&1",
        CRON_END,
    ]


def cron(state: str | None) -> int:
    lines = _crontab()
    if CRON_BEGIN in lines and CRON_END in lines:
        start, end = lines.index(CRON_BEGIN), lines.index(CRON_END)
        rest, installed = lines[:start] + lines[end + 1:], lines[start:end + 1]
    else:
        rest, installed = lines, []
    if state is not None:
        new = rest + (cron_block() if state == "on" else [])
        if new != lines:
            STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, check=True)
        installed = cron_block() if state == "on" else []
        if state == "on":
            snapshot(quiet=True)
    print(f"Snapshot cron: {'ON (every minute)' if installed else 'OFF'}")
    if installed and installed != cron_block():
        print("  (installed line differs from this script's; run --cron on to update it)")
    snaps = boot_snapshots()
    if snaps:
        s = snaps[0]
        print(f"Last snapshot: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s['taken_at']))}, "
              f"{len(s['sessions'])} session(s) open")
    plan, sources = restore_plan(boot_id(), snaps, _read_json(CONSUMED, []))
    print(f"Restore list: {len(plan)} session(s) in {RESTORE_SCRIPT}")
    if CRON_LOG.exists() and CRON_LOG.stat().st_size:
        print(f"Errors were logged to {CRON_LOG}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cron", nargs="?", const="status", choices=["on", "off", "status"],
                    help="turn the every-minute snapshot on/off; alone, print its status")
    ap.add_argument("--snapshot", action="store_true", help="record the sessions open now")
    ap.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what a restore would open")
    ap.add_argument("--force", action="store_true", help="reopen sessions launched in the last 2 minutes")
    ap.add_argument("--apply", metavar="BOOTS", help=argparse.SUPPRESS)  # used by the generated script
    args = ap.parse_args()
    if args.cron:
        return cron(None if args.cron == "status" else args.cron)
    if args.snapshot:
        return snapshot(args.quiet)
    if args.apply is not None:
        return apply(args.apply, args.dry_run, args.force)
    return run_restore(args.dry_run, args.force)


if __name__ == "__main__":
    sys.exit(main())
