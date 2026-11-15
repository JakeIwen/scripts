#!/usr/bin/python3
"""Mac-only SSH adapter for llmrestore; standalone stdlib Python 3.9+."""

import json
import shlex
import subprocess
import sys


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
    result = subprocess.run(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
                             " ".join([pi_restore, "--emit", *flags])], stdout=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"Pi restore failed (ssh {host} exited {result.returncode}); nothing opened", file=sys.stderr)
        return result.returncode
    sessions = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if sessions:
        print(open_tabs([pi_tab_command(s["command"], host) for s in sessions], transport) + f" (ssh {host})")
    return 0


def main(argv=None):
    action, host, pi_restore, transport, *flags = sys.argv[1:] if argv is None else argv
    if action != "restore":
        raise ValueError("unknown Pi action")
    return restore(host, pi_restore, transport, flags)


if __name__ == "__main__":
    raise SystemExit(main())
