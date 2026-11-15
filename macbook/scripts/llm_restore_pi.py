#!/usr/bin/python3
"""Mac-only SSH adapter for llmrestore; standalone stdlib Python 3.9+.

Saved sets include Pi agent and plain login-shell tabs belonging to this Mac's
SSH sockets. Boot restoration (--pi) keeps the Pi's existing selection policy.
"""

import ipaddress
import json
import shlex
import subprocess
import sys


# Sent on stdin in one read-only SSH request; no file is installed on the Pi.
PROBE = r'''
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def processes():
    found = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            try:
                raw = (path / "environ").read_bytes().decode(errors="replace")
            except OSError:
                raw = ""  # sshd's ancestry is useful even when its env is private
            env = dict(pair.split("=", 1) for pair in raw.split("\0") if "=" in pair)
            found[int(path.name)] = {
                "parent": int(stat[1]), "tty": int(stat[4]), "env": env,
                "comm": (path / "comm").read_text().strip(), "path": path}
        except (OSError, ValueError):
            continue
    return found


def endpoint(proc):
    env = proc["env"]
    fields = env.get("SSH_CONNECTION", "").split()
    return fields[:2] if len(fields) == 4 and "TMUX" not in env else None


def main(pi_restore):
    result = subprocess.run(pi_restore + " --list --json", shell=True,
                            capture_output=True, text=True, check=True, timeout=30)
    listed = json.loads(result.stdout)
    procs = processes()
    records = []
    for session in listed["sessions"]:
        proc = procs.get(session["pid"])
        client = endpoint(proc) if proc else None
        if client:
            if "command" not in session:
                raise ValueError("Pi llmrestore --list lacks command; update the Pi shared script before saving SSH tabs")
            records.append({"session": {k: session[k] for k in
                                       ("tool", "id", "cwd", "title", "command")}, "client": client})
    # Walk ancestors of every agent process, including unidentifiable/headless
    # ones: an occupied login shell must not become a duplicate plain-shell tab.
    occupied = set()
    for pid, proc in procs.items():
        if proc["comm"] not in ("claude", "codex"):
            continue
        seen = set()
        while pid in procs and pid not in seen:
            seen.add(pid)
            occupied.add(pid)
            pid = procs[pid]["parent"]
    for pid, proc in procs.items():
        parent = procs.get(proc["parent"], {})
        client = endpoint(proc)
        if (not client or not proc["tty"] or pid in occupied
                or not parent.get("comm", "").startswith("sshd")
                or proc["comm"] not in ("bash", "zsh", "sh", "dash", "ksh", "fish")):
            continue
        try:
            cwd = os.readlink(proc["path"] / "cwd")
        except OSError:
            continue
        records.append({"session": {"tool": "shell", "id": cwd, "cwd": cwd,
                                   "title": cwd, "command": "cd -- " + shlex.quote(cwd)}, "client": client})
    print(json.dumps({"records": records, "warnings": listed.get("warnings", [])}))


if __name__ == "__main__":
    main(sys.argv[1])
'''


def ssh_args(host):
    return ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]


def pi_tab_command(command, host):
    """Run a Pi command over SSH, leaving an interactive Pi shell afterwards."""
    remote = f"bash -lic {shlex.quote(command + '; exec bash -il')}"
    return f"ssh -t {shlex.quote(host)} {shlex.quote(remote)}"


def open_tabs(commands, transport):
    result = subprocess.run(["/usr/bin/python3", "-I", "-B", transport, *commands],
                            input="", capture_output=True, text=True, check=True)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return "Opened as tabs of a new iTerm window."


def restore(host, pi_restore, transport, flags):
    # stderr (the Pi's report) passes straight through to this terminal.
    result = subprocess.run([*ssh_args(host), " ".join([pi_restore, "--emit", *flags])],
                            stdout=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"Pi restore failed (ssh {host} exited {result.returncode}); nothing opened", file=sys.stderr)
        return result.returncode
    sessions = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if sessions:
        print(open_tabs([pi_tab_command(s["command"], host) for s in sessions], transport) + f" (ssh {host})")
    return 0


def normalized_endpoint(address, port):
    ip = ipaddress.ip_address(address.strip("[]"))
    return str(getattr(ip, "ipv4_mapped", None) or ip), int(port)


def mac_endpoints():
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-c", "ssh", "-iTCP",
                             "-sTCP:ESTABLISHED", "-Fn"], capture_output=True, text=True, timeout=15)
    if result.returncode not in (0, 1) or (result.returncode and result.stderr.strip()):
        raise OSError("could not list Mac SSH sockets: " + result.stderr.strip())
    endpoints = set()
    for line in result.stdout.splitlines():
        if line.startswith("n") and "->" in line:
            address, _, port = line[1:].split("->", 1)[0].rpartition(":")
            endpoints.add(normalized_endpoint(address, port))
    return endpoints


def probe(host, pi_restore):
    result = subprocess.run([*ssh_args(host), "python3 -I - " + shlex.quote(pi_restore)],
                            input=PROBE, capture_output=True, text=True, check=True, timeout=45)
    data = json.loads(result.stdout)
    endpoints = mac_endpoints()
    sessions = []
    for record in data["records"]:
        if normalized_endpoint(*record["client"]) in endpoints:
            session = record["session"]
            if session["tool"] not in ("claude", "codex", "shell"):
                raise ValueError("unknown Pi session tool")
            if any(not isinstance(session[k], str) for k in ("id", "cwd", "title", "command")):
                raise ValueError("invalid Pi session")
            sessions.append({**session, "host": host})
    return sessions, data.get("warnings", [])


def warning(host, error):
    detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
    # Saved-set warnings are shell comments, so remote stderr must stay one line.
    return f"Pi SSH tabs ({host}) unavailable: {' '.join(detail.split())}"


def save(host, pi_restore):
    try:
        sessions, warnings = probe(host, pi_restore)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        sessions, warnings = [], [warning(host, error)]
    print(json.dumps({"sessions": sessions, "warnings": warnings}))
    return 0


def apply_host(host, pi_restore, transport, flags, items):
    agents = [i for i in items if i["session"]["tool"] != "shell"]
    shells = [i["session"] for i in items if i["session"]["tool"] == "shell"]
    commands = []
    live, warnings = probe(host, pi_restore) if shells else ([], [])
    for message in warnings:
        print("warning: " + message, file=sys.stderr)
    if agents:
        # The remote chooser owns transcript checks, live detection and its
        # launch guard. Strip host so these are local records on the Pi.
        payload = [{**i, "session": {**i["session"], "host": None}} for i in agents]
        result = subprocess.run([*ssh_args(host), " ".join([pi_restore, "--apply - --emit", *flags])],
                                input="".join(json.dumps(i) + "\n" for i in payload),
                                capture_output=True, text=True, check=True, timeout=60)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        commands += [pi_tab_command(json.loads(line)["command"], host)
                     for line in result.stdout.splitlines() if line.strip()]
    if shells:
        open_cwds = {s["cwd"] for s in live if s["tool"] == "shell"}
        for shell in shells:
            if shell["cwd"] in open_cwds:
                print(f"skip  shell {host}:{shell['cwd']}: already open")
                continue
            open_cwds.add(shell["cwd"])
            # Do not trust an old shell record's command to land in its cwd.
            command = pi_tab_command("cd -- " + shlex.quote(shell["cwd"]), host)
            print(f"{'would open' if '--dry-run' in flags else 'open'}  shell {host}:{shell['cwd']}\n      {command}")
            commands.append(command)
    if commands and "--dry-run" not in flags:
        print(open_tabs(commands, transport) + f" (ssh {host})")


def apply_saved(pi_restore, transport, flags, items):
    # A saved host remains authoritative even if the current default changes.
    for host in dict.fromkeys(i["session"]["host"] for i in items):
        try:
            apply_host(host, pi_restore, transport, flags,
                       [i for i in items if i["session"]["host"] == host])
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print("warning: " + warning(host, error), file=sys.stderr)
    return 0


def main(argv=None):
    action, host, pi_restore, transport, *flags = sys.argv[1:] if argv is None else argv
    if action == "restore":
        return restore(host, pi_restore, transport, flags)
    if action == "save":
        return save(host, pi_restore)
    if action == "apply":
        return apply_saved(pi_restore, transport, flags, json.load(sys.stdin))
    raise ValueError("unknown Pi action")


if __name__ == "__main__":
    raise SystemExit(main())
